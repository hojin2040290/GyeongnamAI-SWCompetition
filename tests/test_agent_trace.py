"""에이전트 동작 기록: 계획은 끝까지, AI가 고른 도구는 판단 글과 따로 태그로, 줄인 도구 기록은 '…'로 표시.

실제 사례: '도구 make_plan 입력 {..."remember → "계획을 세웠어요"'처럼 기록이 중간에 끊겨 보였고,
'AI 판단 1 도구 선택: make_plan, calc_pay, ...'처럼 고른 도구가 판단 글에 섞였다.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import AgentLog
from tests.fake_agent import FakeAgent, called, reply, said

STEPS = [f"{n}단계: 가상 기록의 계약서 점검(get_saved_checks)과 근무 기록(calc_work_days)을 함께 확인해 판단 근거로 정리" for n in range(1, 9)]


@pytest.fixture()
def env(monkeypatch):
    init_db()

    def policy(goal, done, tools):
        names = [n for n, _ in done]
        if "get_all_facts" not in names:  # 실제 모델처럼 한 번에 계획과 도구 여럿을 고른다
            return reply([("make_plan", {"steps": STEPS}), ("get_all_facts", {}), ("get_saved_checks", {})])
        v = called(done, "get_all_facts")
        if "give_advice" not in names:
            return reply([("give_advice", {"advice": said("조언")})])
        return reply([("finish", {"status": "warn", "law": v["관련 조항"][0], "fact": v["사실"][:200], "reason": said("이유")})])
    monkeypatch.setattr(client, "LLM_FAKE", False)
    monkeypatch.setattr(client, "LLM_ENABLED", True)
    monkeypatch.setattr(client, "LLM_MODEL", "fake")
    monkeypatch.setattr(client, "_post", FakeAgent(policy))
    with TestClient(app) as a:
        email = f"tr{uuid.uuid4().hex[:10]}@example.com"
        a.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": "2009-03-02"})
        a.post("/api/auth/login", json={"email": email, "password": "test1234"})
        j = a.post("/api/jobs", json={"name": "가상분식 기록점", "wage": 10320, "start_date": "2026-08-01"}).json()["id"]
        yield a, j


def test_plan_is_whole_and_tools_are_tags(env):
    a, j = env
    r = a.post(f"/api/jobs/{j}/agent/overview").json()
    for steps in (r["trace"], a.get(f"/api/jobs/{j}/agent/runs?events=overview&limit=1").json()[0]["steps"]):
        plan = [t for t in steps if t["step"] in ("계획", "계획 수정") and "8단계" in t["detail"]]
        assert plan and plan[0]["detail"].endswith(STEPS[-1])  # 8단계까지 끝까지
        ai = [t for t in steps if t["step"].startswith("AI 판단 ")]
        many = next(t for t in ai if "get_saved_checks" in t["tags"])
        assert many["tags"][:3] == ["make_plan", "get_all_facts", "get_saved_checks"]
        assert all("도구 선택" not in t["detail"] for t in ai)  # 판단 글에는 고른 도구를 섞지 않는다
        mk = [t for t in steps if t["step"] == "도구 make_plan"]
        assert mk and all("steps" not in t["detail"] for t in mk)  # 계획을 반쯤 잘라 다시 적지 않는다
        facts = [t["detail"] for t in steps if t["step"] == "도구 get_all_facts"]
        assert facts and facts[0].endswith("}")  # 짧은 결과는 줄이지 않는다
    live = a.get("/api/agent/live?after=0").json()
    assert any(t["tags"] for t in live) and any(t["detail"].endswith(STEPS[-1]) for t in live)


def test_old_records_tool_text_becomes_tags(env):
    """예전 코드가 'AI 판단' 글에 남긴 '도구 선택: a, b'도 태그로 보인다."""
    a, j = env
    uid = a.get("/api/me").json()["id"]
    with Session(engine) as s:
        s.add(AgentLog(user_id=uid, job_id=j, run_id="old-run", event="overview", step="AI 판단 1",
                       detail="도구 선택: make_plan, calc_pay", created_at=now_kst()))
        s.commit()
    run = next(x for x in a.get(f"/api/jobs/{j}/agent/runs?events=overview").json() if x["run_id"] == "old-run")
    assert run["steps"][0]["tags"] == ["make_plan", "calc_pay"] and run["steps"][0]["detail"] == ""
    assert json.dumps(run, ensure_ascii=False).count("도구 선택") == 0


def test_cut_marks_shortened_tool_record():
    """도구 입력과 결과를 기록에 줄여 남길 때는 '…'을 붙인다 (중간에 끊긴 것처럼 보이지 않게)."""
    from app.agent.loop import _cut
    assert _cut("가" * 300, 250) == "가" * 250 + "…" and _cut("짧음", 250) == "짧음"
