"""AI 에이전트 진행 상황과 에이전트 메모.

진행 상황은 저장된 기록으로 코드가 정리하고(판단 없음), 메모와 조언은 에이전트가 남긴다.
문제가 생기기 전에 미리 기록을 모으는 사용자도 있으므로 '사건'이라는 말은 쓰지 않는다.
"""
import json

from sqlmodel import Session, select

from app.models import AgentQuestion, AgentTask, CaseNote, CheckRun, Evidence, GuardPost, Job, Report, WorkRecord

MEMORY_LIMIT = 8  # 다음 실행에 넘기는 메모 수
NEXT_TABS = {"check": "계약서 점검", "pay": "급여 점검", "docs": "상담 사전 자료", "guard": "신고 후 보호"}


def _count(rows: list[dict], status: str) -> int:
    return sum(r.get("status") == status for r in rows)


def progress(s: Session, job: Job) -> list[dict]:
    """단계마다 한 일이 있는지와 숫자 (점검, 기록, 급여, 상담 자료, 신고, 보호)."""
    check = s.exec(select(CheckRun).where(CheckRun.job_id == job.id, CheckRun.kind == "contract")
                   .order_by(CheckRun.id.desc())).first()
    items = json.loads(check.results_json) if check else []
    days = len({r.clock_in.date() for r in s.exec(select(WorkRecord).where(
        WorkRecord.job_id == job.id, WorkRecord.void_at == None)).all()})  # noqa: E711
    evs = len(s.exec(select(Evidence).where(Evidence.job_id == job.id)).all())
    pays = [json.loads(p.results_json) for p in s.exec(select(CheckRun).where(CheckRun.job_id == job.id,
                                                                           CheckRun.kind == "payday")).all()]
    short = len({p["month"] for p in pays if p["compare"].get("short")})
    reports = s.exec(select(Report).where(Report.job_id == job.id)).all()
    posts = s.exec(select(GuardPost).where(GuardPost.job_id == job.id)).all()
    return [
        {"key": "check", "name": "점검", "done": bool(check),
         "detail": (f"위반 의심 {_count(items, 'bad')}, 확인 필요 {_count(items, 'warn')}, 확인 중 {_count(items, 'pending')}"
                    if check else "아직 안 함")},
        {"key": "record", "name": "기록", "done": bool(days or evs), "detail": f"근무 {days}일, 증거 자료 {evs}개"},
        {"key": "pay", "name": "급여", "done": bool(pays),
         "detail": f"점검한 달 {len({p['month'] for p in pays})}, 적게 받은 달 {short}" if pays else "아직 안 함"},
        {"key": "report", "name": "상담 자료", "done": bool(reports), "detail": f"{len(reports)}개" if reports else "아직 안 만듦"},
        {"key": "reported", "name": "신고", "done": job.reported, "detail": "신고했어요" if job.reported else "아직 안 함"},
        {"key": "guard", "name": "보호", "done": job.reported and bool(posts),
         "detail": f"확인한 게시물 {len(posts)}, 보복 의심 {sum(p.status == 'suspect' for p in posts)}"
         if job.reported else "신고 뒤 시작"},
    ]


def memories(s: Session, job_id: int, limit: int = MEMORY_LIMIT) -> list[dict]:
    rows = s.exec(select(CaseNote).where(CaseNote.job_id == job_id, CaseNote.kind == "memory")
                  .order_by(CaseNote.id.desc()).limit(limit)).all()
    return [{"날짜": r.created_at.strftime("%Y-%m-%d %H:%M"), "사건": r.event, "기억": r.text} for r in reversed(rows)]


def latest_advice(s: Session, job_id: int) -> CaseNote | None:
    return s.exec(select(CaseNote).where(CaseNote.job_id == job_id, CaseNote.kind == "advice")
                  .order_by(CaseNote.id.desc())).first()


def questions(s: Session, job_id: int, limit: int = 10) -> list[dict]:
    """에이전트가 물어본 질문과 답 (답을 기다리는 질문은 다시 묻지 않게 함께 넘긴다)."""
    rows = s.exec(select(AgentQuestion).where(AgentQuestion.job_id == job_id, AgentQuestion.status != "closed")
                  .order_by(AgentQuestion.id.desc()).limit(limit)).all()
    return [{"answer_id": q.id, "질문": q.question, "관련 조항": q.law,
             "답": q.answer if q.status == "answered" else "답 기다리는 중"} for q in reversed(rows)]


FOLLOWUP_KINDS = {"contract_check": "계약서 점검", "payday": "급여 점검", "quit_check": "퇴직 정산 확인",
                  "guard_review": "게시물 판별", "report": "상담 사전 자료"}


def followups(s: Session, job_id: int) -> list[dict]:
    """예약해 둔 후속 확인 (다시 예약하지 않게 에이전트에게도 넘긴다)."""
    rows = s.exec(select(AgentTask).where(AgentTask.job_id == job_id, AgentTask.status == "pending")
                  .order_by(AgentTask.due_at)).all()
    return [{"task_id": t.id, "점검": FOLLOWUP_KINDS.get(t.kind, t.kind), "kind": t.kind, "달": t.month,
             "때": t.due_at.strftime("%Y-%m-%d %H:%M"), "이유": t.note} for t in rows]
