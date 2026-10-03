"""홈의 AI 에이전트 종합 점검: 코드가 모든 기록을 정리하고, 에이전트가 종합 판단과 조언, 필요하면 질문을 남긴다."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import core, overview
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import AgentQuestion, Job
from tests.fake_agent import FakeAgent, called, reply, said

JOB = {"name": "가상분식 종합점", "wage": 12000, "size": "lt5", "probation": "no", "payday": 10, "start_date": "2026-08-01",
       "contract_written": True, "copy_received": True, "schedule": {"토": {"start": "18:00", "end": "23:00", "brk": "30분"}}}


def use(monkeypatch, policy) -> FakeAgent:
    agent = FakeAgent(policy)
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", agent)
    return agent


def results(payload: dict, name: str) -> list:
    return [json.loads(m["content"]) for m in payload["messages"] if m["role"] == "tool" and m["name"] == name]


@pytest.fixture()
def env():
    init_db()
    with TestClient(app) as a:
        email = f"ov{id(a)}@example.com"
        a.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": "2009-03-02"})
        a.post("/api/auth/login", json={"email": email, "password": "test1234"})
        j = a.post("/api/jobs", json=JOB).json()["id"]
        yield a, j


def good_policy(status: str = "warn"):
    """get_all_facts → get_article → give_advice → finish (종합 판단)."""
    def policy(goal, done, tools):
        names = [n for n, _ in done]
        view = called(done, "get_all_facts")
        if view is None:
            return reply([("get_all_facts", {})])
        if "get_article" not in names:
            return reply([("get_article", {"label": view["관련 조항"][0]})])
        if "give_advice" not in names:
            return reply([("give_advice", {"advice": said("모든 기록을 보면 근무 기록을 꾸준히 남기세요"), "next_tab": "pay"})])
        return reply([("finish", {"status": status, "law": view["관련 조항"][0], "fact": view["사실"][:200],
                                  "reason": said("종합 판단 이유")})])
    return policy


def test_overview_without_ai_keeps_code_facts_and_waits(env, monkeypatch):
    """AI가 없으면 코드가 정리한 사실만 보여 주고 판단은 'AI 응답 대기 중' (기다려도 바뀌지 않게 AI가 생기면 다시 점검)."""
    a, j = env
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    g = a.get(f"/api/jobs/{j}/overview").json()
    assert g["overview"] is None and g["need"] is False and g["ai"] is False
    names = [p["name"] for p in g["parts"]]
    assert {"계약서 점검", "급여 비교", "근무 기록", "증거 자료"} <= set(names)
    r = a.post(f"/api/jobs/{j}/agent/overview").json()
    assert r["overview"]["status"] == "pending" and "AI 응답 대기 중" in r["overview"]["ai_error"]
    with Session(engine) as s:  # AI가 생기면 다시 맡길 일로 잡힌다
        from app import scheduler
        assert ("overview", "") in scheduler.waiting_work(s, s.get(Job, j))


def test_overview_agent_judges_advises_and_is_saved(env, monkeypatch):
    """에이전트가 모든 기록을 확인해 종합 판단(결과, 이유, 근거 조항, 근거 사실)과 조언을 남기고, 홈 조회에 나온다."""
    a, j = env
    a.post(f"/api/jobs/{j}/payslip", data={"month": "2026-08", "amount": "100000"})
    agent = use(monkeypatch, good_policy())
    r = a.post(f"/api/jobs/{j}/agent/overview").json()
    ov = r["overview"]
    assert r["ai_agent"] and r["advised"] and ov["status"] == "warn"
    assert ov["ai_reason"].startswith("테스트 답변입니다") and ov["ai_law"] and ov["ai_fact"]
    seen = results(agent.payloads[-1], "get_all_facts")[0]
    assert "2026-08 급여" in seen["사실"] and "코드가 정리한 결과" in seen
    g = a.get(f"/api/jobs/{j}/overview").json()
    assert g["overview"]["ai_reason"] == ov["ai_reason"] and g["advice"]["text"].startswith("테스트 답변입니다")
    assert g["advice"]["next_tab"] == "pay"
    assert g["changed"] is False and g["need"] is False  # 끝난 직후에는 '기록이 바뀌었어요'가 아니다
    a.post(f"/api/jobs/{j}/payslip", data={"month": "2026-09", "amount": "50000"})
    assert a.get(f"/api/jobs/{j}/overview").json()["changed"] is True


def test_overview_needs_advice_and_review_sends_back(env, monkeypatch):
    """조언 없이 끝내려 하거나, 코드가 위반 의심으로 본 기록이 있는데 정상이라 하면 돌려보낸다 (검증 장치)."""
    a, j = env
    monkeypatch.setattr(overview, "parts", lambda s, uid, jid: [overview._part("2026-08 급여", "bad", "124,872원 적게 받음", "pay")])
    state = {"n": 0}

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        view = called(done, "get_all_facts")
        if view is None:
            return reply([("get_all_facts", {})])
        if "get_article" not in names:
            return reply([("get_article", {"label": view["관련 조항"][0]})])
        state["n"] += 1
        if state["n"] == 1:  # 조언 없이 정상이라 하고 끝내려 함
            return reply([("finish", {"status": "ok", "law": view["관련 조항"][0], "fact": view["사실"], "reason": said("정상")})])
        if "give_advice" not in names:
            return reply([("give_advice", {"advice": said("받지 못한 금액을 확인하세요")})])
        bad = len([1 for n, r in done if n == "finish"]) < 2
        return reply([("finish", {"status": "ok" if bad else "bad", "law": view["관련 조항"][0], "fact": view["사실"],
                                  "reason": said("종합 판단")})])
    agent = use(monkeypatch, policy)
    r = a.post(f"/api/jobs/{j}/agent/overview").json()
    errors = [x.get("error", "") for p in agent.payloads for x in results(p, "finish")]
    assert any("give_advice" in e for e in errors)
    assert any("위반이 의심" in json.dumps(x, ensure_ascii=False) for p in agent.payloads for x in results(p, "finish"))
    assert r["overview"]["status"] == "bad" and r["overview"]["rule_status"] == "bad"


def test_overview_question_and_answer_reruns_overview(env, monkeypatch):
    """판단에 필요한 정보가 없으면 에이전트가 질문하고, 사용자가 답하면 종합 점검을 다시 한다."""
    a, j = env

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        view = called(done, "get_all_facts")
        if view is None:
            return reply([("get_all_facts", {})])
        if "ask_user" not in names and "답: " not in json.dumps(called(done, "get_answers") or [], ensure_ascii=False) \
                and "get_answers" not in names and "가상 질문" not in goal:
            return reply([("ask_user", {"question": "가상 질문: 쉬는 시간을 실제로 쉬었나요?", "options": ["예", "아니요"],
                                        "why": said("휴게 판단에 필요")})])
        return good_policy()(goal, done, tools)
    use(monkeypatch, policy)
    a.post(f"/api/jobs/{j}/agent/overview")
    with Session(engine) as s:
        q = s.exec(select(AgentQuestion).where(AgentQuestion.job_id == j)).first()
    assert q and q.event == "overview" and q.status == "open"
    assert any(p["name"] == "답을 기다리는 질문" for p in a.get(f"/api/jobs/{j}/overview").json()["parts"])
    r = a.post(f"/api/questions/{q.id}/answer", json={"answer": "예"}).json()
    assert r["event"] == "overview" and r["overview"]["status"] in ("ok", "warn", "bad")


def test_daily_runs_overview_only_when_records_changed(env, monkeypatch):
    """매일 자동 점검: 종합 점검을 하고, 지난 점검 뒤로 바뀐 기록이 없으면 AI를 다시 부르지 않는다."""
    from app import scheduler
    a, j = env
    judge = good_policy()

    def policy(goal, done, tools):  # 매일 점검 자체는 바로 끝내고, 종합 점검만 판단한다
        if "get_all_facts" not in tools:
            return reply([("finish", {"note": said("오늘 할 점검 없음")})])
        return judge(goal, done, tools)
    agent = use(monkeypatch, policy)
    with Session(engine) as s:
        uid = s.get(Job, j).user_id
    scheduler.daily_check(uid)
    assert overview.latest(Session(engine), j)["status"] == "warn"
    n = len([p for p in agent.payloads if any(t["function"]["name"] == "get_all_facts" for t in p.get("tools", []))])
    scheduler.daily_check(uid)
    m = len([p for p in agent.payloads if any(t["function"]["name"] == "get_all_facts" for t in p.get("tools", []))])
    assert n > 0 and m == n


def test_contract_waiting_count_is_separate_for_spinner(env, monkeypatch):
    """AI 판단을 기다리는 계약서 항목 수는 wait 칸으로 따로 둔다 (화면이 도는 표시와 함께 보여 주게). AI에게는 함께 넘긴다."""
    a, j = env
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    a.post(f"/api/jobs/{j}/check")
    part = next(p for p in a.get(f"/api/jobs/{j}/overview").json()["parts"] if p["name"] == "계약서 점검")
    assert part["wait"].startswith("AI 에이전트 판단 대기") and "판단 대기" not in part["text"]
    with Session(engine) as s:
        assert part["wait"] in overview.target(s, s.get(Job, j).user_id, j)["text"]
