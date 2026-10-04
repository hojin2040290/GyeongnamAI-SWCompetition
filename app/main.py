"""FastAPI 진입점. 화면(HTML, CSS, JS)과 기능(API)을 함께 제공한다."""
import logging
import sqlite3
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import config, notices, scheduler
from app.auth import current_user
from app.config import BASE_DIR, SECRET_KEY
from app.db import engine, init_db
from app.law.fetch import load_notices
from app.long_task import LongTaskMiddleware
from sqlmodel import Session, select
from app.models import LawArticle, User
from app.routers.api import router as api_router
from app.beta import router as beta_router


def code_version() -> str:
    """지금 돌고 있는 코드의 git 커밋 (최신 코드를 받았는지 확인용)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%m월 %d일 %H:%M"],
                             cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "알 수 없음"
    except (OSError, subprocess.SubprocessError):
        return "알 수 없음"


VERSION = code_version()


def _has_users(db: Path) -> bool:
    try:
        with sqlite3.connect(db) as c:
            return c.execute("select count(*) from user").fetchone()[0] > 0
    except sqlite3.Error:  # 파일이 없거나 테이블이 아직 없음
        return False


def run_child(module: list[str]) -> subprocess.CompletedProcess:
    """계정 만들기 같은 하위 실행. 출력은 항상 UTF-8로 주고받는다
    (Windows 터미널의 기본 cp949는 '—' 같은 글자를 못 써서 계정을 만들다 멈춘 적이 있다)."""
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-m", *module], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600, env=env)


def prepare_test_data(log: logging.Logger) -> None:
    """시험 데이터(data/test)가 아직 없으면(계정이 없으면) 만든다. 이미 쓰던 시험 데이터는 건드리지 않는다.
    DB_PATH 등을 따로 정해 다른 곳을 쓰고 있으면 만들지 않는다."""
    if config.DB_PATH != (config.TEST_DATA_DIR / "app.db").resolve():
        return
    from app.demo_db import EMAILS, existing_emails
    have = existing_emails(config.DB_PATH) if _has_users(config.DB_PATH) else set()
    if all(e in have for e in EMAILS):
        return
    # 시험 데이터가 없으면 새로, 있으면 없는 시험 계정만 더한다 (쓰던 계정과 기록은 그대로)
    log.info("시험 계정이 없어 만들어요 (python -m app.demo_db와 같음)")
    out = run_child(["app.demo_db", "--dir", str(config.TEST_DATA_DIR), "--add" if have else "--force"])
    if out.returncode == 0:
        log.info("시험 데이터를 만들었어요. 로그인: test@example.com, test2~test5@example.com / test1234")
    else:
        log.error("시험 데이터를 만들지 못했어요: %s", (out.stderr or out.stdout)[-500:])


def refresh_laws_on_start(log: logging.Logger) -> None:
    """켤 때 법제처 API로 법령 현행 판을 확인해, 바뀐 법령(또는 아직 없는 법령)을 받아 법 기준표에 저장한다.
    법이 바뀌어도 예전 조문으로 판단하지 않게 하려는 것이다. 실패해도 서버는 켠다 (로그에 남김)."""
    if not config.LAW_REFRESH_ON_START:
        return
    if not config.LAW_OC:
        log.warning("법제처 키(LAW_OC)가 없어 법령 변경을 확인하지 못했어요. .env에 LAW_OC를 넣어 주세요")
    else:
        log.info("법제처에서 법령이 바뀌었는지 확인하는 중이에요")
        result = scheduler.refresh_law_table()
        if isinstance(result, str):
            log.warning("법령 변경 확인: %s", result)
        else:
            log.info("법령 변경 확인: %s", "바뀐 법령을 받아 저장했어요 (" + ", ".join(result) + ")" if result else "바뀐 법령 없음")
    with Session(engine) as s:
        n = len(s.exec(select(LawArticle.id)).all())
    log.info("법 기준표: 조문 %d개", n) if n else log.warning("법 기준표가 비어 있어요 (조문 0개)")


def prepare_beta_accounts(log: logging.Logger) -> None:
    """BETA_GUIDE=true면 상황별 베타 계정(beta1~9, owner1~3)이 모자랄 때 만든다 (있던 계정과 기록은 그대로)."""
    import os
    if not config.BETA_GUIDE or os.environ.get("BETA_BUILDING"):
        return
    from app.beta import missing_accounts
    todo = missing_accounts(config.DB_PATH)
    if not todo:
        log.info("베타 테스트 체험판으로 실행 중이에요 (BETA_GUIDE=true). 베타 계정은 이미 있어요")
        return
    log.info("베타 계정 %d개를 만들어요 (python -m app.beta와 같음)", len(todo))
    out = run_child(["app.beta"])
    if out.returncode == 0:
        log.info("베타 계정을 만들었어요: beta1~9@example.com, owner1~3@example.com / test1234")
    else:
        log.error("베타 계정을 만들지 못했어요: %s", (out.stderr or out.stdout)[-500:])


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("uvicorn.error")
    log.info("알바지킴이 코드 버전: %s", VERSION)
    log.info("저장 위치: 기록 %s, 증거 원본 %s, 상담 사전 자료 %s", config.DB_PATH, config.UPLOAD_DIR, config.REPORT_DIR)
    if config.TEST_DATA:
        log.info("시험 데이터로 실행 중이에요 (TEST_DATA=true). 평소 데이터는 쓰지 않아요")
        prepare_test_data(log)
    if config.LLM_FAKE:
        log.warning("가짜 AI(시험용)가 답해요. AI가 쓰는 글은 모두 '테스트 답변입니다 (...)'예요. "
                    "실제 모델을 쓰려면 .env에 LLM_ENABLED=true와 LLM_MODEL을 넣으세요")
    init_db()
    prepare_beta_accounts(log)
    refresh_laws_on_start(log)
    with Session(engine) as s:
        notices.tidy(s)  # 예전 알림 정리 (같은 종류는 최근 것만, 오래전에 읽은 알림은 지움)
        load_notices(s)  # 법제처에서 불러온 최저임금 고시 (판단 근거로 붙임)
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="알바지킴이", lifespan=lifespan)
# 오래 걸리는 AI 요청을 뒤에서 실행 (cloudflared 100초 대비). 로그인 사용자를 알아야 해서 SessionMiddleware 안쪽에 둔다
app.add_middleware(LongTaskMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")
app.include_router(api_router)
app.include_router(beta_router)


FIELD_NAMES = {"wage": "시급", "payday": "월급날", "probation_months": "수습 개월", "amount": "받은 금액",
               "month": "달", "start_date": "근무 시작일", "end_date": "계약 종료일", "quit_date": "그만둔 날",
               "birth_date": "생년월일", "schedule": "근무 시간", "email": "이메일", "password": "비밀번호"}


@app.exception_handler(RequestValidationError)
async def korean_validation_error(request: Request, exc: RequestValidationError):
    """숫자 칸에 글자를 넣는 등 형식이 틀리면 한국어로 알려 준다."""
    err = exc.errors()[0] if exc.errors() else {}
    field = next((str(x) for x in reversed(err.get("loc", [])) if isinstance(x, str) and x not in ("body", "query", "form")), "")
    name = FIELD_NAMES.get(field, "입력한 값")
    kind = err.get("type", "")
    if "int" in kind or "float" in kind or "number" in kind:
        msg = f"{name}에는 숫자만 적어 주세요"
    elif "date" in kind:
        msg = f"{name}은(는) 날짜 형식으로 골라 주세요"
    elif kind == "missing":
        msg = f"{name}을(를) 입력해 주세요"
    else:
        msg = f"{name} 형식이 맞지 않아요"
    return JSONResponse(status_code=422, content={"detail": msg})


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """예상 못 한 오류: 서버 기록에 자세히 남기고, 화면에는 한국어로 알린다."""
    code = uuid.uuid4().hex[:6]
    logging.getLogger("uvicorn.error").exception("처리 중 오류 [%s] %s %s", code, request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"처리 중 오류가 났어요. 잠시 뒤 다시 해 주세요 (오류 번호 {code})"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """파일 종류를 추측해 실행하지 않게(nosniff), 다른 사이트에 몰래 끼워 넣지 못하게(프레임 금지) 한다."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def static_version() -> str:
    """CSS, JS 파일이 바뀌면 달라지는 값. 주소에 붙여 브라우저가 예전 파일을 쓰지 않게 한다."""
    static = BASE_DIR / "web" / "static"
    return str(max(int((static / f).stat().st_mtime) for f in ("style.css", "app.js")))


@app.get("/api/version")
def version():
    return {"version": VERSION}


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"ver": static_version()})


@app.post("/api/dev/daily-check")
def dev_daily_check(u: User = Depends(current_user)):
    """시연용: 매일 정해진 시각에 도는 점검을 지금 실행한다.
    .env의 DEV_TOOLS=true일 때만 열리고, 로그인한 사용자 자신의 사업장만 점검한다."""
    if not config.DEV_TOOLS:
        raise HTTPException(404, "Not Found")
    return scheduler.daily_check(u.id)
