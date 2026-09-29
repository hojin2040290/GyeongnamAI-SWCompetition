"""에이전트가 쓰는 도구.

사용자 번호(user_id)와 사업장 번호(job_id)는 코드가 미리 고정한다.
AI(나중에 연결)는 이 값을 입력하지 않으므로 다른 사용자의 기록에 접근할 수 없다.
"""
import json
from datetime import date, timedelta

from sqlmodel import Session, select

from app.calc import pay as paycalc
from app.calc import schedule as sch
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.law.lookup import attach_articles
from app.models import CheckRun, ContractFields, GuardPost, Job, Notification, Payslip, User, WorkRecord

NOTIFY_DEDUP_HOURS = 24  # 같은 알림을 다시 보내지 않는 시간


def facts_from_job(user: User, job: Job, fields: dict | None, on: date | None = None) -> engine.Facts:
    return engine.Facts(
        birth=user.birth_date, on=on or today_kst(), wage=job.wage, probation=job.probation,
        probation_months=job.probation_months, start_date=job.start_date, end_date=job.end_date,
        no_end=job.no_end, schedule=sch.parse(job.schedule_json), size=job.size, industry=job.industry,
        work_desc=job.work_desc, contract_written=job.contract_written, copy_received=job.copy_received,
        consent=job.consent, contract_fields=fields or {},
    )


def keywords_of(job: Job) -> list[str]:
    return [k.strip() for k in (job.guard_keywords or "").split(",") if k.strip()]


def make_tools(session: Session, user_id: int, job_id: int | None):
    def get_user() -> User:
        return session.get(User, user_id)

    def get_job() -> Job:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise PermissionError("다른 사용자의 사업장이에요")
        return job

    def get_contract_fields() -> dict:
        row = session.exec(select(ContractFields).where(ContractFields.job_id == job_id)
                           .order_by(ContractFields.id.desc())).first()
        return json.loads(row.fields_json) if row else {}

    def get_records() -> list[WorkRecord]:
        return list(session.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.user_id == user_id)
                                 .order_by(WorkRecord.clock_in)))

    def get_record(record_id: int) -> WorkRecord:
        r = session.get(WorkRecord, record_id)
        if not r or r.user_id != user_id or r.job_id != job_id:
            raise PermissionError("다른 사용자의 근무 기록이에요")
        return r

    # ----- 판단 -----
    def judge_job() -> list[dict]:
        """입력한 기본 정보와 계약서 내용을 법 기준과 대조."""
        user, job = get_user(), get_job()
        items = engine.judge(facts_from_job(user, job, get_contract_fields()), "contract")
        return engine.to_json(items)

    def judge_records() -> list[dict]:
        """실제 출퇴근 기록 전체를 법 기준과 대조."""
        user, job = get_user(), get_job()
        return engine.to_json(engine.judge_records(user.birth_date, get_records(), sch.parse(job.schedule_json)))

    def judge_shift(day: str) -> list[dict]:
        """퇴근한 날과 그 주의 기록만 대조."""
        user, job = get_user(), get_job()
        items = engine.judge_records(user.birth_date, get_records(), sch.parse(job.schedule_json),
                                     focus=date.fromisoformat(day))
        return engine.to_json(items)

    def judge_seek(data: dict) -> dict:
        """지원 전: 공고 조건을 법 기준과 대조하고 물어볼 질문을 만든다."""
        facts = engine.Facts(birth=get_user().birth_date, on=today_kst(), wage=data.get("wage"),
                             probation=data.get("probation") or "unknown", schedule=data.get("schedule") or {},
                             industry=data.get("industry", ""), work_desc=data.get("work_desc", ""))
        items = engine.judge(facts, "seek")
        return {"items": engine.to_json(items), "questions": engine.questions(facts, items)}

    def attach_law(items: list[dict]) -> list[dict]:
        """판단 결과마다 법 기준표의 조문 원문을 붙인다. 없으면 '법 기준표 미구축'."""
        return attach_articles(session, items)

    # ----- 급여, 퇴직 -----
    def calc_pay(month: str) -> dict:
        user, job = get_user(), get_job()
        if not job.wage:
            return {"error": "시급이 등록되지 않아 계산할 수 없어요"}
        r = paycalc.calc_month(get_records(), sch.parse(job.schedule_json), job.wage, job.size, user.birth_date, month)
        return r.as_dict()

    def get_payslip(month: str):
        row = session.exec(select(Payslip).where(Payslip.job_id == job_id, Payslip.month == month)
                           .order_by(Payslip.id.desc())).first()
        return row.amount if row else None

    def compare_pay(expected: dict, paid) -> dict:
        if "error" in expected:
            return {"status": "warn", "text": expected["error"]}
        if paid is None:
            return {"status": "warn", "text": "명세서나 받은 금액이 아직 없어요. 올리면 비교해 드려요."}
        diff = expected["total"] - paid
        tol = max(100, round(expected["total"] * 0.01))
        if diff > tol:
            return {"status": "bad", "diff": diff,
                    "text": f"계산한 금액보다 {diff:,}원 적게 받았어요. 공제 항목이 있다면 명세서로 확인해 보세요."}
        return {"status": "ok", "diff": diff, "text": "계산한 금액과 받은 금액이 거의 같아요."}

    def settlement() -> dict | None:
        job = get_job()
        if job.status != "quit" or not job.quit_date:
            return None
        return paycalc.settlement_status(job.quit_date, today_kst(), job.paid_after_quit)

    # ----- 기록, 알림 -----
    def save_check(kind: str, results) -> int:
        run = CheckRun(user_id=user_id, job_id=job_id, kind=kind, results_json=json.dumps(results, ensure_ascii=False),
                       created_at=now_kst())
        session.add(run)
        session.commit()
        return run.id

    def notify(title: str, body: str) -> str:
        """알림 보내기. 같은 알림이 하루 안에 이미 있으면 다시 보내지 않는다 (점검을 여러 번 눌러도 한 번만)."""
        since = now_kst() - timedelta(hours=NOTIFY_DEDUP_HOURS)
        dup = session.exec(select(Notification).where(
            Notification.user_id == user_id, Notification.job_id == job_id, Notification.title == title,
            Notification.body == body, Notification.created_at >= since)).first()
        if dup:
            return "같은 알림이 이미 있어 보내지 않음"
        session.add(Notification(user_id=user_id, job_id=job_id, title=title, body=body, created_at=now_kst()))
        session.commit()
        return "보냄"

    # ----- 상담 -----
    def counsel_for_age() -> list[dict]:
        from app.calc.age import age_on
        age = age_on(get_user().birth_date, today_kst())
        return [c for c in P()["counsel"] if c["min_age"] <= age <= c["max_age"]]

    def build_report() -> dict:
        """상담 사전 자료 문서 만들기 (AI 연결 후에는 사건 요약을 AI가 작성)."""
        from app import report  # report가 이 모듈을 쓰므로 여기서 불러온다
        rep = report.build(session, user_id, job_id)
        return {"id": rep.id, "url": f"/api/reports/{rep.id}"}

    # ----- 신고 후 보호 -----
    def set_reported(on: bool) -> bool:
        job = get_job()
        job.reported = on
        session.add(job)
        session.commit()
        return on

    def warning_message() -> str:
        from app import guard
        return guard.current_message(get_job())

    def search_posts() -> dict:
        from app import guard
        job = get_job()
        return guard.search_public_posts(session, job, keywords_of(job))

    def preserve_post(url: str, title: str = "") -> dict:
        from app import guard
        return guard.preserve(session, user_id, get_job(), url, title)

    def classify_posts() -> dict:
        """판별 대기 게시물 판별. AI 연결 전에는 판별하지 않고 대기로 둔다."""
        from app.llm import client
        pending = session.exec(select(GuardPost).where(GuardPost.job_id == job_id, GuardPost.status == "pending")).all()
        if not client.available():
            return {"judged": 0, "pending": len(pending), "reason": "AI 연결 전이라 판별하지 않고 증거만 보존했어요"}
        return {"judged": 0, "pending": len(pending), "reason": "AI 판별 연결 예정"}

    return {
        "get_user": get_user, "get_job": get_job, "get_contract_fields": get_contract_fields,
        "get_records": get_records, "get_record": get_record, "judge_job": judge_job, "judge_records": judge_records,
        "judge_shift": judge_shift, "judge_seek": judge_seek, "attach_law": attach_law, "calc_pay": calc_pay,
        "get_payslip": get_payslip, "compare_pay": compare_pay, "settlement": settlement, "save_check": save_check,
        "notify": notify, "counsel_for_age": counsel_for_age, "build_report": build_report,
        "set_reported": set_reported, "warning_message": warning_message, "search_posts": search_posts,
        "preserve_post": preserve_post, "classify_posts": classify_posts,
    }
