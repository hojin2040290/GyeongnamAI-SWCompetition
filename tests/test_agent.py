"""에이전트 판단 반복 테스트: AI가 도구를 고르고, 코드가 실행하고, 결과를 보고 다음 행동을 정하는지.

가짜 AI(tests/fake_agent.py)가 도구 호출을 차례로 내고, 반복, 기록, 권한, 검증 장치는 실제 코드가 돈다.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import core, loop
from app.agent.tools import agent_tools
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import GuardPost, Job
from tests.fake_agent import FakeAgent, called, reply, smart_policy


def use(monkeypatch, policy) -> FakeAgent:
    agent = FakeAgent(policy)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", agent)
    return agent


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
    # check_rules → get_article → finish(검증 장치가 돌려보냄) → finish(다시 판단)
    assert r["ai_agent"] and len(agent.payloads) == 4
    assert agent.payloads[0]["tool_choice"] == "auto"
    first = agent.payloads[0]["messages"][1]["content"]
    assert first.startswith("목표:") and "check_rules" in first
    # 두 번째 요청에는 AI가 부른 도구 결과가 들어 있다
    tool_msgs = [m for m in agent.payloads[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["name"] == "check_rules" and "rule_status" not in tool_msgs[0]["content"]
    assert [t["step"] for t in r["trace"]].count("AI 판단 1") == 1
    feedback = json.loads([m for m in agent.payloads[3]["messages"] if m["role"] == "tool"][-1]["content"])
    assert any("위반이 의심돼요" in f["문제"] for f in feedback["검증 장치"])
    night = [i for i in r["items"] if i["law"] == "근로기준법 제70조"][0]
    assert night["status"] == "warn" and night["ai_reason"] == "검증 장치 의견 반영"
    assert any(t["step"] == "검증 장치 1" for t in r["trace"])


def test_reflection_is_limited(env, monkeypatch):
    """AI가 같은 판단을 고집해도 검증 장치는 REFLECT_MAX번만 돌려보내고, 받은 뒤 확인 필요로 되돌린다."""
    a, ja, *_ = env

    def stubborn(goal, done, tools):
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {})])
        return reply([("finish", {"judgments": [{"i": it["i"], "status": "ok", "law": it["조항"],
                                                 "fact": (it["사실"] or ["입력 정보"])[0], "reason": "고집"}
                                                for it in items]})])
    agent = use(monkeypatch, stubborn)
    r = a.post(f"/api/jobs/{ja}/check").json()
    assert len(agent.payloads) == 2 + loop.REFLECT_MAX and r["ai_agent"]
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
    first_result = [m for m in agent.payloads[1]["messages"] if m["role"] == "tool"][0]
    assert "check_rules" in json.loads(first_result["content"])["error"]
    assert r["ai_agent"] and any(t["step"] == "끝내기 전 확인" for t in r["trace"])


def test_unknown_law_citation_is_rejected(env, monkeypatch):
    a, ja, *_ = env

    def policy(goal, done, tools):
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {})])
        return reply([("finish", {"judgments": [{"i": it["i"], "status": "bad", "law": "가상법 제1조",
                                                 "fact": "사실", "reason": "기억으로 쓴 조항"} for it in items]})])
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
    results = [json.loads(m["content"]) for m in agent.payloads[1]["messages"] if m["role"] == "tool"]
    assert "쓸 수 없는 도구" in results[0]["error"] and "입력이 맞지 않아요" in results[1]["error"]


def test_other_users_post_is_blocked(env, monkeypatch):
    """AI가 다른 사용자의 게시물 번호를 넣어도 코드가 막는다."""
    a, ja, b, jb = env
    b.post(f"/api/jobs/{jb}/guard/posts", json={"url": "https://example.com/other", "title": "다른 사람 글"})
    with Session(engine) as s:
        other_id = s.exec(select(GuardPost).where(GuardPost.job_id == jb)).first().id

    def policy(goal, done, tools):
        if "set_post_status" not in [n for n, _ in done]:
            return reply([("set_post_status", {"post_id": other_id, "status": "ok", "reason": "남의 글 바꾸기"})])
        return reply([("finish", {})])
    agent = use(monkeypatch, policy)
    a.post(f"/api/jobs/{ja}/guard/posts", json={"url": "https://example.com/mine", "title": "내 글"})
    res = [json.loads(m["content"]) for m in agent.payloads[-1]["messages"] if m["role"] == "tool"][0]
    assert "게시물이 아니에요" in res["error"]
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
    assert r["ai_agent"] and r["compare"]["ai_law"] == "근로기준법 제36조"
    # 근무 기록이 없어 필요한 정보가 부족하므로 AI가 위반 의심이라 해도 확인 필요로 되돌린다
    assert r["compare"]["status"] == "warn"
    # 급여 화면을 다시 열어도 AI를 다시 부르지 않고 저장된 판단을 보여 준다
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    again = a.get(f"/api/jobs/{ja}/pay?month=2026-08").json()["compare"]
    assert again.get("ai_law") == "근로기준법 제36조"


def test_seek_adds_ai_questions(env, monkeypatch):
    a, *_ = env
    use(monkeypatch, smart_policy)
    r = a.post("/api/seek/check", json={"name": "가상카페", "wage": 12000, "probation": "no",
                                        "schedule": {"금": {"start": "18:00", "end": "22:00", "brk": "없음"}}}).json()
    assert r["ai_agent"] and "주휴수당을 주나요" in r["questions"]


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
