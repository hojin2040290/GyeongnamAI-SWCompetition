"""FastAPI 진입점. 화면(HTML, CSS, JS)과 기능(API)을 함께 제공한다."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import scheduler
from app.config import BASE_DIR, SECRET_KEY
from app.db import init_db
from app.routers.api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="알바지킴이", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "web" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")
app.include_router(api_router)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/dev/daily-check")
def dev_daily_check():
    """시연용: 매일 정해진 시각에 도는 점검을 지금 실행."""
    return scheduler.daily_check()
