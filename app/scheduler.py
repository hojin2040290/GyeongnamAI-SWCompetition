"""정해진 시점에 에이전트를 스스로 시작시키는 예약 작업."""
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.agent import core
from app.calc.timeutil import today_kst
from app.config import SCHEDULE_HOUR, TIMEZONE
from app.db import engine
from app.models import Job

scheduler = BackgroundScheduler(timezone=TIMEZONE)


def daily_check() -> dict:
    """매일: 월급날인 사업장은 지난달 급여 점검, 그만둔 사업장은 지급 기한 점검,
    신고한 사업장은 공개 게시물 검색 (검색 키가 없으면 건너뛰고 기록만 남김)."""
    today = today_kst()
    last_month = date.fromordinal(today.replace(day=1).toordinal() - 1).strftime("%Y-%m")
    done = {"payday": 0, "quit": 0, "guard": 0}
    with Session(engine) as s:
        for job in s.exec(select(Job)).all():
            if job.status == "working" and job.payday == today.day:
                core.run_payday(s, job.user_id, job.id, last_month, trigger="schedule")
                done["payday"] += 1
            if job.status == "quit":
                core.run_quit_check(s, job.user_id, job.id, trigger="schedule")
                done["quit"] += 1
            if job.reported:
                core.run_guard_search(s, job.user_id, job.id, trigger="schedule")
                done["guard"] += 1
    return done


def start() -> None:
    if not scheduler.running:
        scheduler.add_job(daily_check, "cron", hour=SCHEDULE_HOUR, minute=0, id="daily_check", replace_existing=True)
        scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
