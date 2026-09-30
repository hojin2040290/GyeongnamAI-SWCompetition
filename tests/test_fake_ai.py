"""가짜 AI 모드(LLM_FAKE=true): 실제 모델 없이 에이전트 흐름이 끝까지 돌고, AI 몫의 글은 모두 '테스트 답변입니다 (...)'."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine, init_db
from app.llm import client, fake
from app.main import app
from app.models import AgentLog, Notification

SAMPLE = Path(__file__).resolve().parent.parent / "테스트자료"


@pytest.fixture()
def c(monkeypatch):
    monkeypatch.setattr(client, "LLM_FAKE", True)
    init_db()
    with TestClient(app) as cl:
        cl.post("/api/auth/register", json={"email": "fake.mode@example.com", "password": "test1234", "birth_date": "2009-05-01"})
        cl.post("/api/auth/login", json={"email": "fake.mode@example.com", "password": "test1234"})  # 이미 가입했으면
        yield cl


def test_fake_ai_runs_every_flow(c):
    j = c.post("/api/jobs", json={"name": "가상분식 가짜AI점", "wage": 9000, "payday": 31, "start_date": "2026-08-01",
                                  "schedule": {"월": {"start": "17:00", "end": "22:30", "brk": "없음"}}}).json()["id"]
    assert c.get("/api/ai/status").json()["fake"] is True
    photo = (SAMPLE / "02_근로계약서.png").read_bytes()
    r = c.post(f"/api/jobs/{j}/contract", files={"file": ("계약서.png", photo, "image/png")}).json()
    assert r["ai"] and all(v.startswith(fake.PREFIX) for v in r["fields"].values())
    r = c.post(f"/api/jobs/{j}/payslip/read", files={"file": ("명세서.png", photo, "image/png")}).json()
    assert r["ai"] and r["net_pay"] is None  # 금액은 지어내지 않는다
    r = c.post(f"/api/jobs/{j}/check").json()
    assert r["ai_agent"] and r["plan"][0].startswith(fake.PREFIX)
    assert all(i["status"] == "warn" and i["ai_reason"].startswith(fake.PREFIX) for i in r["items"] if i.get("ai_reason"))
    r = c.post(f"/api/jobs/{j}/payslip", data={"month": "2026-09", "amount": "300000"}).json()
    assert r["ai_agent"]
    r = c.post(f"/api/jobs/{j}/report").json()
    assert r["summary_ai"] and fake.PREFIX in c.get(r["url"]).text
    g = c.post(f"/api/jobs/{j}/guard", json={"reported": True}).json()
    assert g["message"].startswith(fake.PREFIX)
    c.post(f"/api/jobs/{j}/guard/posts", json={"url": "https://example.com/fake-post", "title": "가상 게시물"})
    post = [p for p in c.get(f"/api/jobs/{j}/guard").json()["posts"] if p["url"].endswith("fake-post")][0]
    assert post["status"] == "unclear" and post["ai_reason"].startswith(fake.PREFIX)
    c.post(f"/api/jobs/{j}/guard", json={"reported": False})
    assert c.get(f"/api/jobs/{j}/case").json()["advice"]["text"].startswith(fake.PREFIX)
    with Session(engine) as s:
        notes = s.exec(select(Notification).where(Notification.title.startswith(fake.PREFIX))).all()
        assert notes
        logs = s.exec(select(AgentLog).where(AgentLog.job_id == j)).all()
        assert not [x for x in logs if x.step in ("멈춤", "오류")]
        assert not [x for x in logs if "도구 입력이 맞지 않아요" in x.detail]


def test_fake_text_shows_what_ai_received():
    payload = {"messages": [{"role": "system", "content": "규칙"}, {"role": "user", "content": "목표: 급여를 compare_pay로 비교"},
                            {"role": "assistant", "content": "", "tool_calls": []},
                            {"role": "tool", "name": "compare_pay", "content": '{"사실": "차이 0원", "관련 조항": ["최저임금법 제5조"]}'}],
               "tools": [{"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
                         for n in ("make_plan", "compare_pay", "finish")]}
    msg = fake.respond(payload)["choices"][0]["message"]
    assert msg["content"].startswith(f"{fake.PREFIX} (") and "compare_pay" in msg["content"] and "급여를" in msg["content"]


def test_fake_mode_auto():
    """기본(auto)은 실제 모델 설정이 없을 때만 가짜 AI. true는 늘, false는 쓰지 않음."""
    from app.config import fake_mode
    assert fake_mode("auto", False, "") and fake_mode("", True, "")  # 모델 이름이 없으면 실제 모델을 못 씀
    assert not fake_mode("auto", True, "qwen")
    assert fake_mode("true", True, "qwen") and not fake_mode("false", False, "")


def test_daily_check_notice_has_fake_advice(c):
    """가짜 AI일 때 매일 자동 점검 알림에 에이전트 조언(테스트 답변)이 들어간다."""
    from app import scheduler
    j = c.post("/api/jobs", json={"name": "가상분식 매일점", "wage": 10320, "start_date": "2026-08-01"}).json()["id"]
    uid = c.get("/api/me").json()["id"]
    scheduler.daily_check(uid)
    notes = [n for n in c.get(f"/api/notifications?job_id={j}").json() if n["title"].startswith("오늘 자동 점검")]
    assert notes and "에이전트 조언: " + fake.PREFIX in notes[0]["body"]
