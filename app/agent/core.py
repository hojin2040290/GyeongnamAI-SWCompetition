"""에이전트 판단 반복.

사건(사용자 입력 또는 정해진 시점)이 생기면 시작해서, 도구를 실행하고 결과를 보고 다음 행동을 정한다.
지금은 AI가 연결되지 않아 사건별로 정해 둔 순서(규칙 플래너)로 도구를 부른다.
AI 연결 후에는 planner 부분만 LLM의 도구 호출로 바꾸고, 도구와 기록 방식은 그대로 쓴다.
"""
import json
import uuid

from sqlmodel import Session

from app.agent.tools import make_tools
from app.calc.timeutil import now_kst
from app.models import AgentLog

MAX_STEPS = 10


class Run:
    def __init__(self, session: Session, user_id: int, job_id, event: str):
        self.s, self.user_id, self.job_id, self.event = session, user_id, job_id, event
        self.run_id = uuid.uuid4().hex[:8]
        self.tools = make_tools(session, user_id, job_id)
        self.steps = 0

    def call(self, name: str, *args):
        """도구 실행과 기록. 반복 횟수를 넘으면 멈춘다."""
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise RuntimeError("에이전트 반복 횟수를 넘었어요")
        result = self.tools[name](*args)
        self.log(f"도구 {name}", _short(result))
        return result

    def log(self, step: str, detail: str) -> None:
        self.s.add(AgentLog(user_id=self.user_id, job_id=self.job_id, run_id=self.run_id, event=self.event,
                            step=step, detail=detail[:1000], created_at=now_kst()))
        self.s.commit()


def _short(v) -> str:
    try:
        text = json.dumps(v, ensure_ascii=False, default=str)
    except TypeError:
        text = str(v)
    return text if len(text) < 400 else text[:400] + "…"


def run_contract_check(session: Session, user_id: int, job_id: int) -> dict:
    r = Run(session, user_id, job_id, "contract_check")
    r.log("시작", "계약서와 기본 정보를 법 기준표와 대조")
    items = r.call("judge_job")
    bad = [i for i in items if i["status"] == "bad"]
    warn = [i for i in items if i["status"] == "warn"]
    r.log("판단", f"위반 의심 {len(bad)}건, 확인 필요 {len(warn)}건")
    run_id = r.call("save_check", "contract", items)
    if bad or warn:
        r.call("notify", "계약서 점검 완료", f"위반 의심 {len(bad)}건, 확인 필요 {len(warn)}건이 있어요.")
    else:
        r.call("notify", "계약서 점검 완료", "확인한 항목은 모두 정상이에요.")
    return {"check_id": run_id, "items": items, "run_id": r.run_id}


def run_payday(session: Session, user_id: int, job_id: int, month: str) -> dict:
    r = Run(session, user_id, job_id, "payday")
    r.log("시작", f"{month} 급여 점검")
    expected = r.call("calc_pay", month)
    paid = r.call("get_payslip", month)
    cmp = r.call("compare_pay", expected, paid)
    r.log("판단", cmp["text"])
    r.call("save_check", "payday", {"month": month, "expected": expected, "paid": paid, "compare": cmp})
    if cmp["status"] == "bad":
        r.call("notify", f"{month} 급여 점검 결과", cmp["text"] + " 상담 사전 자료를 만들어 둘 수 있어요.")
    elif paid is None:
        r.call("notify", f"{month} 급여 점검", "받은 급여를 아직 올리지 않았어요. 명세서나 입금 금액을 올려 주세요.")
    return {"expected": expected, "paid": paid, "compare": cmp, "run_id": r.run_id}


def run_quit_check(session: Session, user_id: int, job_id: int) -> dict | None:
    r = Run(session, user_id, job_id, "quit_check")
    st = r.call("settlement")
    if not st:
        r.log("종료", "그만둔 사업장이 아니에요")
        return None
    if st["status"] == "bad":
        r.call("notify", "퇴직 후 임금 지급 기한이 지났어요",
               f"지급 기한 {st['due']}이 지났어요. 아직 못 받았다면 상담 사전 자료를 만들어 보세요.")
    elif st["status"] == "warn" and st["left"] <= 3:
        r.call("notify", "퇴직 후 임금 지급 기한이 다가와요", f"지급 기한 {st['due']}까지 {st['left']}일 남았어요.")
    return st
