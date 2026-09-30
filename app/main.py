"""FastAPI 진입점. 화면(HTML, CSS, JS)과 기능(API)을 함께 제공한다."""
import logging
import subprocess
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import config, scheduler
from app.auth import current_user
from app.config import BASE_DIR, SECRET_KEY
from app.db import engine, init_db
from app.law.fetch import load_notices
from sqlmodel import Session
from app.models import User
from app.routers.api import router as api_router


def code_version() -> str:
    """지금 돌고 있는 코드의 git 커밋 (최신 코드를 받았는지 확인용)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%m월 %d일 %H:%M"],
                             cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "알 수 없음"
    except (OSError, subprocess.SubprocessError):
        return "알 수 없음"


VERSION = code_version()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("uvicorn.error")
    log.info("알바지킴이 코드 버전: %s", VERSION)
    log.info("저장 위치: 기록 %s, 증거 원본 %s, 상담 사전 자료 %s", config.DB_PATH, config.UPLOAD_DIR, config.REPORT_DIR)
    if config.LLM_FAKE:
        log.warning("가짜 AI(시험용)가 답해요. AI가 쓰는 글은 모두 '테스트 답변입니다 (...)'예요. "
                    "실제 모델을 쓰려면 .env에 LLM_ENABLED=true와 LLM_MODEL을 넣으세요")
    init_db()
    with Session(engine) as s:
        load_notices(s)  # 법제처에서 불러온 최저임금 고시 (판단 근거로 붙임)
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="알바지킴이", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")
app.include_router(api_router)


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
