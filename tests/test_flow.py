"""화면에서 부르는 API 흐름 테스트. 모든 기능이 에이전트를 거쳐 동작 기록에 남는지 확인한다."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine, init_db
from app.main import app
from app.models import AgentLog, LawArticle, WorkRecord

JOB = {"name": "가상카페 테스트점", "wage": 11000, "schedule": {"금": {"start": "17:00", "end": "21:00", "brk": "없음"}},
       "size": "5+", "contract_written": True, "copy_received": True, "probation": "no", "consent": "냈어요"}


@pytest.fixture(scope="module")
def c():
    init_db()
    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={"email": "flow@example.com", "password": "test1234",
                                                     "birth_date": "2010-05-01", "mode": "work"})
        assert r.status_code == 200
        yield client


def events(user_id: int = 1) -> set[str]:
    with Session(engine) as s:
        return {a.event for a in s.exec(select(AgentLog).where(AgentLog.user_id == user_id))}


def test_edit_me_and_job(c):
    assert c.put("/api/me", json={"birth_date": "2010-06-01"}).json()["birth_date"] == "2010-06-01"
    assert c.put("/api/me", json={"birth_date": "2999-01-01"}).status_code == 400
    job = c.post("/api/jobs", json=JOB).json()
    upd = c.put(f"/api/jobs/{job['id']}", json={**JOB, "wage": 11500, "size": "lt5"}).json()
    assert upd["wage"] == 11500 and upd["size"] == "lt5"
    c.post("/api/jobs", json={**JOB, "name": "가상편의점"})
    assert c.put(f"/api/jobs/{job['id']}", json={**JOB, "name": "가상편의점"}).status_code == 400


def test_punch_out_runs_shift_check(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    assert c.post(f"/api/jobs/{job_id}/punch", json={}).json()["action"] == "in"
    # 서버 시각 대신 미성년 야간 근무가 생기도록 기록 시각을 조정
    with Session(engine) as s:
        r = s.exec(select(WorkRecord).where(WorkRecord.clock_out == None)).first()  # noqa: E711
        r.clock_in = datetime(2026, 9, 25, 17, 0)
        s.add(r)
        s.commit()
    from unittest.mock import patch
    with patch("app.routers.api.now_kst", return_value=datetime(2026, 9, 25, 22, 30)):
        out = c.post(f"/api/jobs/{job_id}/punch", json={}).json()
    assert out["action"] == "out"
    laws = {i["law"] for i in out["shift"]["items"]}
    assert "근로기준법 제70조" in laws
    assert out["shift"]["trace"][0]["step"] == "시작"
    assert "shift_check" in events()


def test_contract_check_uses_records_and_law_table(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    items = c.post(f"/api/jobs/{job_id}/check").json()["items"]
    assert any(i["source"] == "records" for i in items)
    assert all(i["article"]["built"] is False for i in items)  # 법 기준표가 비어 있으면 미구축
    with Session(engine) as s:
        s.add(LawArticle(law_name="근로기준법", article_no="70", title="테스트 조문", text="테스트용 가상 조문 원문",
                         fetched_at=datetime(2026, 9, 29)))
        s.commit()
    items = c.get(f"/api/jobs/{job_id}/check").json()["items"]
    night = [i for i in items if i["law"] == "근로기준법 제70조"][0]
    assert night["article"]["built"] and night["article"]["text"] == "테스트용 가상 조문 원문"


def test_seek_report_guard_go_through_agent(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    r = c.post("/api/seek/check", json={"name": "가상분식", "wage": 9000, "schedule": {}}).json()
    assert r["items"] and r["questions"] and r["trace"]
    rep = c.post(f"/api/jobs/{job_id}/report").json()
    html = c.get(rep["url"]).text
    assert "관련 조문 원문" in html and "테스트용 가상 조문 원문" in html
    assert c.get(f"/api/jobs/{job_id}/reports").json()[0]["id"] == rep["id"]
    g = c.post(f"/api/jobs/{job_id}/guard", json={"reported": True}).json()
    assert g["reported"] and "제104조" in g["message"]
    g = c.put(f"/api/jobs/{job_id}/guard/keywords", json={"keywords": ["김가상", " 별명 ", ""]}).json()
    assert g["keywords"] == ["김가상", "별명"] and g["queries"] == ["가상카페 테스트점 김가상", "가상카페 테스트점 별명"]
    s = c.post(f"/api/jobs/{job_id}/guard/search").json()
    assert s["skipped"] is True
    p = c.post(f"/api/jobs/{job_id}/guard/posts", json={"url": "https://example.com/post/1"}).json()
    assert p["classify"]["pending"] == 1
    assert {"seek_check", "report", "guard_on", "guard_search", "guard_preserve"} <= events()
    logs = c.get(f"/api/jobs/{job_id}/agent/log").json()
    assert any(lg["event"] == "seek_check" for lg in logs)  # 사업장 없이 실행한 지원 전 확인도 보인다


def test_daily_check_searches_reported_jobs(c):
    assert c.post("/api/dev/daily-check").json()["guard"] == 1


def test_payslip_edit(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "10000"})
    c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "20000"})
    ps = c.get(f"/api/jobs/{job_id}/payslips").json()
    assert [(p["month"], p["amount"]) for p in ps] == [("2026-09", 20000)]
    c.delete(f"/api/jobs/{job_id}/payslips/2026-09")
    assert c.get(f"/api/jobs/{job_id}/payslips").json() == []


def test_prefs_and_guard_message_are_saved(c):
    jobs = c.get("/api/jobs").json()
    second = jobs[1]["id"]
    me = c.put("/api/me/prefs", json={"gps_consent": True, "last_job_id": second}).json()
    assert me["gps_consent"] is True and me["last_job_id"] == second
    assert c.get("/api/me").json()["last_job_id"] == second  # 다시 열어도 마지막으로 보던 곳
    assert c.put("/api/me/prefs", json={"last_job_id": 99999}).status_code == 404
    job_id = jobs[0]["id"]
    g = c.put(f"/api/jobs/{job_id}/guard/message", json={"message": "고친 안내 문구"}).json()
    assert g["message"] == "고친 안내 문구" and g["custom_message"] is True
    assert c.get(f"/api/jobs/{job_id}/guard").json()["message"] == "고친 안내 문구"
    g = c.put(f"/api/jobs/{job_id}/guard/message", json={"message": ""}).json()
    assert "제104조" in g["message"] and g["custom_message"] is False


def test_contract_fields_saved_without_check(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    c.put(f"/api/jobs/{job_id}/contract/fields", json={"fields": {"임금": "시급 11,000원"}})
    assert c.get(f"/api/jobs/{job_id}/contract/fields").json()["fields"]["임금"] == "시급 11,000원"


def test_same_notification_is_not_repeated(c):
    job_id = c.get("/api/jobs").json()[0]["id"]
    before = len(c.get("/api/notifications").json())
    c.post(f"/api/jobs/{job_id}/agent/payday?month=2026-08")
    c.post(f"/api/jobs/{job_id}/agent/payday?month=2026-08")
    titles = [n["title"] for n in c.get("/api/notifications").json()]
    assert titles.count("2026-08 급여 점검") == 1 and len(titles) == before + 1


def test_mistaken_punch_is_marked_not_deleted(c):
    from unittest.mock import patch
    job = c.post("/api/jobs", json={**JOB, "name": "가상실수점"}).json()
    jid = job["id"]
    t0 = datetime(2026, 9, 21, 10, 0)
    with patch("app.routers.api.now_kst", return_value=t0):
        c.post(f"/api/jobs/{jid}/punch", json={})
    with patch("app.routers.api.now_kst", return_value=t0.replace(second=30)):
        r = c.post(f"/api/jobs/{jid}/punch", json={})
        assert r.status_code == 409 and "방금 출근" in r.json()["detail"]  # 1분 안에 퇴근은 한 번 더 확인
    with patch("app.routers.api.now_kst", return_value=t0.replace(hour=14)):
        assert c.post(f"/api/jobs/{jid}/punch", json={}).json()["action"] == "out"
    rec = c.get(f"/api/jobs/{jid}/records").json()["records"][0]
    assert c.get(f"/api/jobs/{jid}/pay?month=2026-09").json()["expected"]["work_min"] == 240
    v = c.post(f"/api/jobs/{jid}/records/{rec['id']}/void", json={"reason": "일 안 한 날"}).json()
    assert v["void"] and v["void_reason"] == "일 안 한 날" and v["clock_in"] == rec["clock_in"]  # 시각은 그대로
    assert c.get(f"/api/jobs/{jid}/pay?month=2026-09").json()["expected"]["work_min"] == 0  # 계산에서 빠짐
    html = c.get(c.post(f"/api/jobs/{jid}/report").json()["url"]).text
    assert "실수로 표시함" in html and "일 안 한 날" in html
    assert not c.delete(f"/api/jobs/{jid}/records/{rec['id']}/void").json()["void"]
    assert c.get(f"/api/jobs/{jid}/pay?month=2026-09").json()["expected"]["work_min"] == 240


def test_forgotten_punch_out(c):
    from unittest.mock import patch
    from app import scheduler
    jid = c.post("/api/jobs", json={**JOB, "name": "가상퇴근잊음점"}).json()["id"]
    t0 = datetime(2026, 9, 21, 10, 0)
    with patch("app.routers.api.now_kst", return_value=t0):
        c.post(f"/api/jobs/{jid}/punch", json={})
    with patch("app.routers.api.now_kst", return_value=t0.replace(day=22, hour=9)):
        r = c.post(f"/api/jobs/{jid}/punch", json={})
        assert r.status_code == 409 and "23시간" in r.json()["detail"]
    assert scheduler.open_record_check()["open"] >= 1
    scheduler.open_record_check()  # 두 번 돌아도 알림은 하나
    assert [n["title"] for n in c.get("/api/notifications").json()].count("퇴근을 누르지 않은 것 같아요") == 1
    rec = c.get(f"/api/jobs/{jid}/records").json()["records"][0]
    c.post(f"/api/jobs/{jid}/records/{rec['id']}/void", json={})
    recs = c.get(f"/api/jobs/{jid}/records").json()
    assert recs["working"] is False and recs["records"][0]["void_reason"] == "실수로 누름"
    assert c.post(f"/api/jobs/{jid}/punch", json={}).json()["action"] == "in"  # 새 출근 가능
    assert c.delete(f"/api/jobs/{jid}/records/{rec['id']}/void").status_code == 400  # 출근 중이면 취소 불가


def test_notifications_per_job_and_delete(c):
    jobs = c.get("/api/jobs").json()
    a, b = jobs[0]["id"], jobs[1]["id"]
    c.post(f"/api/jobs/{a}/agent/payday?month=2026-07")
    c.post(f"/api/jobs/{b}/agent/payday?month=2026-07")
    ta = [n["title"] for n in c.get(f"/api/notifications?job_id={a}").json()]
    tb = c.get(f"/api/notifications?job_id={b}").json()
    assert ta.count("2026-07 급여 점검") == 1 and all(n["job_id"] in (b, None) for n in tb)
    c.delete(f"/api/notifications/{tb[0]['id']}")
    assert tb[0]["id"] not in [n["id"] for n in c.get(f"/api/notifications?job_id={b}").json()]
    c.post(f"/api/jobs/{b}/agent/payday?month=2026-06")
    b_before = len(c.get(f"/api/notifications?job_id={b}").json())
    assert c.delete(f"/api/notifications?job_id={a}").json()["deleted"] >= 1
    assert c.get(f"/api/notifications?job_id={a}").json() == []
    assert len(c.get(f"/api/notifications?job_id={b}").json()) == b_before  # 다른 곳 알림은 남음
