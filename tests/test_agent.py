"""에이전트 판단 반복 테스트: AI가 도구를 고르고, 코드가 실행하고, 결과를 보고 다음 행동을 정하는지.

가짜 AI(tests/fake_agent.py)가 도구 호출을 차례로 내고, 반복, 기록, 권한, 검증 장치는 실제 코드가 돈다.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import core, loop
from app.agent.tools import agent_tools, make_tools
from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import AgentQuestion, GuardPost, Job
from tests.fake_agent import FakeAgent, called, reply, said, smart_policy


def use(monkeypatch, policy, plan_first: bool = True) -> FakeAgent:
    agent = FakeAgent(policy, plan_first)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", agent)
    return agent


def results(payload: dict, name: str | None = None) -> list:
    """요청에 담긴 도구 결과 (이름으로 거르기)."""
    return [json.loads(m["content"]) for m in payload["messages"]
            if m["role"] == "tool" and (name is None or m["name"] == name)]


def _login(cl: TestClient, email: str, job: dict) -> int:
    cl.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": "2009-03-02"})
    cl.post("/api/auth/login", json={"email": email, "password": "test1234"})
    return cl.post("/api/jobs", json=job).json()["id"]


JOB = {"name": "가상분식 에이전트점", "wage": 12000, "size": "lt5", "probation": "no", "payday": 10,
       "contract_written": True, "copy_received": True,
       "schedule": {"토": {"start": "18:00", "end": "23:00", "brk": "30분"}}}


@pytest.fixture(scope="module")
def env():
    init_db()
    with TestClient(app) as a, TestClient(app) as b:
        ja = _login(a, "agent_a@example.com", JOB)
        jb = _login(b, "agent_b@example.com", {**JOB, "name": "가상분식 다른사람점"})
        yield a, ja, b, jb


def test_tools_have_no_user_or_job_input():
    """AI가 고르는 도구의 입력에는 사용자 번호와 사업장 번호가 없다 (코드가 고정)."""
    with Session(engine) as s:
        for t in agent_tools(s, 1, 1, {}).values():
            props = t.spec()["function"]["parameters"]["properties"]
            assert not {"user_id", "job_id"} & set(props), t.name


def test_agent_chooses_tools_and_finishes(env, monkeypatch):
    a, ja, *_ = env
    agent = use(monkeypatch, smart_policy)
    r = a.post(f"/api/jobs/{ja}/check").json()
    # make_plan → check_rules → get_article → finish(검증 장치가 돌려보냄) → finish(다시 판단)
    assert r["ai_agent"] and len(agent.payloads) == 5 and r["plan"]
    assert agent.payloads[0]["tool_choice"] == "auto"
    first = agent.payloads[0]["messages"][1]["content"]
    assert first.startswith("목표:") and "check_rules" in first
    # 두 번째 요청에는 AI가 부른 도구 결과가 들어 있다
    tool_msgs = [m for m in agent.payloads[2]["messages"] if m["role"] == "tool"]
    assert tool_msgs[1]["name"] == "check_rules" and "rule_status" not in tool_msgs[1]["content"]
    assert [t["step"] for t in r["trace"]].count("AI 판단 1") == 1
    feedback = results(agent.payloads[4], "finish")[-1]
    assert any("위반이 의심돼요" in f["문제"] for f in feedback["검증 장치"])
    night = [i for i in r["items"] if i["law"] == "근로기준법 제70조"][0]
    assert night["status"] == "warn" and night["ai_reason"] == said("검증 장치 의견 반영")
    assert any(t["step"] == "검증 장치 1" for t in r["trace"])


def test_reflection_is_limited(env, monkeypatch):
    """AI가 같은 판단을 고집해도 검증 장치는 REFLECT_MAX번만 돌려보내고, 받은 뒤 확인 필요로 되돌린다."""
    a, ja, *_ = env

    def stubborn(goal, done, tools):
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {})])
        return reply([("finish", {"judgments": [{"i": it["i"], "status": "ok", "law": it["조항"],
                                                 "fact": (it["사실"] or ["입력 정보"])[0], "reason": said("고집")}
                                                for it in items]})])
    agent = use(monkeypatch, stubborn)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert len(agent.payloads) == 3 + loop.REFLECT_MAX and r["ai_agent"]  # 계획, 항목 받기, 끝내기 + 다시 판단
    night = [i for i in r["items"] if i["law"] == "근로기준법 제70조"][0]
    assert night["status"] == "warn"


def test_step_limit_falls_back_to_waiting(env, monkeypatch):
    a, ja, *_ = env
    agent = use(monkeypatch, lambda goal, done, tools: reply([("get_profile", {})]))
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert len(agent.payloads) == loop.MAX_STEPS and r["ai_agent"] is False
    assert any(t["step"] == "멈춤" for t in r["trace"])
    pend = [i for i in r["items"] if i["status"] == "pending"]
    assert pend and all("반복" in i["ai_error"] for i in pend)


def test_finish_too_early_is_sent_back(env, monkeypatch):
    a, ja, *_ = env

    def policy(goal, done, tools):
        if not done:
            return reply([("finish", {"judgments": []})])
        return smart_policy(goal, [d for d in done if d[0] != "finish"], tools)
    agent = use(monkeypatch, policy)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert "check_rules" in results(agent.payloads[2], "finish")[0]["error"]
    assert r["ai_agent"] and any(t["step"] == "끝내기 전 확인" for t in r["trace"])


def test_plan_is_required_first(env, monkeypatch):
    """계획 없이 도구를 부르면 돌려보내고, 계획을 세운 뒤에야 도구를 실행한다."""
    a, ja, *_ = env

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        if not names:
            return reply([("check_rules", {})])
        if "make_plan" not in names:
            return reply([("make_plan", {"steps": [said("검토 항목 받기"), said("판단하기")]})])
        return smart_policy(goal, [d for d in done if d[0] != "make_plan" and "error" not in d[1]], tools)
    agent = use(monkeypatch, policy, plan_first=False)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert "make_plan" in results(agent.payloads[1], "check_rules")[0]["error"]
    steps = [t["step"] for t in r["trace"]]
    assert steps.index("계획 전 확인") < steps.index("계획") < steps.index("도구 check_rules")
    assert r["ai_agent"] and r["plan"] == [said("검토 항목 받기"), said("판단하기")]


def test_unknown_law_citation_is_rejected(env, monkeypatch):
    a, ja, *_ = env

    def policy(goal, done, tools):
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {})])
        return reply([("finish", {"judgments": [{"i": it["i"], "status": "bad", "law": "가상법 제1조",
                                                 "fact": "사실", "reason": said("기억으로 쓴 조항")} for it in items]})])
    use(monkeypatch, policy)
    items = a.post(f"/api/jobs/{ja}/check").json()["items"]
    assert "bad" not in {i["status"] for i in items}
    assert any("법 기준표에 없어" in i.get("ai_error", "") for i in items)


def test_tool_not_in_goal_and_bad_input_are_errors(env, monkeypatch):
    a, ja, *_ = env

    def policy(goal, done, tools):
        if not done:
            return reply([("delete_everything", {}), ("get_age_on", {"day": "어제"})])
        return smart_policy(goal, [d for d in done if d[0] not in ("delete_everything", "get_age_on")], tools)
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/check")
    assert "쓸 수 없는 도구" in results(agent.payloads[2], "delete_everything")[0]["error"]
    assert "입력이 맞지 않아요" in results(agent.payloads[2], "get_age_on")[0]["error"]


def test_other_users_post_is_blocked(env, monkeypatch):
    """AI가 다른 사용자의 게시물 번호를 넣어도 코드가 막는다."""
    a, ja, b, jb = env
    b.post(f"/api/jobs/{jb}/guard/posts", json={"url": "https://example.com/other", "title": "다른 사람 글"})
    with Session(engine) as s:
        other_id = s.exec(select(GuardPost).where(GuardPost.job_id == jb)).first().id

    def policy(goal, done, tools):
        if "set_post_status" not in [n for n, _ in done]:
            return reply([("set_post_status", {"post_id": other_id, "status": "ok", "reason": said("남의 글 바꾸기")})])
        return reply([("finish", {})])
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/guard/posts", json={"url": "https://example.com/mine", "title": "내 글"})
    assert "게시물이 아니에요" in results(agent.payloads[-1], "set_post_status")[0]["error"]
    with Session(engine) as s:
        assert s.get(GuardPost, other_id).status == "pending"


def test_llm_error_midway_falls_back(env, monkeypatch):
    a, ja, *_ = env
    n = {"calls": 0}

    def flaky(payload):
        n["calls"] += 1
        if n["calls"] == 1:
            return reply([("check_rules", {})])
        raise client.LLMError("AI 모델에 연결하지 못했어요 (ReadTimeout)")
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", flaky)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert r["ai_agent"] is False and any(t["step"] == "AI 응답 없음" for t in r["trace"])
    assert all("ReadTimeout" in i["ai_error"] for i in r["items"] if i["status"] == "pending")


def test_payday_judged_by_agent(env, monkeypatch):
    a, ja, *_ = env
    a.post(f"/api/jobs/{ja}/payslip", data={"month": "2026-08", "amount": "10000"})
    use(monkeypatch, smart_policy)
    with Session(engine) as s:
        r = core.run_payday(s, s.get(Job, ja).user_id, ja, "2026-08")
    # 근무 기록이 없어 비교할 수 없으면 AI에게 체불 판단을 받지 않는다 (추측하지 않고 확인 필요)
    assert r["ai_agent"] and r["compare"]["status"] == "warn" and "ai_law" not in r["compare"]
    assert "근무 기록" in r["compare"]["needed"]
    # 급여 화면을 다시 열어도 저장된 결과를 그대로 보여 준다 (판단 대기가 아님)
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    again = a.get(f"/api/jobs/{ja}/pay?month=2026-08").json()["compare"]
    assert again["status"] == "warn"


def test_seek_adds_ai_questions(env, monkeypatch):
    a, *_ = env
    use(monkeypatch, smart_policy)
    r = a.post("/api/seek/check", json={"name": "가상카페", "wage": 12000, "probation": "no",
                                        "schedule": {"금": {"start": "18:00", "end": "22:00", "brk": "없음"}}}).json()
    assert r["ai_agent"] and said("주휴수당을 주나요") in r["questions"]


def test_daily_agent_picks_checks(env, monkeypatch):
    a, ja, *_ = env
    a.post(f"/api/jobs/{ja}/guard", json={"reported": True})
    use(monkeypatch, smart_policy)
    with Session(engine) as s:
        job = s.get(Job, ja)
        r = core.run_daily(s, job.user_id, ja)
    assert r["ai_agent"] and r["ran"] == ["guard"]
    a.post(f"/api/jobs/{ja}/guard", json={"reported": False})


def test_retry_waiting_hands_work_back_to_ai(env, monkeypatch):
    """AI 응답 대기 중이던 점검을 AI가 연결되면 다시 맡기고, 실패가 이어지면 하루 횟수까지만 맡긴다."""
    from app import scheduler
    a, ja, *_ = env
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    assert any(i["status"] == "pending" for i in a.post(f"/api/jobs/{ja}/check").json()["items"])
    assert "skipped" in scheduler.retry_waiting()

    def down(payload):
        raise client.LLMError("AI 모델에 연결하지 못했어요 (ConnectError)")
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", down)
    tries = [scheduler.retry_waiting()["retried"] for _ in range(scheduler.AI_RETRY_PER_DAY + 1)]
    mine = [sum(x.startswith(f"{ja}:contract_check") for x in t) for t in tries]
    assert mine == [1] * scheduler.AI_RETRY_PER_DAY + [0]  # 하루 횟수를 넘으면 더 맡기지 않음


def test_retry_waiting_completes_when_ai_answers(env, monkeypatch):
    from app import scheduler
    a, ja, b, jb = env
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    b.post(f"/api/jobs/{jb}/check")  # 다른 사용자 사업장의 대기 중 점검
    use(monkeypatch, smart_policy)
    assert f"{jb}:contract_check" in scheduler.retry_waiting()["retried"]
    items = b.get(f"/api/jobs/{jb}/check").json()["items"]
    assert "pending" not in {i["status"] for i in items}


def test_live_steps_while_agent_runs(env):
    a, ja, *_ = env
    last = a.get("/api/agent/last").json()["id"]
    a.post(f"/api/jobs/{ja}/check")
    rows = a.get(f"/api/agent/live?after={last}").json()
    assert rows[0]["step"] == "시작" and rows[0]["event"] == "contract_check" and all(r["id"] > last for r in rows)


def test_case_memory_and_advice(env, monkeypatch):
    """에이전트가 남긴 메모는 다음 실행에 넘어가고, 조언은 홈에 보인다."""
    a, ja, *_ = env

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        if "remember" not in names:
            return reply([("remember", {"note": said("야간근로 인가 여부를 아직 모름")}),
                          ("give_advice", {"advice": said("사업장에 야간근로 인가를 받았는지 물어보세요"), "next_tab": "check"})])
        return smart_policy(goal, [d for d in done if d[0] not in ("remember", "give_advice")], tools)
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/check")
    st = a.get(f"/api/jobs/{ja}/case").json()
    assert st["advice"]["text"] == said("사업장에 야간근로 인가를 받았는지 물어보세요") and st["advice"]["next_tab"] == "check"
    assert st["memory"][-1]["기억"] == said("야간근로 인가 여부를 아직 모름")
    assert [p["name"] for p in st["progress"]] == ["점검", "기록", "급여", "상담 자료", "신고", "보호"]
    assert st["progress"][0]["done"]
    # 다음 실행: 시작 상황에 지난 메모와 진행 상황이 들어 있다
    agent.payloads.clear()
    a.post(f"/api/jobs/{ja}/check")
    first = agent.payloads[0]["messages"][1]["content"]
    assert "지난 메모" in first and said("야간근로 인가 여부를 아직 모름") in first and "진행 상황" in first
    tool_names = [t["function"]["name"] for t in agent.payloads[0]["tools"]]
    assert {"remember", "give_advice", "make_plan"} <= set(tool_names)


def test_bad_advice_tab_is_rejected(env, monkeypatch):
    a, ja, *_ = env

    def policy(goal, done, tools):
        if "give_advice" not in [n for n, _ in done]:
            return reply([("give_advice", {"advice": said("조언"), "next_tab": "admin"})])
        return reply([("finish", {"note": said("끝")})])
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/guard/posts", json={"url": "https://example.com/advice", "title": "글"})
    assert "next_tab" in results(agent.payloads[-1], "give_advice")[0]["error"]


def test_ask_user_then_resume_with_answer(env, monkeypatch):
    """정보가 없으면 AI가 사용자에게 묻고, 답하면 같은 점검을 다시 시작해 답을 근거로 판단한다."""
    a, ja, b, jb = env
    Q = said("보호자 동의서와 가족관계증명서를 사업장에 냈나요?")

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {}), ("get_answers", {})])
        answers = (called(done, "get_answers") or {}).get("답변", [])
        docs = [it for it in items if it["조항"] == "근로기준법 제66조"][0]
        if not answers and "ask_user" not in names:
            return reply([("ask_user", {"question": Q, "options": ["냈어요", "안 냈어요"], "why": said("서류 제출 여부가 기록에 없어요"),
                                        "law": "근로기준법 제66조"})])
        judgments = [{"i": it["i"], "status": "warn", "law": it["조항"], "fact": (it["사실"] or ["입력 정보"])[0],
                      "reason": said("판단 이유")} for it in items if it["i"] != docs["i"]]
        if answers:
            judgments.append({"i": docs["i"], "status": "ok", "law": "근로기준법 제66조", "fact": "사용자가 서류를 냈다고 답함",
                              "reason": said("답을 근거로 판단"), "answer_ids": [answers[0]["answer_id"]]})
        else:
            judgments.append({"i": docs["i"], "status": "warn", "law": "근로기준법 제66조", "fact": "서류 제출 여부 모름",
                              "reason": said("답을 기다려요")})
        return reply([("finish", {"judgments": judgments})])
    use(monkeypatch, policy)
    # 다른 사용자 사업장에 서류 제출 여부가 없도록 비워 둔 상태에서 시작
    r = a.post(f"/api/jobs/{ja}/check").json()
    docs = [i for i in r["items"] if i["law"] == "근로기준법 제66조"][0]
    assert docs["status"] == "warn"
    qs = a.get(f"/api/jobs/{ja}/questions").json()
    assert qs[-1]["question"] == Q and "모름" in qs[-1]["options"]
    assert any(n["title"] == "에이전트가 물어볼 게 있어요" for n in a.get("/api/notifications").json())
    # 같은 질문은 두 번 만들지 않는다
    a.post(f"/api/jobs/{ja}/check")
    assert len([q for q in a.get(f"/api/jobs/{ja}/questions").json() if q["question"] == Q]) == 1
    # 다른 사용자는 이 질문에 답할 수 없다
    assert b.post(f"/api/questions/{qs[-1]['id']}/answer", json={"answer": "안 냈어요"}).status_code == 404
    res = a.post(f"/api/questions/{qs[-1]['id']}/answer", json={"answer": "냈어요"}).json()
    assert res["event"] == "contract_check" and res["ai_agent"]
    docs = [i for i in res["items"] if i["law"] == "근로기준법 제66조"][0]
    assert docs["status"] == "ok" and any("사용자 답변" in b_ for b_ in docs["basis"])
    assert a.get(f"/api/jobs/{ja}/questions").json() == []


def test_unknown_answer_does_not_fill_info(env, monkeypatch):
    """'모름'이라고 답한 질문은 부족한 정보를 채운 것으로 보지 않는다."""
    a, ja, *_ = env
    with Session(engine) as s:
        job = s.get(Job, ja)
        q = AgentQuestion(user_id=job.user_id, job_id=ja, event="contract_check", question="연장 합의를 했나요?",
                          status="answered", answer="모름", created_at=now_kst())
        s.add(q)
        s.commit()
        t = make_tools(s, job.user_id, ja)
        item = {"law": "근로기준법 제69조", "status": "pending", "rule_status": "warn", "text": "", "basis": ["주 38시간"],
                "needed": ["연장 합의 여부"]}
        out = t["apply_judgments"]([item], [{"i": 0, "status": "ok", "law": "근로기준법 제69조", "fact": "주 38시간",
                                             "reason": "", "answer_ids": [q.id]}])
    assert out[0]["status"] == "warn"


def test_followup_scheduled_run_and_cancel(env, monkeypatch):
    """에이전트가 후속 확인을 예약하고, 때가 되면 스케줄러가 예약한 이유와 함께 다시 시작한다."""
    from datetime import timedelta
    from app import scheduler
    from app.calc.timeutil import today_kst
    from app.models import AgentTask
    a, ja, b, jb = env
    tomorrow, far = (today_kst() + timedelta(days=1)).isoformat(), (today_kst() + timedelta(days=90)).isoformat()

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        if "schedule_followup" not in names:
            return reply([("schedule_followup", {"check": "contract_check", "day": far, "note": said("너무 먼 날")}),
                          ("schedule_followup", {"check": "payday", "day": tomorrow, "note": said("명세서 없음")}),
                          ("schedule_followup", {"check": "contract_check", "day": tomorrow,
                                                 "note": said("야간근로 인가 여부를 다시 확인")})])
        return smart_policy(goal, [d for d in done if d[0] != "schedule_followup"], tools)
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/check")
    errs = results(agent.payloads[2], "schedule_followup")
    assert "60일" in errs[0]["error"] and "month" in errs[1]["error"] and errs[2]["task_id"]
    fu = a.get(f"/api/jobs/{ja}/case").json()["followups"]
    assert len(fu) == 1 and fu[0]["이유"] == said("야간근로 인가 여부를 다시 확인")
    # 때가 되면 실행: 시작 상황에 예약한 이유가 들어간다
    with Session(engine) as s:
        task = s.get(AgentTask, fu[0]["task_id"])
        task.due_at = now_kst() - timedelta(minutes=1)
        s.add(task)
        s.commit()
    agent2 = use(monkeypatch, smart_policy)
    assert fu[0]["task_id"] in scheduler.run_due_followups()["followups"]
    first = agent2.payloads[0]["messages"][1]["content"]
    assert "지난번에 예약한 확인이에요: " + said("야간근로 인가 여부를 다시 확인") in first
    assert a.get(f"/api/jobs/{ja}/case").json()["followups"] == []
    assert scheduler.run_due_followups()["followups"] == []  # 같은 확인을 되풀이하지 않음
    # 사용자가 취소 (다른 사용자는 못 함)
    use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/check")
    tid = a.get(f"/api/jobs/{ja}/case").json()["followups"][0]["task_id"]
    assert b.delete(f"/api/followups/{tid}").status_code == 404
    assert a.delete(f"/api/followups/{tid}").json()["ok"]
    assert a.get(f"/api/jobs/{ja}/case").json()["followups"] == []


def test_daily_combined_advice(env, monkeypatch):
    """매일 종합 조언: 기록을 모두 넘기고 조언을 남겨야 끝나며, 달라진 기록이 없으면 AI를 다시 부르지 않는다."""
    from app.models import CaseNote
    a, ja, *_ = env
    with Session(engine) as s:
        uid = s.get(Job, ja).user_id
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    with Session(engine) as s:
        out = core.run_advice(s, uid, ja)
    assert out["advised"] is False and "AI 응답 대기 중" in out["trace"][-1]["detail"]

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        if "finish" not in names:  # 조언 없이 끝내려 하면 돌려보내는지 본다
            return reply([("finish", {"note": said("조언 없이 끝냄")})])
        if "give_advice" not in names:
            return reply([("give_advice", {"advice": said("근무 기록과 받은 급여를 보면 기록을 꾸준히 남기는 게 좋아요")})])
        return reply([("finish", {"note": said("조언 남김")})])
    agent = use(monkeypatch, policy)
    with Session(engine) as s:
        out = core.run_advice(s, uid, ja)
    assert out["advised"] and "give_advice" in results(agent.payloads[2], "finish")[0]["error"]
    first = agent.payloads[0]["messages"][1]["content"]
    assert all(k in first for k in ("진행 상황", "지난 메모", "질문과 답", "예약한 확인"))
    with Session(engine) as s:
        note = s.exec(select(CaseNote).where(CaseNote.job_id == ja, CaseNote.kind == "advice")
                      .order_by(CaseNote.id.desc())).first()
        assert note.event == "advice" and note.basis_key
    # 달라진 기록이 없으면 AI를 부르지 않는다
    n = len(agent.payloads)
    with Session(engine) as s:
        out = core.run_advice(s, uid, ja)
    assert out["advised"] is False and len(agent.payloads) == n
    # 사용자 기록이 생기면 다시 조언한다
    a.post(f"/api/jobs/{ja}/payslip", data={"month": "2026-07", "amount": "30000"})
    agent.payloads.clear()
    with Session(engine) as s:
        assert core.run_advice(s, uid, ja)["advised"]
    assert agent.payloads


def test_daily_check_counts_advice(env, monkeypatch):
    from app import scheduler
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    monkeypatch.setattr(scheduler, "refresh_law_table", lambda: "건너뜀")
    assert scheduler.daily_check()["advice"] == 0  # AI가 없으면 종합 조언은 대기


def test_other_user_cannot_touch_payslip(env):
    a, ja, b, jb = env
    a.post(f"/api/jobs/{ja}/payslip", data={"month": "2026-06", "amount": "1000"})
    pid = [p for p in a.get(f"/api/jobs/{ja}/payslips").json() if p["month"] == "2026-06"][0]["id"]
    assert b.put(f"/api/payslips/{pid}", json={"amount": 1}).status_code == 404
    assert b.delete(f"/api/payslips/{pid}").status_code == 404
    assert a.delete(f"/api/payslips/{pid}").json()["ok"]


def test_bad_tool_input_goes_back_to_ai(env, monkeypatch):
    """실제 모델처럼 도구 입력 모양이 틀려도 요청이 멈추지 않고, AI에게 오류로 돌려준다."""
    a, ja, b, jb = env
    bad = {"label": ["근로기준법"], "month": 202608, "post_id": [1], "text": {"a": 1}, "steps": "계획"}

    def policy(goal, done, tools):
        if len(done) < 6:
            name = [t for t in tools if t not in ("finish", "make_plan")][len(done) % 3]
            return reply([(name, bad)])
        return reply([("finish", {"judgments": "모두 정상", "status": 3, "note": ["x"]})])
    agent = use(monkeypatch, policy)
    for method, url in (("post", f"/api/jobs/{ja}/check"), ("post", f"/api/jobs/{ja}/agent/payday?month=2026-08"),
                        ("post", f"/api/jobs/{ja}/report"), ("post", "/api/seek/check")):
        r = getattr(a, method)(url, json={"wage": 9000} if "seek" in url else None)
        assert r.status_code == 200, (url, r.text)
    errors = [x for p in agent.payloads for x in results(p) if isinstance(x, dict) and "error" in x]
    assert any("도구 입력이 맞지 않아요" in e["error"] for e in errors)


def test_evidence_list_shows_all_saved(env):
    """자료 탭에는 올린 파일, 사업장 등록 전 공고, 주소만 보존한 게시물이 모두 보인다."""
    a, ja, b, jb = env
    a.post("/api/evidence", data={"kind": "notice"}, files={"file": ("공고.png", b"png", "image/png")})
    with Session(engine) as s:
        s.add(GuardPost(job_id=ja, url="https://example.com/p/1", title="가상 게시물", source="user", found_at=now_kst()))
        s.commit()
    evs = a.get(f"/api/jobs/{ja}/evidence").json()
    assert any(e["kind"] == "notice" and e["before_job"] for e in evs)
    assert any(e["kind"] == "post_link" and e["note"] == "https://example.com/p/1" and not e["file"] for e in evs)
    assert not any(e["kind"] == "notice" for e in b.get(f"/api/jobs/{jb}/evidence").json())  # 다른 사람 것은 안 보임


def test_daily_check_always_notifies(env, monkeypatch):
    """매일 자동 점검은 할 점검이 없던 날도 사업장마다 결과를 알림으로 보낸다 (AI가 없으면 조언은 대기라고 알림)."""
    from app import scheduler
    a, ja, *_ = env
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    monkeypatch.setattr(scheduler, "refresh_law_table", lambda: "건너뜀")
    with Session(engine) as s:
        uid = s.get(Job, ja).user_id
    out = scheduler.daily_check(uid)
    assert out["notified"] >= 1
    notes = [n for n in a.get("/api/notifications").json() if n["title"].startswith("오늘 자동 점검")]
    assert notes and "AI 응답 대기 중" in notes[0]["body"] and ("실행한 점검" in notes[0]["body"] or "필요한 점검은 없었어요" in notes[0]["body"])


def test_last_turn_forces_finish(env, monkeypatch):
    """도구만 계속 부르는 모델도 마지막 반복에는 finish만 낼 수 있어, 판단 없이 '반복 초과'로 멈추지 않는다."""
    a, ja, *_ = env

    def wanders(goal, done, tools):
        if tools == ["finish"]:  # 마지막 반복: finish만 받은 경우
            items = called(done, "check_rules") or []
            return reply([("finish", {"judgments": [{"i": it["i"], "status": "warn", "law": it["조항"],
                                                      "fact": said("사실"), "reason": said("판단 이유")} for it in items]})])
        if "check_rules" not in [n for n, _ in done]:
            return reply([("check_rules", {})])
        return reply([("get_profile", {})])  # 끝내지 않고 계속 돌아다닌다
    agent = use(monkeypatch, wanders)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert len(agent.payloads) == loop.MAX_STEPS and r["ai_agent"] is True
    assert agent.payloads[-1]["tool_choice"] == {"type": "function", "function": {"name": "finish"}}
    texts = [m["content"] for m in agent.payloads[-2]["messages"] if m["role"] == "user"]
    assert any("남았어요" in t for t in texts)  # 마무리 알림
    assert not any("반복" in (i.get("ai_error") or "") for i in r["items"])


def test_bad_without_rule_basis_is_sent_back(env):
    """코드 계산으로 문제가 없는데(더 받음) 위반 의심이라 하면 검증 장치가 다시 판단하게 한다."""
    from app.agent.tools import judgment_problems
    with Session(engine) as s:
        probs = judgment_problems(s, {"rule_status": "ok", "text": "계산한 금액보다 10,000원 더 받았어요."},
                                  {"status": "bad", "law": "최저임금법 제5조", "fact": said("사실")})
    assert any("코드 계산과 기록으로는 문제가 확인되지 않았어요" in p for p in probs)


def test_paid_after_quit_is_not_violation(env, monkeypatch):
    """그만둔 뒤 남은 임금을 받았다고 기록했으면, 기한이 지났어도 AI가 위반 의심이라 해 그대로 받지 않는다."""
    from app.agent.tools import saved_settlement
    from app.models import CheckRun
    a, *_ = env
    j = a.post("/api/jobs", json={**JOB, "name": "가상분식 정산점"}).json()["id"]
    a.post(f"/api/jobs/{j}/quit", json={"quit_date": "2026-08-01", "check": False})  # 기한이 지난 날
    a.post(f"/api/jobs/{j}/paid", json={"paid": True, "check": False})

    def stubborn(goal, done, tools):  # 받았다는 기록을 보고도 계속 위반 의심이라 한다
        st = called(done, "settlement")
        if st is None:
            return reply([("settlement", {})])
        return reply([("finish", {"status": "bad", "law": st["관련 조항"][0], "fact": said("지급 기한이 지남"),
                                  "reason": said("위반 의심")})])
    agent = use(monkeypatch, stubborn)
    st = a.post(f"/api/jobs/{j}/agent/quit").json()["settlement"]
    seen = results(agent.payloads[-1], "settlement")[0]
    assert "받았다고 기록했어요" in seen["받음 여부"] and "지났어요" in seen["사실"]  # AI가 받은 사실을 본다
    assert any("남은 임금을 받았다고 기록함" in json.dumps(m, ensure_ascii=False)
               for p in agent.payloads for m in p["messages"] if m["role"] == "tool")  # 검증 장치가 돌려보냄
    assert st["status"] == "ok" and not st.get("ai_reason")
    assert a.get(f"/api/jobs/{j}/settlement").json()["settlement"]["status"] == "ok"
    # 예전 코드가 저장한 '받았는데 위반 의심'은 화면에 쓰지 않고 다시 판단한다
    with Session(engine) as s:
        s.add(CheckRun(user_id=s.get(Job, j).user_id, job_id=j, kind="quit", created_at=now_kst(),
                       results_json=json.dumps({**st, "status": "bad", "ai_reason": said("예전 판단")}, ensure_ascii=False)))
        s.commit()
        assert saved_settlement(s, j, {**st, "status": "pending"})["status"] != "bad"


def test_old_pay_judgment_without_records_is_not_shown(env):
    """근무 기록이 없는 달에 예전 코드가 저장한 체불 판단은 급여 화면에 다시 쓰지 않는다 (지금 규칙: 정보가 없으면 AI 판단 안 받음)."""
    from app.models import CheckRun
    a, *_ = env
    j = a.post("/api/jobs", json={**JOB, "name": "가상분식 예전판단점"}).json()["id"]
    a.post(f"/api/jobs/{j}/payslip", data={"month": "2026-10", "amount": "557280", "check": "false"})
    now = a.get(f"/api/jobs/{j}/pay?month=2026-10").json()
    old = {k: v for k, v in now.items() if k != "ai"}
    old["compare"] = {**now["compare"], "ai_reason": said("체불이 의심됩니다"), "ai_law": "근로기준법 제55조", "ai_fact": said("계산 0원")}
    with Session(engine) as s:
        s.add(CheckRun(user_id=s.get(Job, j).user_id, job_id=j, kind="payday", created_at=now_kst(),
                       results_json=json.dumps(old, ensure_ascii=False, default=str)))
        s.commit()
    shown = a.get(f"/api/jobs/{j}/pay?month=2026-10").json()["compare"]
    assert shown["status"] == "warn" and not shown.get("ai_reason") and not shown.get("ai_law")
    assert "근무 기록" in shown["needed"]


def test_quit_paid_question_does_not_loop(env, monkeypatch):
    """퇴직 정산은 받았는지를 질문으로 묻지 않고(받음 여부는 전용 버튼으로 기록), 버튼을 누르면 열린 정산 질문이 닫힌다.
    예전에는 질문에 '받았어요'로 답해도 받음 여부가 기록되지 않아 다시 점검할 때 같은 질문이 또 생겼다."""
    from app.models import AgentQuestion
    a, *_ = env
    j = a.post("/api/jobs", json={**JOB, "name": "가상분식 질문점"}).json()["id"]
    a.post(f"/api/jobs/{j}/quit", json={"quit_date": "2026-08-01", "check": False})

    def asks(goal, done, tools):  # 받았는지 기록이 없으면 묻는 모델
        st = called(done, "settlement")
        if st is None:
            return reply([("settlement", {})])
        if "ask_user" in tools and "기록하지 않았어요" in st["받음 여부"] and not called(done, "ask_user"):
            return reply([("ask_user", {"question": said("임금을 받았나요"), "options": ["받았어요", "받지 못했어요"],
                                        "why": said("기한이 지남")})])
        return reply([("finish", {"status": "warn", "law": st["관련 조항"][0], "fact": said("받았는지 기록 없음"),
                                  "reason": said("확인 필요")})])
    use(monkeypatch, asks)
    open_qs = lambda: [q for q in a.get(f"/api/jobs/{j}/questions").json() if q["status"] == "open"]  # noqa: E731
    a.post(f"/api/jobs/{j}/agent/quit")
    assert not open_qs()  # 받음 여부는 질문으로 묻지 않는다
    # 예전 코드가 만든 정산 질문이 남아 있어도, 버튼으로 받았다고 기록하면 닫힌다
    with Session(engine) as s:
        s.add(AgentQuestion(user_id=s.get(Job, j).user_id, job_id=j, event="quit_check", question=said("임금을 받았나요"),
                            options_json='["받았어요","모름"]', why=said("기한이 지남"), status="open", created_at=now_kst()))
        s.commit()
    assert open_qs()
    a.post(f"/api/jobs/{j}/paid", json={"paid": True, "check": False})
    assert not open_qs()


def _guard_policy(agent_ref: list, mode: str):
    """실제 모델 기록(신고했어요)처럼 다른 도구만 부르다가 안내 문구 없이 finish를 내려 한다.
    mode "save_finish": '아직 할 일' 안내를 받으면 안내 문구 저장과 finish를 함께 낸다.
    mode "late_save_only": 첫 안내는 무시하고 finish만 내고, 마지막 반복에야 안내 문구만 저장한다 (finish 없음)."""
    from app.agent.core import _retaliation_laws
    save = ("save_warning_message", {"message": said("보복 금지 안내 문구"), "laws": _retaliation_laws()})

    def policy(goal, done, tools):
        last_user = [m["content"] for m in agent_ref[0].payloads[-1]["messages"] if m["role"] == "user"][-1]
        final = len(agent_ref[0].payloads) == loop.MAX_STEPS
        if "아직 할 일" in last_user and "save_warning_message" in tools:
            if mode == "save_finish":
                return reply([save, ("finish", {"note": said("한 일")})])
            if final:
                return reply([save])
        if tools == ["finish"] or "남았어요" in last_user:
            return reply([("finish", {"note": said("한 일")})])  # 안내 문구를 저장하지 않고 끝내려 함
        return reply([("get_profile", {})])
    return policy


@pytest.mark.parametrize("mode", ["save_finish", "late_save_only"])
def test_guard_on_does_not_stop_when_required_tool_left(env, monkeypatch, mode):
    """신고했어요: 꼭 할 일(안내 문구 저장)이 남은 채 반복이 끝나 가면 남은 일을 알려 주고, 마지막 반복에도 그 도구를 쓸 수 있게 한다.
    (예전에는 마지막 반복에 finish만 강제해 '끝내기 전 확인'에 걸려 '반복 10회 안에 끝내지 못했어요'로 멈췄다)"""
    a, ja, *_ = env
    ref = []
    agent = use(monkeypatch, _guard_policy(ref, mode))
    ref.append(agent)
    a.post(f"/api/jobs/{ja}/guard", json={"reported": True})
    with Session(engine) as s:
        job = s.get(Job, ja)
        saved = job.guard_ai_message
        job.reported, job.guard_ai_message = False, ""
        s.add(job)
        s.commit()
    assert saved.startswith("테스트 답변입니다")  # AI가 쓴 문구가 저장됨 (기본 문구로 대기하지 않음)
    texts = [m["content"] for p in agent.payloads for m in p["messages"] if m["role"] == "user"]
    assert any("아직 할 일" in t and "save_warning_message" in t for t in texts)
    if mode == "late_save_only":  # 마지막 반복에 할 일이 남아 finish만 강제하지 않았다
        assert len(agent.payloads) == loop.MAX_STEPS and agent.payloads[-1].get("tool_choice") == "auto"


def test_ask_user_does_not_ask_law_text(env):
    """법 조문 내용은 사용자에게 묻지 않는다 (실제 모델이 기준표에 없는 조문을 사용자에게 물은 일이 있었다)."""
    _, ja, *_ = env
    with Session(engine) as s:
        ask = agent_tools(s, s.get(Job, ja).user_id, ja, {})["ask_user"].fn
        with pytest.raises(ValueError):
            ask("근로기준법 제104조 조문 내용을 알려주세요", ["모름"], said("조문 확인"))


def test_copy_law_table_to_test_data(tmp_path):
    """시험 데이터에 평소 DB의 법 기준표를 복사한다 (없으면 get_article이 모두 '미구축')."""
    import sqlite3

    from sqlmodel import SQLModel, create_engine

    from app import demo_db
    src, dst = tmp_path / "app.db", tmp_path / "test.db"
    for p in (src, dst):
        SQLModel.metadata.create_all(create_engine(f"sqlite:///{p}"))
    cols = [r[1] for r in sqlite3.connect(src).execute("pragma table_info(lawarticle)")]
    row = {c: None for c in cols if c != "id"}
    row.update(law_name="근로기준법", article_no="제104조", title="가상 제목", text="가상 조문", fetched_at="2026-10-01 00:00:00")
    with sqlite3.connect(src) as c:
        c.execute(f"insert into lawarticle ({','.join(row)}) values ({','.join('?' * len(row))})", list(row.values()))
    assert demo_db.copy_law_table(dst, src) == 1
    assert sqlite3.connect(dst).execute("select count(*) from lawarticle").fetchone()[0] == 1


def test_prompt_says_not_to_ask_law_text():
    """실제 모델이 법 기준표에 없는 조문 내용을 사용자에게 물은 일이 있었다: 묻지 말라는 규칙이 프롬프트에 있어야 한다."""
    assert "법 조문 내용은 사용자에게 묻지 마세요. 기준표에 없으면 조항 이름만 근거로 쓰고 이어 하세요." in loop.AGENT_SYSTEM
