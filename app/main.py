"""FastAPI 진입점. 화면(HTML, CSS, JS)과 기능(API)을 함께 제공한다."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
