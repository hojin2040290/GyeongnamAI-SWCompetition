"""정해진 시점에 에이전트를 스스로 시작시키는 예약 작업."""
import json
import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from app import notices
from app.agent import core, legacy
from app.agent.queue import QUEUE
from app.calc.timeutil import now_kst
from app.config import AI_RETRY_MIN, AI_RETRY_PER_DAY, LAW_OC, OPEN_RECORD_ALERT_HOURS, SCHEDULE_HOUR, TIMEZONE
from app.db import engine
from app.llm import client as llm_client
from app.models import AgentLog, AgentTask, CheckRun, GuardPost, Job, Report, WorkRecord

log = logging.getLogger("uvicorn.error")  # 서버 로그에 보이게
scheduler = BackgroundScheduler(timezone=TIMEZONE)


def daily_check(user_id: int | None = None) -> dict:
    """매일: 사업장마다 에이전트가 오늘 필요한 점검(급여, 퇴직 지급 기한, 공개 게시물)을 골라 실행하고,
    끝에 기록을 종합해 판단하고 조언하고(홈의 종합 점검), 사업장마다 오늘 결과를 알림으로 보낸다.
    AI 응답이 없으면 정해 둔 조건(월급날, 그만둠, 신고함)으로 점검만 실행한다. DEV_TOOLS와 상관없이 매일 SCHEDULE_HOUR시에 돈다.
    user_id가 있으면 그 사용자의 사업장만 점검하고 법 기준표 갱신은 하지 않는다 (시연용 '지금 실행')."""
    done = {"payday": 0, "quit": 0, "guard": 0, "advice": 0}
    q = select(Job) if user_id is None else select(Job).where(Job.user_id == user_id)
    with Session(engine) as s:
        for job in s.exec(q).all():
            ran = core.run_daily(s, job.user_id, job.id)["ran"]
            for kind in ran:
                done[kind] += 1
            # 종합 점검(홈): 기록을 모두 종합해 판단하고 조언한다. 지난 점검 뒤로 바뀐 기록이 없으면 AI를 부르지 않는다
            advised = core.run_overview(s, job.user_id, job.id, "schedule")["advised"] if core.overview_needed(s, job.id) else False
            done["advice"] += advised
            core.daily_notice(s, job.user_id, job.id, ran, advised)  # 점검할 게 없던 날도 결과를 알린다
            done["notified"] = done.get("notified", 0) + 1
    if user_id is None:
        done["law_changed"] = refresh_law_table()
        with Session(engine) as s:
            done["notices_removed"] = notices.tidy(s)["removed"]
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


RETRY_LABEL = "AI 응답 대기 중이던 일 다시 맡김"


def _retries_today(s: Session, job_id: int, event: str) -> int:
    since = now_kst() - timedelta(days=1)
    return len(s.exec(select(AgentLog).where(AgentLog.job_id == job_id, AgentLog.event == event, AgentLog.step == "시작",
                                             AgentLog.detail == RETRY_LABEL, AgentLog.created_at >= since)).all())


def _latest(s: Session, job_id: int, kind: str) -> list[CheckRun]:
    return list(s.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == kind)
                       .order_by(CheckRun.id.desc())))


def waiting_work(s: Session, job: Job) -> list[tuple[str, str]]:
    """이 사업장에서 AI 응답 대기 중인 일: (할 일 종류, 달)."""
    work: list[tuple[str, str]] = []
    check = next(iter(_latest(s, job.id, "contract")), None)
    if check and any(it["status"] == "pending" for it in json.loads(check.results_json)):
        work.append(("contract_check", ""))
    seen: set[str] = set()
    for p in _latest(s, job.id, "payday"):  # 달마다 가장 최근 비교만 본다
        d = json.loads(p.results_json)
        if d["month"] not in seen:
            seen.add(d["month"])
            if d["compare"]["status"] == "pending":
                work.append(("payday", d["month"]))
    quit_run = next(iter(_latest(s, job.id, "quit")), None)
    if job.status == "quit" and quit_run and json.loads(quit_run.results_json)["status"] == "pending":
        work.append(("quit_check", ""))
    pending_post = s.exec(select(GuardPost).where(GuardPost.job_id == job.id, GuardPost.status == "pending")).first()
    if pending_post or (job.reported and not job.guard_ai_message):
        work.append(("guard_review", ""))
    rep = s.exec(select(Report).where(Report.job_id == job.id).order_by(Report.id.desc())).first()
    if rep and not rep.ai_summary:
        work.append(("report", ""))
    view = next(iter(_latest(s, job.id, "overview")), None)
    if view and json.loads(view.results_json).get("status") == "pending":
        work.append(("overview", ""))
    return work


def retry_waiting() -> dict:
    """AI가 연결돼 있으면 'AI 응답 대기 중'인 일을 다시 맡긴다 (같은 일은 하루 AI_RETRY_PER_DAY번까지)."""
    if not llm_client.available():
        return {"skipped": "AI 모델이 연결되지 않았어요"}
    runs = {"contract_check": lambda s, j, m: core.run_contract_check(s, j.user_id, j.id, trigger="retry"),
            "payday": lambda s, j, m: core.run_payday(s, j.user_id, j.id, m, trigger="retry"),
            "quit_check": lambda s, j, m: core.run_quit_check(s, j.user_id, j.id, trigger="retry"),
            "guard_review": lambda s, j, m: core.run_guard_review(s, j.user_id, j.id, trigger="retry"),
            "report": lambda s, j, m: core.run_report(s, j.user_id, j.id, trigger="retry"),
            "overview": lambda s, j, m: core.run_overview(s, j.user_id, j.id, trigger="retry")}
    done: list[str] = []
    with Session(engine) as s:
        try:  # 예전 AI 글 속 서버 내부 이름(warn, quit_check 등)을 LLM이 한국어로 고쳐 쓰게 한다
            if fixed := QUEUE.run(None, "예전 AI 글 고쳐 쓰기", legacy.rename_internal, s):  # LLM을 부르므로 대기줄에 선다
                done.append(f"예전 AI 글 {fixed}칸 고쳐 씀")
        except Exception:  # noqa: BLE001 (정리가 실패해도 다시 맡기기는 계속)
            log.exception("예전 AI 글 고쳐 쓰기 중 오류")
            s.rollback()
        for job in s.exec(select(Job)).all():
            for event, month in waiting_work(s, job):
                if _retries_today(s, job.id, event) < AI_RETRY_PER_DAY:
                    try:  # 한 사업장의 오류가 다른 사업장의 다시 맡기기를 막지 않게
                        runs[event](s, job, month)
                        done.append(f"{job.id}:{event}{':' + month if month else ''}")
                    except Exception:
                        s.rollback()
                        log.exception("다시 맡기기 실패: 사업장 %s, %s", job.id, event)
    return {"retried": done}


def run_due_followups() -> dict:
    """에이전트가 예약한 확인 중 때가 된 것을 실행한다 (AI가 없으면 정해 둔 순서로, 판단은 대기)."""
    done: list[int] = []
    with Session(engine) as s:
        due = s.exec(select(AgentTask).where(AgentTask.status == "pending", AgentTask.due_at <= now_kst())
                     .order_by(AgentTask.due_at)).all()
        for task in due:
            task.status, task.done_at = "done", now_kst()  # 실행 중 오류가 나도 같은 확인을 되풀이하지 않게 먼저 표시
            s.add(task)
            s.commit()
            core.run_followup(s, task)
            done.append(task.id)
    return {"followups": done}


def start() -> None:
    if not scheduler.running:
        # misfire_grace_time: 컴퓨터가 잠자기 등으로 늦게 깨어나도 이 시간(초) 안이면 건너뛰지 않고 실행한다
        scheduler.add_job(daily_check, "cron", hour=SCHEDULE_HOUR, minute=0, id="daily_check", replace_existing=True,
                          misfire_grace_time=12 * 3600)
        scheduler.add_job(open_record_check, "interval", minutes=30, id="open_record_check", replace_existing=True,
                          misfire_grace_time=30 * 60)
        # 서버를 켜고 곧바로 한 번: AI가 없던 때 저장된 'AI 응답 대기 중' 결과를 첫 주기(AI_RETRY_MIN분)까지 두지 않는다
        scheduler.add_job(retry_waiting, "interval", minutes=AI_RETRY_MIN, id="retry_waiting", replace_existing=True,
                          misfire_grace_time=AI_RETRY_MIN * 60, next_run_time=now_kst() + timedelta(seconds=20))
        scheduler.add_job(run_due_followups, "interval", minutes=AI_RETRY_MIN, id="run_due_followups",
                          replace_existing=True, misfire_grace_time=AI_RETRY_MIN * 60)
        scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
