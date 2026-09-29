"""정해진 시점에 에이전트를 스스로 시작시키는 예약 작업."""
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.agent import core
from app.calc.timeutil import today_kst
from app.config import SCHEDULE_HOUR, TIMEZONE
from app.db import engine
from app.models import Job

scheduler = BackgroundScheduler(timezone=TIMEZONE)


def daily_check() -> dict:
    """매일: 월급날인 사업장은 지난달 급여 점검, 그만둔 사업장은 지급 기한 점검."""
    today = today_kst()
    prev = (today.replace(day=1).toordinal() - 1)
    from datetime import date
    last_month = date.fromordinal(prev).strftime("%Y-%m")
    done = {"payday": 0, "quit": 0}
    with Session(engine) as s:
        for job in s.exec(select(Job)).all():
            if job.status == "working" and job.payday == today.day:
                core.run_payday(s, job.user_id, job.id, last_month)
                done["payday"] += 1
            if job.status == "quit":
                core.run_quit_check(s, job.user_id, job.id)
                done["quit"] += 1
    return done


def start() -> None:
    if not scheduler.running:
        scheduler.add_job(daily_check, "cron", hour=SCHEDULE_HOUR, minute=0, id="daily_check", replace_existing=True)
        scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
