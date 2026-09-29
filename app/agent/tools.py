"""에이전트가 쓰는 도구.

사용자 번호(user_id)와 사업장 번호(job_id)는 코드가 미리 고정한다.
AI(나중에 연결)는 이 값을 입력하지 않으므로 다른 사용자의 기록에 접근할 수 없다.
"""
import json
from datetime import date

from sqlmodel import Session, select

from app.calc import pay as paycalc
from app.calc import schedule as sch
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.models import CheckRun, ContractFields, Job, Notification, Payslip, User, WorkRecord


def facts_from_job(user: User, job: Job, fields: dict | None, on: date | None = None) -> engine.Facts:
    return engine.Facts(
        birth=user.birth_date, on=on or today_kst(), wage=job.wage, probation=job.probation,
        probation_months=job.probation_months, start_date=job.start_date, end_date=job.end_date,
        no_end=job.no_end, schedule=sch.parse(job.schedule_json), size=job.size, industry=job.industry,
        work_desc=job.work_desc, contract_written=job.contract_written, copy_received=job.copy_received,
        consent=job.consent, contract_fields=fields or {},
    )


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

    def judge_job() -> list[dict]:
        user, job = get_user(), get_job()
        items = engine.judge(facts_from_job(user, job, get_contract_fields()), "contract")
        return engine.to_json(items)

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

    def save_check(kind: str, results) -> int:
        run = CheckRun(user_id=user_id, job_id=job_id, kind=kind, results_json=json.dumps(results, ensure_ascii=False),
                       created_at=now_kst())
        session.add(run)
        session.commit()
        return run.id

    def notify(title: str, body: str) -> None:
        session.add(Notification(user_id=user_id, job_id=job_id, title=title, body=body, created_at=now_kst()))
        session.commit()

    def counsel_for_age() -> list[dict]:
        from app.calc.age import age_on
        age = age_on(get_user().birth_date, today_kst())
        return [c for c in P()["counsel"] if c["min_age"] <= age <= c["max_age"]]

    return {
        "get_user": get_user, "get_job": get_job, "get_contract_fields": get_contract_fields,
        "get_records": get_records, "judge_job": judge_job, "calc_pay": calc_pay, "get_payslip": get_payslip,
        "compare_pay": compare_pay, "settlement": settlement, "save_check": save_check, "notify": notify,
        "counsel_for_age": counsel_for_age,
    }
