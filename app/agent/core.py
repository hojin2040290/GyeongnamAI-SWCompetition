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


def _summary(items: list[dict]) -> str:
    """결과 개수 요약. AI 판단 전 항목은 '확인 중'으로 센다."""
    names = [("bad", "위반 의심"), ("warn", "확인 필요"), ("pending", "확인 중(AI 판단 전)"), ("ok", "정상")]
    parts = [f"{label} {n}건" for key, label in names if (n := sum(i["status"] == key for i in items))]
    return ", ".join(parts) or "점검할 항목이 없어요"


# ---------- 계약서, 근무 기록 점검 ----------
def run_contract_check(session: Session, user_id: int, job_id: int) -> dict:
    r = Run(session, user_id, job_id, "contract_check")
    r.log("입력", "기본 정보, 계약서, 실제 출퇴근 기록을 법 기준표와 대조")
    items = r.call("judge_job")
    rec_items = r.call("judge_records")
    r.log("판단", f"입력 정보 {len(items)}건, 근무 기록 {len(rec_items)}건")
    items = r.call("attach_law", items + rec_items)
    items = r.call("ai_judge", items)
    check_id = r.call("save_check", "contract", items)
    r.call("notify", "계약서 점검 완료", _summary(items) + ". 계약서 탭에서 확인해 보세요.")
    return r.done({"check_id": check_id, "items": items}, _summary(items))


def run_shift_check(session: Session, user_id: int, job_id: int, record_id: int) -> dict:
    """퇴근 버튼을 누른 순간: 그날 기록으로 쉬는 시간, 청소년 근로시간 한도, 야간근로를 바로 점검."""
    r = Run(session, user_id, job_id, "shift_check")
    rec = r.tools["get_record"](record_id)
    day = rec.clock_in.date().isoformat()
    r.log("입력", f"퇴근 기록 {rec.clock_in:%H:%M}~{rec.clock_out:%H:%M}")
    items = r.call("judge_shift", day)
    if not items:
        return r.done({"day": day, "items": []}, "오늘 근무 기록에서 문제를 찾지 못했어요")
    items = r.call("ai_judge", r.call("attach_law", items))
    r.call("save_check", "shift", {"day": day, "items": items})
    r.call("notify", "오늘 근무 점검 결과", _summary(items) + ". 계약서 탭에서 확인해 보세요.")
    return r.done({"day": day, "items": items}, _summary(items))


def run_seek_check(session: Session, user_id: int, data: dict) -> dict:
    r = Run(session, user_id, None, "seek_check")
    r.log("입력", f"지원하려는 곳: {data.get('name') or '이름 없음'}")
    out = r.call("judge_seek", data)
    out["items"] = r.call("ai_judge", r.call("attach_law", out["items"]))
    r.call("save_check", "seek", {"input": data, **out})
    return r.done(out, f"{_summary(out['items'])}, 물어볼 질문 {len(out['questions'])}개")


# ---------- 급여, 퇴직 ----------
def run_payday(session: Session, user_id: int, job_id: int, month: str, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "payday", trigger)
    r.log("입력", f"{month} 급여 점검")
    expected = r.call("calc_pay", month)
    paid = r.call("get_payslip", month)
    cmp = r.call("compare_pay", expected, paid)
    r.log("판단", cmp["text"])
    r.call("save_check", "payday", {"month": month, "expected": expected, "paid": paid, "compare": cmp})
    if cmp.get("short"):  # 금액 차이는 코드가 계산한 사실이라 바로 알린다 (체불 판단은 AI)
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
    unpaid = st["rule_status"] != "ok"  # 받았다고 기록하지 않음
    if unpaid and st["left"] < 0:
        r.call("notify", "퇴직 후 임금 지급 기한이 지났어요",
               f"지급 기한 {st['due']}이 지났어요. 아직 못 받았다면 상담 사전 자료를 만들어 보세요.")
    elif unpaid and st["left"] <= 3:
        r.call("notify", "퇴직 후 임금 지급 기한이 다가와요", f"지급 기한 {st['due']}까지 {st['left']}일 남았어요.")
    r.done({}, f"지급 기한 {st['due']}")
    return st


def run_open_check(session: Session, user_id: int, job_id: int, limit_hours: int, trigger: str = "schedule") -> dict:
    """퇴근을 잊은 기록 찾기: 출근한 지 오래됐는데 퇴근이 없으면 알린다."""
    r = Run(session, user_id, job_id, "open_check", trigger)
    rec = r.call("find_open_record")
    if not rec or rec["hours"] < limit_hours:
        return r.done({"open": rec}, "오래된 출근 기록 없음")
    r.log("판단", f"출근 뒤 {rec['hours']}시간 동안 퇴근 기록 없음")
    day = rec["clock_in"][:16].replace("T", " ")
    r.call("notify", "퇴근을 누르지 않은 것 같아요",
           f"{day} 출근 뒤 퇴근 기록이 없어요. 일을 마쳤다면 퇴근을 누르고, 잘못 누른 출근이면 홈에서 실수로 표시해 주세요.")
    return r.done({"open": rec}, "퇴근 잊음 알림")


# ---------- 사진 읽기 (비전 모델) ----------
def run_read_image(session: Session, user_id: int, job_id: int, evidence_id: int, kind: str) -> dict:
    """계약서나 급여명세서 사진을 AI가 읽는다. 읽은 값은 화면에 채워 사용자가 확인한 뒤 저장한다."""
    r = Run(session, user_id, job_id, f"read_{kind}")
    r.log("입력", f"{'근로계약서' if kind == 'contract' else '급여명세서'} 사진")
    res = r.call("read_contract_image" if kind == "contract" else "read_payslip_image", evidence_id)
    summary = f"항목 {res.get('found', 0)}개를 읽었어요" if res.get("ai") else res.get("reason", "")
    return r.done(res, summary)


# ---------- 상담 ----------
def run_report(session: Session, user_id: int, job_id: int) -> dict:
    r = Run(session, user_id, job_id, "report")
    r.log("입력", "상담 사전 자료 만들기")
    counsel = r.call("counsel_for_age")
    r.log("판단", "만 나이에 맞는 상담 기관: " + ", ".join(c["name"] for c in counsel))
    summary = r.call("summarize_case")
    r.log("판단", "AI가 사건 요약 작성" if summary["ai"] else summary["reason"])
    rep = r.call("build_report", summary)
    r.call("notify", "상담 사전 자료를 만들었어요", "자료 탭에서 다시 열어 볼 수 있어요. 상담 기관에 낼 때 함께 보여 주세요.")
    return r.done({**rep, "counsel": counsel, "summary_ai": summary["ai"]}, "상담 사전 자료 작성 완료")


# ---------- 신고 후 보호 ----------
def run_guard_toggle(session: Session, user_id: int, job_id: int, on: bool) -> dict:
    r = Run(session, user_id, job_id, "guard_on" if on else "guard_off")
    r.log("입력", "신고했어요 켬" if on else "신고했어요 끔")
    r.call("set_reported", on)
    if not on:
        return r.done({"message": ""}, "보복 대응을 멈췄어요")
    wrote = r.call("write_warning_message")
    r.log("판단", "AI가 불리한 처우 금지 조항을 근거로 안내 문구 작성" if wrote["ai"] else wrote["reason"])
    msg = r.call("warning_message")
    r.call("notify", "보복 대응을 시작했어요", "사업주에게 보낼 안내 문구를 준비했고, 매일 공개 게시물을 확인해요.")
    return r.done({"message": msg}, "안내 문구 준비, 매일 게시물 확인 예약")


def run_guard_search(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "guard_search", trigger)
    if not r.tools["get_job"]().guard_ai_message:  # 전에 AI 응답이 없었다면 안내 문구를 다시 맡긴다
        r.call("write_warning_message")
    res = r.call("search_posts")
    judged = r.call("classify_posts")  # 검색을 건너뛰어도 판별 대기 게시물은 판별한다
    _notify_posts(r, res.get("added", 0), judged)
    if res.get("skipped"):
        return r.done({**res, "classify": judged}, res["reason"])
    return r.done({**res, "classify": judged}, f"새 게시물 {res['added']}건")


def _notify_posts(r: Run, added: int, judged: dict) -> None:
    if judged.get("suspect"):
        r.call("notify", "보복이 의심되는 게시물이 있어요",
               f"AI가 게시물 {judged['suspect']}건을 보복 의심으로 판별했어요. 보호 탭에서 확인하고 상담 사전 자료를 만들어 보세요.")
    elif added:
        r.call("notify", "새 공개 게시물을 찾았어요", f"게시물 {added}건을 보존했어요. 보호 탭에서 확인해 보세요.")


def run_guard_preserve(session: Session, user_id: int, job_id: int, url: str, title: str = "") -> dict:
    r = Run(session, user_id, job_id, "guard_preserve")
    r.log("입력", f"게시물 주소 {url[:120]}")
    res = r.call("preserve_post", url, title)
    judged = r.call("classify_posts")
    _notify_posts(r, 0, judged)
    return r.done({**res, "classify": judged}, "주소, 확인 시각" + (", 화면 캡처" if res["captured"] else "") + " 보존")
