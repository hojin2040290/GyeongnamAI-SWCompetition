"""정해진 시점에 에이전트를 스스로 시작시키는 예약 작업."""
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app.agent import core
from app.config import LAW_OC, OPEN_RECORD_ALERT_HOURS, SCHEDULE_HOUR, TIMEZONE
from app.db import engine
from app.models import Job, WorkRecord

scheduler = BackgroundScheduler(timezone=TIMEZONE)


def daily_check() -> dict:
    """매일: 사업장마다 에이전트가 오늘 필요한 점검(급여, 퇴직 지급 기한, 공개 게시물)을 골라 실행한다.
    AI 응답이 없으면 정해 둔 조건(월급날, 그만둠, 신고함)으로 실행한다."""
    done = {"payday": 0, "quit": 0, "guard": 0}
    with Session(engine) as s:
        for job in s.exec(select(Job)).all():
            for kind in core.run_daily(s, job.user_id, job.id)["ran"]:
                done[kind] += 1
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
