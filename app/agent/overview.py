"""홈의 'AI 에이전트 종합 점검': 사업장 하나의 모든 기록을 코드가 한눈에 정리한다 (숫자와 결과는 코드의 일).

정리한 사실과 그중 가장 나쁜 결과(rule_status)를 AI에게 넘기고, AI는 도구로 자세히 확인한 뒤 종합 판단과 조언을 남긴다
(app/agent/core.py의 run_overview). 저장은 CheckRun(kind="overview").
"""
import json

from sqlmodel import Session, select

from app.agent import case
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.models import AgentQuestion, CheckRun, Evidence, GuardPost, Job, WorkRecord

RANK = {engine.OK: 0, engine.WARN: 1, engine.PENDING: 1, engine.BAD: 2}
NAME = {engine.OK: "정상", engine.WARN: "확인 필요", engine.BAD: "위반 의심", engine.PENDING: "AI 에이전트 판단 대기"}


def _latest(s: Session, job_id: int, kind: str) -> list[CheckRun]:
    return s.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == kind).order_by(CheckRun.id.desc())).all()


def _part(name: str, status: str | None, text: str, tab: str = "", todo: bool = False) -> dict:
    """정리한 사실 한 줄. status가 None이면 결과가 없는 정보(기록 수 등). todo: 아직 하지 않은 점검 (화면에 회색)."""
    return {"name": name, "status": status, "text": text, "tab": tab, "todo": todo}


def _contract(s: Session, job_id: int) -> dict:
    row = next(iter(_latest(s, job_id, "contract")), None)
    if not row:
        return _part("계약서 점검", None, "아직 점검하지 않았어요", "check", todo=True)
    items = json.loads(row.results_json)
    counts = {k: sum(it.get("status") == k for it in items) for k in (engine.BAD, engine.WARN, engine.PENDING, engine.OK)}
    # AI 판단 전 항목은 코드 규칙의 결과(rule_status)로 센다 (가장 나쁜 결과를 놓치지 않게)
    seen = [it.get("rule_status") or engine.WARN if it.get("status") == engine.PENDING else it.get("status", engine.WARN)
            for it in items]
    worst = max(seen, key=lambda x: RANK.get(x, 1), default=engine.OK)
    text = ", ".join(f"{NAME[k]} {n}건" for k, n in counts.items() if n) or "점검할 항목이 없어요"
    return _part("계약서 점검", worst, text, "check")


def _pays(s: Session, job_id: int) -> list[dict]:
    """달마다 가장 최근 급여 비교만 (최근 3달)."""
    out, seen = [], set()
    for row in _latest(s, job_id, "payday"):
        d = json.loads(row.results_json)
        if d.get("month") in seen:
            continue
        seen.add(d.get("month"))
        cmp = d.get("compare") or {}
        out.append(_part(f"{d.get('month', '')} 급여", cmp.get("status", engine.WARN), str(cmp.get("text", ""))[:160], "pay"))
        if len(out) == 3:
            break
    return out or [_part("급여 비교", None, "아직 비교한 달이 없어요", "pay", todo=True)]


def _records(s: Session, job_id: int) -> list[dict]:
    rows = s.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.void_at == None)).all()  # noqa: E711
    month = today_kst().strftime("%Y-%m")
    this = sum(1 for r in rows if r.clock_in.strftime("%Y-%m") == month)
    out = [_part("근무 기록", None, f"모두 {len(rows)}건, 이번 달 {this}건")]
    open_rows = [r for r in rows if r.clock_out is None]
    if open_rows:
        out.append(_part("퇴근 안 누른 기록", engine.WARN, f"{len(open_rows)}건 (퇴근을 누르지 않으면 급여 계산에서 빠져요)"))
    return out


def _quit(s: Session, user_id: int, job: Job) -> list[dict]:
    if job.status != "quit":
        return []
    from app.agent.tools import make_tools, saved_settlement  # tools가 이 모듈을 쓰므로 여기서 불러온다
    st = make_tools(s, user_id, job.id)["settlement"]()
    if not st:
        return []
    st = saved_settlement(s, job.id, st)
    return [_part("퇴직 후 임금 정산", st.get("status") if st.get("status") != engine.PENDING else st.get("rule_status"),
                  st.get("text", ""))]


def _guard(s: Session, job: Job) -> list[dict]:
    if not job.reported:
        return []
    posts = s.exec(select(GuardPost).where(GuardPost.job_id == job.id)).all()
    suspect = sum(p.status == "suspect" for p in posts)
    text = f"신고함, 보존한 게시물 {len(posts)}건" + (f", 보복 의심 {suspect}건" if suspect else "")
    return [_part("신고 후 보호", engine.BAD if suspect else None, text, "guard")]


def parts(s: Session, user_id: int, job_id: int) -> list[dict]:
    """이 사업장의 모든 기록을 한 줄씩 (화면과 AI가 함께 본다)."""
    job = s.get(Job, job_id)
    evidence = s.exec(select(Evidence).where(Evidence.job_id == job_id)).all()
    open_q = s.exec(select(AgentQuestion).where(AgentQuestion.job_id == job_id, AgentQuestion.status == "open")).all()
    out = [_contract(s, job_id), *_pays(s, job_id), *_records(s, job_id), *_quit(s, user_id, job), *_guard(s, job),
           _part("증거 자료", None, f"{len(evidence)}개 보관 중", "docs")]
    if open_q:
        out.append(_part("답을 기다리는 질문", engine.WARN, f"{len(open_q)}개 (홈의 '에이전트가 물어봐요'에서 답할 수 있어요)"))
    return out


def rule_status(items: list[dict]) -> str:
    """코드가 정리한 사실 중 가장 나쁜 결과. 결과가 있는 사실이 하나도 없으면 확인 필요."""
    found = [p["status"] for p in items if p.get("status")]
    if not found:
        return engine.WARN
    worst = max(found, key=lambda x: RANK.get(x, 1))
    return engine.WARN if worst == engine.PENDING else worst


def target(s: Session, user_id: int, job_id: int) -> dict:
    """AI 판단을 붙일 대상 (검증 장치 judgment_problems가 rule_status와 사실을 본다)."""
    items = parts(s, user_id, job_id)
    return {"law": "", "parts": items, "rule_status": rule_status(items), "status": engine.PENDING,
            "basis": [f"{p['name']}: {p['text']}" for p in items], "text": "; ".join(f"{p['name']} {p['text']}" for p in items),
            "basis_key": case.data_key(s, s.get(Job, job_id)), "at": now_kst().isoformat()}


def latest(s: Session, job_id: int) -> dict | None:
    row = next(iter(_latest(s, job_id, "overview")), None)
    return json.loads(row.results_json) if row else None
