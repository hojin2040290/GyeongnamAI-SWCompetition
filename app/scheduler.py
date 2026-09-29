"""정해진 시점에 에이전트를 스스로 시작시키는 예약 작업."""
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.agent import core
from app.calc.timeutil import today_kst
from app.config import LAW_OC, OPEN_RECORD_ALERT_HOURS, SCHEDULE_HOUR, TIMEZONE
from app.db import engine
from app.models import Job, WorkRecord

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
    done["law_changed"] = refresh_law_table()
    return done


def refresh_law_table() -> list[str] | str:
    """법령 현행 판이 바뀌었으면 법 기준표를 새로 만든다 (법제처 키가 없으면 건너뜀)."""
    if not LAW_OC:
        return "법제처 키가 없어 건너뜀"
    import httpx
    from app.law import fetch
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client, Session(engine) as s:
            changed = fetch.refresh_if_changed(s, client)
            if changed:
                fetch.fetch_min_wage_notices(s, client)
            return changed
    except Exception as exc:  # 법제처 오류로 다른 자동 점검이 멈추지 않게
        return f"법제처 확인 실패: {type(exc).__name__}"


def open_record_check() -> dict:
    """30분마다: 퇴근을 누르지 않은 채 오래된 출근 기록이 있으면 알림 (같은 알림은 하루 한 번)."""
    done = 0
    with Session(engine) as s:
        rows = s.exec(select(WorkRecord).where(WorkRecord.clock_out == None, WorkRecord.void_at == None)).all()  # noqa: E711
        for job_id, user_id in {(r.job_id, r.user_id) for r in rows}:
            if core.run_open_check(s, user_id, job_id, OPEN_RECORD_ALERT_HOURS)["open"]:
                done += 1
    return {"open": done}


def start() -> None:
    if not scheduler.running:
        scheduler.add_job(daily_check, "cron", hour=SCHEDULE_HOUR, minute=0, id="daily_check", replace_existing=True)
        scheduler.add_job(open_record_check, "interval", minutes=30, id="open_record_check", replace_existing=True)
        scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
