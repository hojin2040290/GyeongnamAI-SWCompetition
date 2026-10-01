"""에이전트 실행.

사용자 입력이나 정해진 시점이 되면 시작한다. AI에게는 그 일의 목표와 쓸 수 있는 도구만 주고,
AI가 도구를 고르고 결과를 보며 다음 행동을 정한다 (app/agent/loop.py).
AI가 없거나 응답하지 않으면 정해 둔 순서로 사실만 정리하고, 판단할 부분은 'AI 응답 대기 중'으로 둔다.
모든 흐름은 입력, AI 판단, 도구 실행, 결과를 동작 기록(AgentLog)에 남기고 화면에도 돌려준다.
"""
import dataclasses
import json
import uuid
from contextvars import ContextVar
from datetime import date

from sqlmodel import Session, select

from app import notices
from app.agent import case
from app.agent.loop import AI_WAITING, Goal, Tool, run_agent
from app.agent.safety import scrub
from app.calc import pay as paycalc
from app.agent.tools import agent_tools, for_ai, make_tools
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.law.lookup import known_law
from app.llm import client
from app.models import AgentLog, CaseNote

MAX_STEPS = 10  # AI 응답이 없을 때 정해 둔 순서의 도구 호출 수 제한
FOLLOWUP_NOTE: ContextVar[str] = ContextVar("followup_note", default="")  # 예약한 확인을 실행할 때 그 이유

S = {"type": "string"}
JUDGE_ONE = {"status": {"type": "string", "enum": ["ok", "warn", "bad"], "description": "정상, 확인 필요, 위반 의심"},
             "law": {"type": "string", "description": "근거로 쓴 조항"}, "fact": {"type": "string", "description": "근거로 쓴 사실"},
             "reason": {"type": "string", "description": "판단 이유 한두 문장"}}
JUDGMENTS = {"judgments": {"type": "array", "description": "검토 항목마다 판단 하나", "items": {
    "type": "object", "properties": {"i": {"type": "integer", "description": "check_rules 항목 번호"}, **JUDGE_ONE,
                                     "answer_ids": {"type": "array", "items": {"type": "integer"},
                                                    "description": "근거로 쓴 사용자 답변 번호 (get_answers)"}},
    "required": ["i", "status", "law", "fact", "reason"]}}}
DONE = {"note": {"type": "string", "description": "한 일 요약"}}


class Run:
    def __init__(self, session: Session, user_id: int, job_id, event: str, trigger: str = "user", topic: str = ""):
        self.s, self.user_id, self.job_id, self.event = session, user_id, job_id, event
        self.run_id = uuid.uuid4().hex[:8]
        self.topic = topic or notices.topic_of(event)  # 이 실행이 보내는 알림의 종류
        self.tools = make_tools(session, user_id, job_id, self.topic, self.run_id)
        self.state: dict = {"event": event, "run_id": self.run_id, "topic": self.topic}  # AI가 도구로 만든 결과 (검토 항목, 급여 비교 등)
        self.followup = FOLLOWUP_NOTE.get()
        self.steps = 0
        self.ai_used = False
        self.ai_tried = False  # AI에게 맡기려 했는지 (결과 기록에 AI 몫이 빠졌는지 적기 위해)
        self.ai_error = ""
        self.trace: list[dict] = []
        label = {"user": "사용자 입력", "schedule": "정해진 시점 (자동 점검)", "agent": "에이전트가 시작",
                 "answer": "사용자가 에이전트의 질문에 답함", "followup": "에이전트가 예약한 확인",
                 "retry": "AI 응답 대기 중이던 일 다시 맡김"}
        self.log("시작", label.get(trigger, trigger))

    def call(self, name: str, *args):
        """정해 둔 순서로 도구 실행 (AI 응답이 없을 때). 반복 횟수를 넘으면 멈춘다."""
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise RuntimeError("에이전트 반복 횟수를 넘었어요")
        result = self.tools[name](*args)
        self.log(f"도구 {name}", _short(result))
        return result

    def agent(self, goal: Goal, context: dict, extra: list[Tool] | None = None) -> dict | None:
        """AI가 목표를 이룰 때까지 도구를 고르게 한다. 못 하면 None (대기)."""
        tools = agent_tools(self.s, self.user_id, self.job_id, self.state)
        for t in extra or []:
            tools[t.name] = t
        if self.job_id is not None:  # 지난 메모와 진행 상황을 넘기고, 기억 남기기와 조언을 쓸 수 있게 한다
            context = {**context, "진행 상황": case.progress(self.s, self.tools["get_job"]()),
                       "지난 메모": case.memories(self.s, self.job_id) or "아직 없음",
                       "질문과 답": case.questions(self.s, self.job_id) or "아직 없음",
                       "예약한 확인": case.followups(self.s, self.job_id) or "없음"}
            if self.followup:
                context["이번에 할 일"] = f"지난번에 예약한 확인이에요: {self.followup}"
            goal = dataclasses.replace(goal, tools=[*goal.tools, "remember", "give_advice", *(["ask_user"] if goal.ask else []),
                                                    "get_answers", "schedule_followup"])
        try:
            out = run_agent(self, goal, tools, context)
        except Exception as exc:  # 예상 못 한 오류는 요청을 멈추지 않고 AI 없이 하는 방식으로 넘긴다
            self.s.rollback()
            self.ai_error = f"에이전트 실행 중 오류가 났어요 ({type(exc).__name__})"
            self.log("오류", self.ai_error)
            out = None
        self.ai_tried, self.ai_used = True, out is not None
        if not self.ai_used:
            self.log("대기", f"{AI_WAITING}: 정해 둔 순서로 사실만 정리해요")
        return out

    def rules_tool(self, make_items, desc: str) -> Tool:
        """법 기준표와 대조해 검토할 항목과 사실을 만드는 도구. 결과 판단은 AI가 한다."""
        def check_rules() -> list[dict]:
            self.state["items"] = self.tools["wait_ai"](make_items())
            return for_ai(self.state["items"])
        return Tool("check_rules", desc, check_rules)

    def need(self, key: str, msg: str):
        """finish 전에 꼭 거쳐야 할 도구를 확인한다. 반복이 끝나 갈 때 남은 할 일로 AI에게 알린다 (loop.pending)."""
        def check(_args: dict) -> str | None:
            return None if self.state.get(key) is not None else msg
        check.pending_tool = True
        return check

    def log(self, step: str, detail: str) -> None:
        self.trace.append({"step": step, "detail": detail[:300]})
        self.s.add(AgentLog(user_id=self.user_id, job_id=self.job_id, run_id=self.run_id, event=self.event,
                            step=step, detail=detail[:1000], created_at=now_kst()))
        self.s.commit()

    def done(self, out: dict, summary: str) -> dict:
        if self.ai_tried and not self.ai_used and AI_WAITING not in summary:
            summary += f" (AI가 할 판단과 작성은 {AI_WAITING})"
        self.log("결과", summary)
        return {**out, "ai_agent": self.ai_used, "plan": self.state.get("plan"), "run_id": self.run_id,
                "trace": self.trace, "trace_at": now_kst().isoformat()}  # trace_at: 에이전트가 끝난 시각 (화면의 동작 보기에 적음)


def _short(v) -> str:
    try:
        text = json.dumps(v, ensure_ascii=False, default=str)
    except TypeError:
        text = str(v)
    return text if len(text) < 400 else text[:400] + "…"


def _summary(items: list[dict]) -> str:
    """결과 개수 요약. AI 판단 전 항목은 'AI 에이전트 판단 대기'로 센다."""
    names = [("bad", "위반 의심"), ("warn", "확인 필요"), ("pending", "AI 에이전트 판단 대기"), ("ok", "정상")]
    parts = [f"{label} {n}건" for key, label in names if (n := sum(i["status"] == key for i in items))]
    return ", ".join(parts) or "점검할 항목이 없어요"


def apply_one(s: Session, target: dict, out: dict | None, error: str = "") -> dict:
    """AI의 판단 하나(급여, 퇴직 정산)를 붙이고 검증 장치로 확인한다. AI가 없으면 대기로 둔다."""
    law, fact = str((out or {}).get("law", "")).strip(), str((out or {}).get("fact", "")).strip()
    if out and out.get("status") == engine.BAD and target.get("rule_status") == engine.OK:
        # 검증 장치가 돌려보냈는데도(또는 마지막 반복이라) 코드 계산·기록과 맞지 않는 위반 의심이면 받지 않는다
        target.update(status=engine.OK, ai_error="AI 판단이 코드 계산과 기록에 맞지 않아 받지 않았어요")
        target.pop("ai_reason", None)
        return engine.cross_check([target])[0]
    if out and out.get("status") in (engine.OK, engine.WARN, engine.BAD) and fact and known_law(s, law):
        target.update(status=out["status"], ai_reason=scrub(str(out.get("reason", "")))[:300], ai_law=law, ai_fact=fact[:300])
        target.pop("ai_error", None)
        return engine.cross_check([target])[0]
    if target.get("status") == engine.PENDING:
        target["ai_error"] = (error or AI_WAITING) if not out else "AI가 댄 근거 조항이나 사실을 받을 수 없어 판단하지 않았어요"
    return target


STATUS_NAME = {"ok": "정상", "warn": "확인 필요", "bad": "위반 의심", "pending": "AI 에이전트 판단 대기"}


def log_judgments(r: Run, items: list[dict]) -> None:
    """항목마다 AI 에이전트의 판단(결과, 근거 조항, 사실, 이유)을 동작 기록에 읽을 수 있게 남긴다."""
    for it in items:
        name = it.get("law") or "비교 결과"
        if it.get("ai_reason"):
            r.log("AI 판단 결과", f"{name}: {STATUS_NAME.get(it['status'], it['status'])} · 근거 조항 {it.get('ai_law', '')} · "
                                f"사실 {it.get('ai_fact', '')} · 이유 {it['ai_reason']}")
        elif it.get("status") == engine.PENDING:
            r.log("AI 판단 결과", f"{name}: {STATUS_NAME['pending']}" + (f" ({it['ai_error']})" if it.get("ai_error") else ""))
        else:
            r.log("AI 판단 결과", f"{name}: {STATUS_NAME.get(it['status'], it['status'])} (AI 판단 없음, 코드가 정리한 사실 기준)")


def _judge_items(r: Run, goal_text: str, tools: list[str], make_items, context: dict, finish_extra: dict | None = None):
    """검토 항목 판단 (계약서, 퇴근, 지원 전 공통): AI가 check_rules로 항목을 받아 판단한다."""
    goal = Goal(goal_text, ["check_rules", *tools], {**JUDGMENTS, **(finish_extra or {})}, ["judgments"],
                check=r.need("items", "먼저 check_rules로 검토 항목을 받아 주세요"),
                review=lambda a: r.tools["review_judgments"](r.state["items"], a.get("judgments")))
    out = r.agent(goal, context, [r.rules_tool(make_items, "법 기준표와 대조해 검토할 항목과 근거 사실을 받는다.")])
    if out:
        items = r.tools["apply_judgments"](r.state["items"], out.get("judgments"))
        log_judgments(r, items)
        return items, out
    items = r.tools["wait_ai"](r.state.get("items") or make_items(), r.ai_error)
    log_judgments(r, items)
    return items, None


# ---------- 계약서, 근무 기록 점검 ----------
CONTRACT_GOAL = ("이 사업장의 기본 정보, 계약서 내용, 실제 출퇴근 기록을 법 기준과 대조해 주세요. check_rules로 검토 항목을 받고, "
                 "필요하면 조문과 계산 결과를 확인한 뒤, 항목마다 정상(ok), 확인 필요(warn), 위반 의심(bad) 중 하나로 판단해 "
                 "finish의 judgments에 담아 주세요. 위반 의심이 있으면 사용자에게 알림을 보내 주세요.")


def run_contract_check(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "contract_check", trigger)
    r.log("입력", "기본 정보, 계약서, 실제 출퇴근 기록을 법 기준표와 대조")
    items, _ = _judge_items(r, CONTRACT_GOAL, ["get_profile", "get_contract", "calc_work_days", "get_age_on",
                                                "get_article", "find_refs", "notify"],
                            lambda: r.tools["judge_job"]() + r.tools["judge_records"](), {"사업장": r.tools["get_job"]().name})
    items = r.tools["attach_law"](items)
    check_id = r.tools["save_check"]("contract", items)
    if not r.ai_used:
        r.call("notify", "계약서 점검 완료", _summary(items) + ". 계약서 탭에서 확인해 보세요.")
    return r.done({"check_id": check_id, "items": items}, _summary(items))


def run_shift_check(session: Session, user_id: int, job_id: int, record_id: int) -> dict:
    """퇴근 버튼을 누른 순간: 그날 기록으로 쉬는 시간, 청소년 근로시간 한도, 야간근로를 바로 점검."""
    r = Run(session, user_id, job_id, "shift_check")
    rec = r.tools["get_record"](record_id)
    day = rec.clock_in.date().isoformat()
    r.log("입력", f"퇴근 기록 {rec.clock_in:%H:%M}~{rec.clock_out:%H:%M}")
    found = r.call("judge_shift", day)
    if not found:  # 코드가 찾은 검토 항목이 없으면 AI에게 맡길 일이 없다
        return r.done({"day": day, "items": []}, "오늘 근무 기록에서 검토할 항목이 없어요")
    goal = (f"사용자가 방금 퇴근했어요 ({day}). 그날과 그 주의 출퇴근 기록을 check_rules로 받아 항목마다 판단해 "
            "finish의 judgments에 담고, 문제가 의심되면 사용자에게 알려 주세요.")
    items, _ = _judge_items(r, goal, ["calc_work_days", "get_age_on", "get_article", "find_refs", "notify"],
                            lambda: found, {"퇴근한 날": day})
    items = r.tools["attach_law"](items)
    r.tools["save_check"]("shift", {"day": day, "items": items})
    if not r.ai_used:
        r.call("notify", "오늘 근무 점검 결과", _summary(items) + ". 계약서 탭에서 확인해 보세요.")
    return r.done({"day": day, "items": items}, _summary(items))


def run_seek_check(session: Session, user_id: int, data: dict) -> dict:
    r = Run(session, user_id, None, "seek_check")
    r.log("입력", f"지원하려는 곳: {data.get('name') or '이름 없음'}")
    seek = r.call("judge_seek", data)
    goal = ("사용자가 아르바이트에 지원하기 전이에요. 공고 조건을 check_rules로 받아 항목마다 판단해 finish의 judgments에 담고, "
            "지원할 때 사업장에 물어보면 좋을 질문이 더 있으면 extra_questions에 넣어 주세요.")
    extra_q = {"extra_questions": {"type": "array", "items": S, "description": "더 물어볼 질문 (없으면 빈 배열)"}}
    items, out = _judge_items(r, goal, ["get_profile", "get_age_on", "get_article", "find_refs"],
                              lambda: seek["items"], {"공고": data}, extra_q)
    questions = seek["questions"] + [scrub(str(q))[:200] for q in (out or {}).get("extra_questions") or []
                                     if str(q).strip() and q not in seek["questions"]][:5]
    res = {"items": r.tools["attach_law"](items), "questions": questions}
    r.tools["save_check"]("seek", {"input": data, **res})
    return r.done(res, f"{_summary(res['items'])}, 물어볼 질문 {len(questions)}개")


# ---------- 급여, 퇴직 ----------
def run_payday(session: Session, user_id: int, job_id: int, month: str, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "payday", trigger, topic=notices.topic_of("payday", month))
    r.state["resume"] = {"month": month}  # 질문에 답하면 같은 달로 다시 시작
    r.log("입력", f"{month} 급여 점검")
    tools = ["get_profile", "calc_pay", "get_payslip", "compare_pay", "calc_work_days", "get_article", "notify"]
    missing = r.tools["compare_pay"](r.tools["calc_pay"](month), r.tools["get_payslip"](month)).get("needed")
    if missing:  # 비교에 필요한 정보가 없으면 체불을 판단하지 않는다 (추측하지 않고 코드가 '확인 필요'로 둔다)
        goal = Goal(f"{month} 급여는 비교에 필요한 정보({', '.join(missing)})가 없어 체불 여부를 판단하지 않아요. "
                    "compare_pay로 상황을 확인하고, 무엇을 기록하거나 올리면 비교할 수 있는지 사용자에게 알리거나 ask_user로 물어본 뒤 "
                    "finish로 끝내 주세요. 정보가 없는 것을 체불이나 위반으로 쓰지 마세요.",
                    tools, DONE, check=r.need("pay", "먼저 compare_pay로 금액을 확인해 주세요"))
    else:
        goal = Goal(f"{month} 급여를 계산한 금액과 받은 금액을 compare_pay로 비교하고, 적게 받은 것이 의심되는지 판단해 finish에 담아 주세요. "
                    "금액은 도구 결과만 쓰세요. 판단 기준: "
                    "ok: 받은 금액이 계산한 금액 이상이거나 차이가 아주 작음 (더 받은 것은 체불이 아니에요). "
                    "warn: 적게 받았지만 계산에 쓴 정보(쉬는 시간, 사업장 인원, 수당 조건 등 calc_pay의 notes)가 불확실해 단정할 수 없음. "
                    "bad: 근무 기록으로 계산한 금액보다 분명히 적게 받았고 계산 조건에 불확실한 점이 없음. "
                    "근거 조항(law)은 compare_pay의 '관련 조항' 가운데 차이와 관련된 것을 쓰세요. "
                    "적게 받았거나 받은 금액이 없으면 사용자에게 알림을 보내 주세요.",
                    tools, JUDGE_ONE, list(JUDGE_ONE), check=r.need("pay", "먼저 compare_pay로 금액을 비교해 주세요"),
                    review=lambda a: r.tools["review_one"](r.state["pay"]["compare"], a))
    out = r.agent(goal, {"달": month})
    if missing:
        out = None  # 판단은 받지 않는다 (알림, 질문, 메모는 위에서 이미 했다)
    pay = r.state.get("pay")
    if not pay:
        expected, paid = r.call("calc_pay", month), r.call("get_payslip", month)
        pay = {"month": month, "expected": expected, "paid": paid, "compare": r.call("compare_pay", expected, paid)}
    cmp = apply_one(session, pay["compare"], out, r.ai_error)
    log_judgments(r, [cmp])
    r.log("판단", cmp["text"])
    r.tools["save_check"]("payday", pay)
    if not r.ai_used:
        if cmp.get("short"):  # 금액 차이는 코드가 계산한 사실이라 바로 알린다 (체불 판단은 AI)
            r.call("notify", f"{month} 급여 점검 결과", cmp["text"] + " 상담 사전 자료를 만들어 둘 수 있어요.")
        elif pay["paid"] is None:
            r.call("notify", f"{month} 급여 점검", "받은 급여를 아직 올리지 않았어요. 명세서나 입금 금액을 올려 주세요.")
    return r.done({"expected": pay["expected"], "paid": pay["paid"], "compare": cmp}, cmp["text"])


def run_quit_check(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict | None:
    r = Run(session, user_id, job_id, "quit_check", trigger)
    if not r.tools["settlement"]():
        r.done({}, "그만둔 사업장이 아니에요")
        return None
    goal = Goal("사용자가 일을 그만뒀어요. settlement로 임금 지급 기한과 받음 여부를 확인하고, 남은 임금을 기한 안에 받지 못한 것이 "
                "의심되는지 판단해 finish에 담아 주세요. 판단 기준: 사용자가 받았다고 기록했으면 기한이 지났어도 ok "
                "(받은 것을 체불로 보지 않아요). 기한이 남았고 못 받았으면 warn. 기한이 지났고 못 받았다고 기록했으면 bad. "
                "기한이 지났는데 받았는지 기록하지 않았으면 추측하지 말고 warn으로 두고, 홈의 퇴직 정산에서 '남은 임금 받았어요' 또는 "
                "'아직 못 받았어요'를 눌러 기록해 달라고 notify로 알려 주세요 (받았는지는 질문으로 묻지 않아요). "
                "근거 조항은 settlement의 '관련 조항'에서, 사실은 settlement의 '사실'에서 가져오세요. "
                "받았다는 기록이 없는데 기한이 지났거나 3일 안으로 다가왔으면 사용자에게 알려 주세요.",
                ["get_profile", "settlement", "get_article", "notify"], JUDGE_ONE, list(JUDGE_ONE),
                check=r.need("settlement", "먼저 settlement로 지급 기한을 확인해 주세요"),
                review=lambda a: r.tools["review_one"](r.state["settlement"], a), ask=False)
    out = r.agent(goal, {})
    st = r.state.get("settlement") or r.call("settlement")
    st = apply_one(session, st, out, r.ai_error)
    log_judgments(r, [{**st, "law": "퇴직 후 임금 지급"}])
    r.tools["save_check"]("quit", st)
    if not r.ai_used:
        unpaid = st["rule_status"] != "ok"  # 받았다고 기록하지 않음
        if unpaid and st["left"] < 0:
            r.call("notify", "퇴직 후 임금 지급 기한이 지났어요",
                   f"지급 기한 {st['due']}이 지났어요. 아직 못 받았다면 상담 사전 자료를 만들어 보세요.")
        elif unpaid and st["left"] <= 3:
            r.call("notify", "퇴직 후 임금 지급 기한이 다가와요", f"지급 기한 {st['due']}까지 {st['left']}일 남았어요.")
    r.done({}, f"지급 기한 {st['due']}")
    return st


def run_open_check(session: Session, user_id: int, job_id: int, limit_hours: int, trigger: str = "schedule") -> dict:
    """퇴근을 잊은 기록 찾기: 출근한 지 오래됐는데 퇴근이 없으면 알린다 (시간 계산과 알림은 코드의 일)."""
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
def run_report(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "report", trigger)
    r.log("입력", "상담 사전 자료 만들기")
    goal = Goal("사용자가 노동 상담 기관에 가져갈 상담 사전 자료를 만들어 주세요. 저장된 점검 결과와 기록을 확인하고, "
                "상황 요약과 상담 때 물어볼 점을 build_report에 넣어 문서를 만든 뒤, 사용자에게 알리고 finish로 끝내 주세요. "
                "요약에는 기록에 있는 사실만 쓰고, 숫자는 도구 결과를 그대로 옮기세요.",
                ["get_profile", "get_contract", "get_saved_checks", "calc_work_days", "settlement", "list_evidence",
                 "counsel_for_age", "get_article", "find_refs", "build_report", "notify"], DONE,
                check=r.need("report", "먼저 build_report로 문서를 만들어 주세요"))
    r.agent(goal, {})
    counsel = r.tools["counsel_for_age"]()
    rep = r.state.get("report")
    if not rep:
        rep = r.call("build_report", {"ai": False, "reason": f"{AI_WAITING}: {r.ai_error or 'AI가 응답하면 요약을 넣어 다시 만들어요'}"})
        r.call("notify", "상담 사전 자료를 만들었어요 (AI 요약은 응답 대기 중)",
               "기록을 정리한 자료를 만들었어요. AI가 쓰는 요약은 아직 빠져 있고, AI가 응답하면 요약을 넣어 다시 만들어요.")
    return r.done({**rep, "counsel": counsel, "summary_ai": r.ai_used},
                  "상담 사전 자료 작성 완료 (AI 요약 포함)" if r.ai_used
                  else f"기록만 정리한 상담 사전 자료 작성, AI 요약은 {AI_WAITING}")


# ---------- 신고 후 보호 ----------
def run_guard_toggle(session: Session, user_id: int, job_id: int, on: bool) -> dict:
    r = Run(session, user_id, job_id, "guard_on" if on else "guard_off")
    r.log("입력", "신고했어요 켬" if on else "신고했어요 끔")
    r.call("set_reported", on)
    if not on:
        return r.done({"message": ""}, "신고 후 보호를 멈췄어요")
    goal = Goal("사용자가 사업장을 노동관계법 위반으로 신고했어요. 보복을 막기 위해 사업주에게 보낼 안내 문구를 법 기준표의 조문을 "
                "근거로 정중하게 써서 save_warning_message로 저장하고, 사용자에게 신고 후 보호를 시작했다고 알린 뒤 finish로 끝내 주세요.",
                ["get_profile", "get_article", "save_warning_message", "notify"], DONE,
                check=r.need("message", "먼저 save_warning_message로 안내 문구를 저장해 주세요"), ask=False)
    r.agent(goal, {"보복 금지 관련 조항": _retaliation_laws()})
    if not r.ai_used:
        r.call("notify", "신고 후 보호를 시작했어요", "사업주에게 보낼 기본 안내 문구를 준비했고, 매일 공개 게시물을 확인해요. "
               "AI가 응답하면 상황에 맞는 문구로 바꿔요.")
    return r.done({"message": r.call("warning_message")},
                  "AI가 쓴 안내 문구 저장, 매일 게시물 확인 예약" if r.ai_used
                  else f"기본 안내 문구로 준비 (AI 문구는 {AI_WAITING}), 매일 게시물 확인 예약")


def _retaliation_laws() -> list[str]:
    from app.calc.params import P
    return P()["retaliation"]["laws"]


POST_GOAL = ("판별 대기 게시물을 list_posts로 보고, 신고한 근로자를 겨냥한 보복성 게시물(신상 공개, 비방, 취업 방해 등)인지 "
             "게시물마다 set_post_status로 저장해 주세요. 제목과 내용만 보고 판단하고, 내용이 부족하면 unclear로 두세요. "
             "보복이 의심되면 사용자에게 알린 뒤 finish로 끝내 주세요.")
POST_TOOLS = ["get_profile", "list_posts", "set_post_status", "get_article", "notify"]


def run_guard_search(session: Session, user_id: int, job_id: int, trigger: str = "user") -> dict:
    r = Run(session, user_id, job_id, "guard_search", trigger)
    no_msg = not r.tools["get_job"]().guard_ai_message
    goal = Goal("공개 게시물을 search_posts로 검색해 새 게시물을 찾아 주세요. 그다음 " + POST_GOAL
                + (" 아직 AI가 쓴 보복 금지 안내 문구가 없으니 save_warning_message로 함께 저장해 주세요." if no_msg else ""),
                ["search_posts", *POST_TOOLS] + (["save_warning_message"] if no_msg else []), DONE,
                check=r.need("search", "먼저 search_posts로 검색해 주세요"))
    def search_posts() -> dict:  # 결과를 이번 실행에 남겨 화면에 돌려준다
        r.state["search"] = r.tools["search_posts"]()
        return r.state["search"]
    tools_extra = [Tool("search_posts", "사업장 이름과 검색어로 공개 게시물을 검색해 새 게시물을 보존한다.", search_posts)]
    r.agent(goal, {"보복 금지 관련 조항": _retaliation_laws()}, tools_extra)
    res = r.state.get("search") or r.call("search_posts")
    judged = _posts_result(r)
    if not r.ai_used and res.get("added"):
        r.call("notify", "새 공개 게시물을 찾았어요", f"게시물 {res['added']}건을 보존했어요. 보호 탭에서 확인해 보세요.")
    return r.done({**res, "classify": judged}, res.get("reason") if res.get("skipped") else f"새 게시물 {res['added']}건")


def run_guard_review(session: Session, user_id: int, job_id: int, trigger: str = "user", r: Run | None = None) -> dict:
    """판별 대기 게시물 판별 (게시물을 보존한 뒤, 또는 AI 응답 대기 중이던 일을 다시 맡길 때).
    AI가 쓴 안내 문구가 아직 없으면 함께 맡긴다."""
    own_run = r is None  # 따로 불렸으면 이 실행의 결과 기록(trace)까지 돌려준다
    r = r or Run(session, user_id, job_id, "guard_review", trigger)
    no_msg = r.tools["get_job"]().reported and not r.tools["get_job"]().guard_ai_message
    if r.tools["pending_posts"]() or no_msg:
        text = (POST_GOAL + (" 아직 AI가 쓴 보복 금지 안내 문구가 없으니 save_warning_message로 함께 저장해 주세요." if no_msg else ""))
        r.agent(Goal(text, POST_TOOLS + (["save_warning_message"] if no_msg else []), DONE),
                {"보복 금지 관련 조항": _retaliation_laws()})
    out = _posts_result(r)
    if own_run:
        return r.done(out, f"판별 {out['judged']}건, 판별 대기 {out['pending']}건")
    return out


def _posts_result(r: Run) -> dict:
    judged = r.state.get("posts", [])
    out = {"judged": len(judged), "pending": r.tools["pending_posts"](), "suspect": sum(p["status"] == "suspect" for p in judged)}
    if out["pending"] and not r.ai_used:
        out["reason"] = f"{AI_WAITING}: {r.ai_error or '판별하지 않고 증거만 보존했어요'}"
    return out


def run_guard_preserve(session: Session, user_id: int, job_id: int, url: str, title: str = "") -> dict:
    r = Run(session, user_id, job_id, "guard_preserve")
    r.log("입력", f"게시물 주소 {url[:120]}")
    res = r.call("preserve_post", url, title)
    judged = run_guard_review(session, user_id, job_id, r=r)
    return r.done({**res, "classify": judged}, "주소, 확인 시각" + (", 화면 캡처" if res["captured"] else "") + " 보존")


# ---------- 매일 종합 조언 ----------
ADVICE_TOOLS = ["get_profile", "get_contract", "get_saved_checks", "calc_work_days", "settlement", "list_evidence",
                "list_posts", "get_answers", "counsel_for_age", "get_article", "find_refs"]


def run_advice(session: Session, user_id: int, job_id: int, trigger: str = "schedule") -> dict:
    """사용자에게서 얻은 기록과 에이전트가 만든 기록을 종합해 조언한다 (매일 한 번).
    지난 종합 조언 뒤로 달라진 기록이 없으면 AI를 부르지 않는다."""
    r = Run(session, user_id, job_id, "advice", trigger)
    key = case.data_key(session, r.tools["get_job"]())
    last = session.exec(select(CaseNote).where(CaseNote.job_id == job_id, CaseNote.kind == "advice",
                                               CaseNote.event == "advice").order_by(CaseNote.id.desc())).first()
    if last and last.basis_key == key:
        return r.done({"advised": False}, "지난 종합 조언 뒤로 달라진 기록이 없어 새로 조언하지 않아요")
    if not client.available():  # 조언은 AI 몫이라 대신 쓰지 않는다
        return r.done({"advised": False}, f"종합 조언은 {AI_WAITING}")
    r.state["basis_key"] = key
    goal = Goal("매일 종합 조언 시간이에요. 상황의 진행 상황, 지난 메모, 질문과 답, 예약한 확인을 보고, 필요하면 도구로 점검 결과와 "
                "기록을 확인해, 이 사용자에게 지금 가장 도움이 될 조언을 give_advice로 남긴 뒤 finish로 끝내 주세요. "
                "조언은 기록에 있는 사실에 근거하고, 법적 판단을 단정하지 마세요.",
                ADVICE_TOOLS, DONE, check=r.need("advice", "먼저 give_advice로 조언을 남겨 주세요"))
    r.agent(goal, {})
    return r.done({"advised": bool(r.state.get("advice"))}, "종합 조언을 남겼어요" if r.state.get("advice")
                  else "종합 조언을 남기지 못했어요")


# ---------- 매일 자동 점검 ----------
def run_daily(session: Session, user_id: int, job_id: int) -> dict:
    """매일 정해진 시각: 오늘 이 사업장에 무엇을 확인하고 알릴지 AI가 정한다. AI가 없으면 정해 둔 조건으로 실행한다."""
    r = Run(session, user_id, job_id, "daily", "schedule")
    today = today_kst()
    last_month = date.fromordinal(today.replace(day=1).toordinal() - 1).strftime("%Y-%m")
    ran: list[str] = []

    def overview() -> dict:
        job, st = r.tools["get_job"](), r.tools["settlement"]()
        return {"오늘": today, "월급날": paycalc.payday_text(job.payday) or "모름", "오늘이 월급날": paycalc.is_payday(job.payday, today), "지난달": last_month,
                "상태": "그만둠" if job.status == "quit" else "일하는 중",
                "퇴직 후 지급 기한까지 남은 날": st["left"] if st else None, "신고함": job.reported,
                "판별 대기 게시물": r.tools["pending_posts"](), "오래된 출근 기록": r.tools["find_open_record"]()}

    def pay_check(month: str) -> dict:
        ran.append("payday")
        out = run_payday(session, user_id, job_id, month, trigger="agent")
        return {"결과": out["compare"]["status"], "사실": out["compare"]["text"]}

    def quit_check() -> dict:
        ran.append("quit")
        st = run_quit_check(session, user_id, job_id, trigger="agent")
        return {"결과": st["status"], "지급 기한": st["due"]} if st else {"안내": "그만둔 사업장이 아니에요"}

    def post_search() -> dict:
        ran.append("guard")
        out = run_guard_search(session, user_id, job_id, trigger="agent")
        return {"새 게시물": out.get("added", 0), "보복 의심": out["classify"]["suspect"]}

    extra = [Tool("get_overview", "오늘 날짜, 월급날, 퇴직 지급 기한, 신고 여부, 판별 대기 게시물을 본다.", overview),
             Tool("run_pay_check", "그 달 급여 점검을 실행한다 (보통 월급날에 지난달).", pay_check,
                  {"month": {"type": "string", "description": "YYYY-MM"}}, ["month"]),
             Tool("run_quit_check", "그만둔 뒤 임금 지급 기한 점검을 실행한다.", quit_check),
             Tool("run_post_search", "신고한 사업장의 공개 게시물 검색과 판별을 실행한다.", post_search)]
    goal = Goal("매일 자동 점검 시간이에요. get_overview로 오늘 상황을 보고, 이 사업장에 오늘 필요한 점검을 골라 실행해 주세요. "
                "필요 없는 점검은 하지 마세요. 알릴 일이 있으면 사용자에게 알린 뒤 finish로 끝내 주세요.",
                ["get_overview", "run_pay_check", "run_quit_check", "run_post_search", "notify"], DONE)
    if r.agent(goal, {}, extra) is None and not ran:
        job = r.tools["get_job"]()
        if job.status == "working" and paycalc.is_payday(job.payday, today):  # 없는 날이면 그 달 마지막 날
            pay_check(last_month)
        if job.status == "quit":
            quit_check()
        if job.reported:
            post_search()
    return r.done({"ran": ran}, "실행한 점검: " + (", ".join(ran) or "없음"))


# ---------- 사용자가 에이전트의 질문에 답했을 때 ----------
RESUME = {"contract_check": "contract_check", "shift_check": "contract_check", "payday": "payday",
          "quit_check": "quit_check", "report": "report", "daily": "daily", "guard_on": "guard_review",
          "guard_search": "guard_review", "guard_preserve": "guard_review", "guard_review": "guard_review"}


# ---------- 매일 자동 점검 결과 알림 ----------
DAILY_KINDS = {"payday": "급여(지난달)", "quit": "퇴직 뒤 임금 지급 기한", "guard": "공개 게시물 검색과 판별"}


def daily_notice(session: Session, user_id: int, job_id: int, ran: list[str], advised: bool) -> str:
    """매일 자동 점검이 끝나면 사업장마다 알림 하나로 결과를 알린다 (점검할 게 없던 날도).
    알림은 코드가 보내고, 조언 글은 AI가 give_advice로 남긴 것을 그대로 옮긴다."""
    t = make_tools(session, user_id, job_id, "daily")
    today = today_kst()
    done = ", ".join(DAILY_KINDS.get(k, k) for k in ran)
    lines = [f"실행한 점검: {done}." if done else "오늘 필요한 점검은 없었어요 (월급날, 그만둔 뒤 지급 기한, 신고 후 게시물 확인에 해당 없음)."]
    adv = case.latest_advice(session, job_id)
    if advised and adv:
        lines.append(f"에이전트 조언: {adv.text}")
    elif not client.available():
        lines.append(f"종합 조언은 {AI_WAITING}이에요.")
    else:
        lines.append("지난 조언 뒤로 달라진 기록이 없어 새 조언은 없어요.")
    lines.append("자세한 결과는 홈의 AI 에이전트 진행 상황에서 볼 수 있어요.")
    return t["notify"](f"오늘 자동 점검 ({today.month}월 {today.day}일)", " ".join(lines))


def run_again(session: Session, user_id: int, job_id: int, kind: str, month: str, trigger: str) -> dict:
    """점검 하나를 다시 시작한다 (질문에 답했을 때, 예약한 확인의 때가 됐을 때)."""
    if kind == "payday":
        return run_payday(session, user_id, job_id, month or today_kst().strftime("%Y-%m"), trigger)
    if kind == "quit_check":
        st = run_quit_check(session, user_id, job_id, trigger)
        return {"settlement": st, "trace": []}
    if kind == "report":
        return run_report(session, user_id, job_id, trigger)
    if kind == "daily":
        return run_daily(session, user_id, job_id)
    if kind == "guard_review":
        r = Run(session, user_id, job_id, "guard_review", trigger)
        return r.done({"classify": run_guard_review(session, user_id, job_id, r=r)}, "게시물 판별 다시")
    return run_contract_check(session, user_id, job_id, trigger)


def run_answer(session: Session, user_id: int, job_id: int, event: str, context: dict) -> dict:
    """답을 받아 질문했던 점검을 다시 시작한다 (퇴근 점검은 그 주만 보므로 계약서 점검으로 이어 간다)."""
    return run_again(session, user_id, job_id, RESUME.get(event, "contract_check"), context.get("month", ""), "answer")


def run_followup(session: Session, task) -> dict:
    """에이전트가 예약한 확인을 실행한다. 예약한 이유를 이번 실행의 상황에 넘긴다."""
    token = FOLLOWUP_NOTE.set(task.note)
    try:
        return run_again(session, task.user_id, task.job_id, task.kind, task.month, "followup")
    finally:
        FOLLOWUP_NOTE.reset(token)
