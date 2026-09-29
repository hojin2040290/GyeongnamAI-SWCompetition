"""에이전트 판단 반복.

사건(사용자 입력 또는 정해진 시점)이 생기면 시작해서, 도구를 실행하고 결과를 보고 다음 행동을 정한다.
지금은 AI가 연결되지 않아 사건별로 정해 둔 순서(규칙 플래너)로 도구를 부른다.
AI 연결 후에는 planner 부분만 LLM의 도구 호출로 바꾸고, 도구와 기록 방식은 그대로 쓴다.
모든 흐름은 입력, 판단, 도구 실행, 결과를 동작 기록(AgentLog)에 남기고 화면에도 돌려준다.
"""
import json
import uuid

from sqlmodel import Session

from app.agent.tools import make_tools
from app.calc.timeutil import now_kst
from app.models import AgentLog

MAX_STEPS = 10


class Run:
    def __init__(self, session: Session, user_id: int, job_id, event: str, trigger: str = "user"):
        self.s, self.user_id, self.job_id, self.event = session, user_id, job_id, event
        self.run_id = uuid.uuid4().hex[:8]
        self.tools = make_tools(session, user_id, job_id)
        self.steps = 0
        self.trace: list[dict] = []
        self.log("시작", "사용자 입력" if trigger == "user" else "정해진 시점 (자동 점검)")

    def call(self, name: str, *args):
        """도구 실행과 기록. 반복 횟수를 넘으면 멈춘다."""
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise RuntimeError("에이전트 반복 횟수를 넘었어요")
        result = self.tools[name](*args)
        self.log(f"도구 {name}", _short(result))
        return result

    def log(self, step: str, detail: str) -> None:
        self.trace.append({"step": step, "detail": detail[:300]})
        self.s.add(AgentLog(user_id=self.user_id, job_id=self.job_id, run_id=self.run_id, event=self.event,
                            step=step, detail=detail[:1000], created_at=now_kst()))
        self.s.commit()

    def done(self, out: dict, summary: str) -> dict:
        self.log("결과", summary)
        return {**out, "run_id": self.run_id, "trace": self.trace}


def _short(v) -> str:
    try:
        text = json.dumps(v, ensure_ascii=False, default=str)
    except TypeError:
        text = str(v)
    return text if len(text) < 400 else text[:400] + "…"


def _count(items: list[dict]) -> tuple[int, int]:
    return sum(i["status"] == "bad" for i in items), sum(i["status"] == "warn" for i in items)


# ---------- 계약서, 근무 기록 점검 ----------
def run_contract_check(session: Session, user_id: int, job_id: int) -> dict:
    r = Run(session, user_id, job_id, "contract_check")
    r.log("입력", "기본 정보, 계약서, 실제 출퇴근 기록을 법 기준표와 대조")
    items = r.call("judge_job")
    rec_items = r.call("judge_records")
    r.log("판단", f"입력 정보 {len(items)}건, 근무 기록 {len(rec_items)}건")
    items = r.call("attach_law", items + rec_items)
    bad, warn = _count(items)
    check_id = r.call("save_check", "contract", items)
    r.call("notify", "계약서와 근무 기록 점검 완료",
           f"위반 의심 {bad}건, 확인 필요 {warn}건이 있어요." if bad or warn else "확인한 항목은 모두 정상이에요.")
    return r.done({"check_id": check_id, "items": items}, f"위반 의심 {bad}건, 확인 필요 {warn}건")


def run_shift_check(session: Session, user_id: int, job_id: int, record_id: int) -> dict:
    """퇴근 버튼을 누른 순간: 그날 기록으로 쉬는 시간, 청소년 근로시간 한도, 야간근로를 바로 점검."""
    r = Run(session, user_id, job_id, "shift_check")
    rec = r.tools["get_record"](record_id)
    day = rec.clock_in.date().isoformat()
    r.log("입력", f"퇴근 기록 {rec.clock_in:%H:%M}~{rec.clock_out:%H:%M}")
    items = r.call("judge_shift", day)
    if not items:
        return r.done({"day": day, "items": []}, "오늘 근무 기록에서 문제를 찾지 못했어요")
    items = r.call("attach_law", items)
    bad, warn = _count(items)
    r.call("save_check", "shift", {"day": day, "items": items})
    r.call("notify", "오늘 근무 점검 결과", f"위반 의심 {bad}건, 확인 필요 {warn}건. 점검 탭에서 확인해 보세요.")
    return r.done({"day": day, "items": items}, f"위반 의심 {bad}건, 확인 필요 {warn}건")


def run_seek_check(session: Session, user_id: int, data: dict) -> dict:
    r = Run(session, user_id, None, "seek_check")
    r.log("입력", f"지원하려는 곳: {data.get('name') or '이름 없음'}")
    out = r.call("judge_seek", data)
    out["items"] = r.call("attach_law", out["items"])
    bad, warn = _count(out["items"])
    r.call("save_check", "seek", {"input": data, **out})
    return r.done(out, f"위반 의심 {bad}건, 확인 필요 {warn}건, 물어볼 질문 {len(out['questions'])}개")


# ---------- 급여, 퇴직 ----------
def run_payday(session: Session, user_id: int, job_id: int, month: str, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "payday", trigger)
    r.log("입력", f"{month} 급여 점검")
    expected = r.call("calc_pay", month)
    paid = r.call("get_payslip", month)
    cmp = r.call("compare_pay", expected, paid)
    r.log("판단", cmp["text"])
    r.call("save_check", "payday", {"month": month, "expected": expected, "paid": paid, "compare": cmp})
    if cmp["status"] == "bad":
        r.call("notify", f"{month} 급여 점검 결과", cmp["text"] + " 상담 사전 자료를 만들어 둘 수 있어요.")
    elif paid is None:
        r.call("notify", f"{month} 급여 점검", "받은 급여를 아직 올리지 않았어요. 명세서나 입금 금액을 올려 주세요.")
    return r.done({"expected": expected, "paid": paid, "compare": cmp}, cmp["text"])


def run_quit_check(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict | None:
    r = Run(session, user_id, job_id, "quit_check", trigger)
    st = r.call("settlement")
    if not st:
        r.done({}, "그만둔 사업장이 아니에요")
        return None
    if st["status"] == "bad":
        r.call("notify", "퇴직 후 임금 지급 기한이 지났어요",
               f"지급 기한 {st['due']}이 지났어요. 아직 못 받았다면 상담 사전 자료를 만들어 보세요.")
    elif st["status"] == "warn" and st["left"] <= 3:
        r.call("notify", "퇴직 후 임금 지급 기한이 다가와요", f"지급 기한 {st['due']}까지 {st['left']}일 남았어요.")
    r.done({}, f"지급 기한 {st['due']}")
    return st


# ---------- 상담 ----------
def run_report(session: Session, user_id: int, job_id: int) -> dict:
    r = Run(session, user_id, job_id, "report")
    r.log("입력", "상담 사전 자료 만들기")
    counsel = r.call("counsel_for_age")
    r.log("판단", "만 나이에 맞는 상담 기관: " + ", ".join(c["name"] for c in counsel))
    rep = r.call("build_report")
    r.call("notify", "상담 사전 자료를 만들었어요", "자료 탭에서 다시 열어 볼 수 있어요. 상담 기관에 낼 때 함께 보여 주세요.")
    return r.done({**rep, "counsel": counsel}, "상담 사전 자료 작성 완료")


# ---------- 신고 후 보호 ----------
def run_guard_toggle(session: Session, user_id: int, job_id: int, on: bool) -> dict:
    r = Run(session, user_id, job_id, "guard_on" if on else "guard_off")
    r.log("입력", "신고했어요 켬" if on else "신고했어요 끔")
    r.call("set_reported", on)
    if not on:
        return r.done({"message": ""}, "보복 대응을 멈췄어요")
    msg = r.call("warning_message")
    r.log("판단", "신고를 이유로 한 불리한 처우 금지 조항을 담은 안내 문구 작성")
    r.call("notify", "보복 대응을 시작했어요", "사업주에게 보낼 안내 문구를 준비했고, 매일 공개 게시물을 확인해요.")
    return r.done({"message": msg}, "안내 문구 준비, 매일 게시물 확인 예약")


def run_guard_search(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "guard_search", trigger)
    res = r.call("search_posts")
    if res.get("skipped"):
        return r.done(res, res["reason"])
    judged = r.call("classify_posts")
    if res["added"]:
        r.call("notify", "새 공개 게시물을 찾았어요", f"게시물 {res['added']}건을 보존했어요. 보호 탭에서 확인해 보세요.")
    return r.done({**res, "classify": judged}, f"새 게시물 {res['added']}건")


def run_guard_preserve(session: Session, user_id: int, job_id: int, url: str, title: str = "") -> dict:
    r = Run(session, user_id, job_id, "guard_preserve")
    r.log("입력", f"게시물 주소 {url[:120]}")
    res = r.call("preserve_post", url, title)
    judged = r.call("classify_posts")
    return r.done({**res, "classify": judged}, "주소, 확인 시각" + (", 화면 캡처" if res["captured"] else "") + " 보존")
