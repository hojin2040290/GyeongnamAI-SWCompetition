"""사용자에게 보일 AI 글에 서버 안에서만 쓰는 이름(warn, quit_check, give_advice 등)이 섞이면 LLM이 고쳐 쓴다.

실제 사례: '... 확인 필요(warn)로 판단. 10월 12일 quit_check 예약으로 추적 예정.'
에이전트가 일하는 중이면 같은 에이전트에게 돌려보내 다시 쓰게 하고, 돌려보낼 수 없는 글(예전에 저장된 글)은
LLM을 한 번 더 불러 고쳐 쓰게 한다. 코드가 사전으로 바꿔 끼우지 않는다.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.agent import legacy, rewrite
from app.agent.safety import internal_names, output_problem
from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import CaseNote, CheckRun
from tests.fake_agent import FakeAgent, called, reply, said

BAD = "퇴직 임금 지급 기한 내지만 수령 여부가 기록되지 않아 확인 필요(warn)로 판단. 10월 12일 quit_check 예약으로 추적 예정."
GOOD = "퇴직 임금 지급 기한 안이지만 받았는지 기록이 없어 확인 필요로 판단했어요. 10월 12일에 퇴직 정산을 다시 확인해요."


def use(monkeypatch, policy) -> FakeAgent:
    agent = FakeAgent(policy)
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", agent)
    return agent


@pytest.fixture()
def env():
    init_db()
    with TestClient(app) as a:
        email = f"in{uuid.uuid4().hex[:10]}@example.com"
        a.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": "2009-03-02"})
        a.post("/api/auth/login", json={"email": email, "password": "test1234"})
        yield a, a.post("/api/jobs", json={"name": "가상분식 이름점", "wage": 10320, "start_date": "2026-08-01"}).json()["id"]


def test_detects_internal_names_but_not_fake_ai_or_law():
    assert internal_names(BAD) == ["warn", "quit_check"]
    assert "quit_check → 퇴직 정산 확인" in output_problem(BAD)
    assert output_problem(GOOD) is None and output_problem("근로기준법 제36조에 따라 14일 안에 받아야 해요") is None
    assert internal_names(said("get_all_facts 결과로 판단")) == []  # 가짜 AI 글은 무엇을 넘겼는지 보이려고 도구 이름을 적는다


def test_agent_rewrites_its_own_reason_and_advice(env, monkeypatch):
    """에이전트가 판단 이유와 조언에 내부 이름을 쓰면 돌려보내고, 에이전트가 고쳐 쓴 글이 저장된다."""
    a, j = env

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        v = called(done, "get_all_facts")
        if v is None:
            return reply([("get_all_facts", {})])
        if "get_article" not in names:
            return reply([("get_article", {"label": v["관련 조항"][0]})])
        if "give_advice" not in names:
            return reply([("give_advice", {"advice": "give_advice로 남겨요: 10월 12일 quit_check 예약을 확인하세요"})])
        if "give_advice" in json.dumps(called(done, "give_advice") or "", ensure_ascii=False):
            return reply([("give_advice", {"advice": "10월 12일에 퇴직 정산을 다시 확인할게요"})])
        first = len([1 for n, _ in done if n == "finish"]) == 0
        return reply([("finish", {"status": "warn", "law": v["관련 조항"][0], "fact": v["사실"][:200], "reason": BAD if first else GOOD})])
    agent = use(monkeypatch, policy)
    r = a.post(f"/api/jobs/{j}/agent/overview").json()
    sent_back = json.dumps([m for p in agent.payloads for m in p["messages"] if m["role"] == "tool"], ensure_ascii=False)
    assert "quit_check → 퇴직 정산 확인" in sent_back
    ended = [t["detail"] for t in r["trace"] if t["step"] == "AI 끝냄"]
    assert ended and GOOD in ended[-1]  # 에이전트가 고쳐 쓴 판단 이유로 끝냈다
    assert r["overview"].get("ai_reason") in (GOOD, None)  # (법 기준표 상태에 따라 판단 대기일 수 있지만) 고치기 전 글은 저장되지 않는다
    assert BAD not in json.dumps(r["overview"], ensure_ascii=False)
    assert a.get(f"/api/jobs/{j}/overview").json()["advice"]["text"] == "10월 12일에 퇴직 정산을 다시 확인할게요"


def test_llm_rewrites_text_that_cannot_be_sent_back(monkeypatch):
    """돌려보낼 수 없는 글은 LLM을 한 번 더 불러 고쳐 쓰게 한다. 고친 글도 문제면 원래 글을 둔다."""
    seen = []

    def policy(goal, done, tools):
        seen.append(goal)
        return reply(content=GOOD)
    use(monkeypatch, policy)
    assert rewrite.for_user(BAD) == GOOD
    assert "quit_check(뜻: 퇴직 정산 확인)" in seen[0] and BAD in seen[0]
    assert rewrite.for_user(GOOD) == GOOD and len(seen) == 1  # 내부 이름이 없으면 부르지 않는다
    use(monkeypatch, lambda g, d, t: reply(content="여전히 warn이에요"))
    assert rewrite.for_user(BAD) == BAD
    monkeypatch.setattr(client, "LLM_ENABLED", False)
    assert rewrite.for_user(BAD) == BAD  # AI가 없으면 그대로 (AI가 생기면 다시 맡기기가 고친다)


def test_old_saved_texts_are_rewritten_by_llm(env, monkeypatch):
    """예전 코드가 저장한 판단 이유와 조언 속 내부 이름도 LLM이 고쳐 쓴다 (다시 맡기기 작업)."""
    a, j = env
    uid = a.get("/api/me").json()["id"]
    with Session(engine) as s:
        s.add(CheckRun(user_id=uid, job_id=j, kind="quit", created_at=now_kst(),
                       results_json=json.dumps({"status": "warn", "ai_reason": BAD, "text": "지급 기한 10월 11일"}, ensure_ascii=False)))
        s.add(CaseNote(user_id=uid, job_id=j, kind="advice", text=BAD, created_at=now_kst()))
        s.add(CaseNote(user_id=uid, job_id=j, kind="advice", text=said("조언"), created_at=now_kst()))
        s.commit()
    use(monkeypatch, lambda g, d, t: reply(content=GOOD))
    from app import scheduler
    scheduler.retry_waiting()
    with Session(engine) as s:
        run = json.loads(s.exec(select(CheckRun).where(CheckRun.job_id == j, CheckRun.kind == "quit")).first().results_json)
        notes = [n.text for n in s.exec(select(CaseNote).where(CaseNote.job_id == j)).all()]
    assert run["ai_reason"] == GOOD and run["status"] == "warn"  # 결과 값(status)은 코드의 값이라 그대로
    assert GOOD in notes and said("조언") in notes and BAD not in notes
