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


def events() -> set[str]:
    from app.models import User
    with Session(engine) as s:
        uid = s.exec(select(User).where(User.email == "flow@example.com")).first().id
        return {a.event for a in s.exec(select(AgentLog).where(AgentLog.user_id == uid))}


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
    got = c.get(f"/api/jobs/{job_id}/check").json()
    assert "ai" in got  # AI가 있으면 화면이 예전 대기 결과를 다시 점검한다
    items = got["items"]
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


def test_dev_daily_check_is_locked(c, monkeypatch):
    """시연용 매일 점검: DEV_TOOLS를 끄면 없는 주소이고, 켜도 로그인한 사용자의 사업장만 점검한다."""
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    with TestClient(app) as other:  # 로그인하지 않은 사람
        assert other.post("/api/dev/daily-check").status_code == 401
    monkeypatch.setattr(config, "DEV_TOOLS", False)
    assert c.post("/api/dev/daily-check").status_code == 404
    monkeypatch.setattr(config, "DEV_TOOLS", True)
    assert "law_changed" not in c.post("/api/dev/daily-check").json()  # 법 기준표 갱신은 하지 않음


def test_payslip_multiple_in_month(c):
    """같은 달에 나눠 받은 금액은 여러 건으로 저장해 합쳐 비교하고, 한 건씩 고치거나 지울 수 있다."""
    job_id = c.get("/api/jobs").json()[0]["id"]
    c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "10000"})
    r = c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "20000"}).json()
    assert r["paid"] == 30000  # 따로 더 받은 금액은 합친다
    ps = c.get(f"/api/jobs/{job_id}/payslips").json()
    assert [(p["month"], p["amount"]) for p in ps] == [("2026-09", 10000), ("2026-09", 20000)]
    assert c.put(f"/api/payslips/{ps[0]['id']}", json={"amount": 15000}).json()["paid"] == 35000
    c.delete(f"/api/payslips/{ps[1]['id']}")
    assert [p["amount"] for p in c.get(f"/api/jobs/{job_id}/payslips").json()] == [15000]
    r = c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "50000", "mode": "replace"}).json()
    assert r["paid"] == 50000 and len(c.get(f"/api/jobs/{job_id}/payslips").json()) == 1  # 금액 고치기
    assert c.post(f"/api/jobs/{job_id}/payslip", data={"month": "2026-09", "amount": "-1"}).status_code == 400
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


def test_business_number_saved_and_checked(c):
    jid = c.post("/api/jobs", json={**JOB, "name": "가상번호점", "biz_no": "1234567891"}).json()["id"]
    assert c.get("/api/jobs").json()[-1]["biz_no"] == "123-45-67891"
    # 검증 번호가 틀려도 저장은 되고, 점검에서 확인 필요로 안내한다
    assert c.put(f"/api/jobs/{jid}", json={**JOB, "name": "가상번호점", "biz_no": "123-45-67890"}).json()["biz_no"] == "123-45-67890"
    biz = [i for i in c.post(f"/api/jobs/{jid}/check").json()["items"] if i["law"] == "사업자 정보"][0]
    assert biz["status"] == "warn" and "검증 번호" in biz["text"] and biz["article"].get("na")
    assert c.put(f"/api/jobs/{jid}", json={**JOB, "name": "가상번호점", "biz_no": ""}).json()["biz_no"] == ""
    assert c.post("/api/seek/check", json={"biz_no": "123-45"}).status_code == 400  # 자리 수가 틀리면 막음
    html = c.get(c.post(f"/api/jobs/{jid}/report").json()["url"]).text
    assert "사업자등록번호" in html


def test_judgments_wait_for_ai_without_model(c):
    """AI 연결 전: 코드가 정상/위반 의심을 정하지 않고, 정보가 없는 항목만 확인 필요로 둔다."""
    jid = c.get("/api/jobs").json()[0]["id"]
    items = c.post(f"/api/jobs/{jid}/check").json()["items"]
    assert {i["status"] for i in items} <= {"pending", "warn"}
    assert all("rule_status" in i for i in items)  # 규칙 결과는 검증 장치로 남는다
    assert c.get("/api/ai/status").json()["judge"] is False


def test_pay_compare_facts_only(c):
    from app.agent.tools import make_tools
    from app.db import engine as db_engine
    with Session(db_engine) as s:
        t = make_tools(s, 1, c.get("/api/jobs").json()[0]["id"])
        none = t["compare_pay"]({"total": 0, "work_min": 0}, 550000)
        more = t["compare_pay"]({"total": 100000, "work_min": 600}, 550000)
        less = t["compare_pay"]({"total": 100000, "work_min": 600}, 50000)
    assert none["status"] == "warn" and "근무 기록이 없어" in none["text"]  # 0원인데 '거의 같아요'가 나오던 문제
    assert more["status"] == "pending" and "450,000원 더 받았어요" in more["text"] and not more["short"]
    assert less["status"] == "pending" and "50,000원 적게" in less["text"] and less["short"]


def test_work_period_dates_are_checked(c):
    """시작일은 오늘보다 뒤일 수 없고, 종료일과 그만둔 날은 시작일보다 앞일 수 없다. 그만둔 날은 미래여도 된다."""
    from datetime import timedelta
    from app.calc.timeutil import today_kst
    t = today_kst()
    base = {"name": "가상카페 날짜점", "wage": 10030}
    r = c.post("/api/jobs", json={**base, "start_date": (t + timedelta(days=1)).isoformat()})
    assert r.status_code == 400 and "오늘보다 뒤" in r.json()["detail"]
    r = c.post("/api/jobs", json={**base, "start_date": "2026-03-02", "end_date": "2026-03-01"})
    assert r.status_code == 400 and "계약 종료일" in r.json()["detail"]
    r = c.post("/api/jobs", json={**base, "start_date": "2026-03-02", "status": "quit", "quit_date": "2026-03-01"})
    assert r.status_code == 400 and "그만둔 날" in r.json()["detail"]
    later = (t + timedelta(days=7)).isoformat()  # 앞으로 그만둘 날은 된다
    job = c.post("/api/jobs", json={**base, "start_date": "2026-03-02", "status": "quit", "quit_date": later}).json()
    assert job["quit_date"] == later
    assert c.post(f"/api/jobs/{job['id']}/quit", json={"quit_date": "2026-03-01"}).status_code == 400
    c.delete(f"/api/jobs/{job['id']}")


def test_save_returns_before_agent_check(c):
    """저장(check=false)은 에이전트를 부르지 않고 바로 응답하고, 점검은 /agent/... 로 따로 부른다."""
    from unittest.mock import patch
    job = c.post("/api/jobs", json={**JOB, "name": "가상분리점"}).json()
    jid = job["id"]
    with patch("app.agent.core.Run.agent", side_effect=AssertionError("저장 중에는 AI를 부르지 않아요")):
        assert c.post(f"/api/jobs/{jid}/payslip", data={"month": "2026-09", "amount": "10000", "check": "false"}).json() \
            == {"saved": True, "month": "2026-09"}
        pid = c.get(f"/api/jobs/{jid}/payslips").json()[0]["id"]
        assert c.put(f"/api/payslips/{pid}", json={"amount": 12000, "check": False}).json()["saved"]
        assert c.post(f"/api/jobs/{jid}/quit", json={"quit_date": "2026-09-20", "check": False}).json() \
            == {"settlement": None, "saved": True}
        assert c.post(f"/api/jobs/{jid}/paid", json={"paid": False, "check": False}).json()["saved"]
        g = c.post(f"/api/jobs/{jid}/guard", json={"reported": True, "check": False}).json()
        assert g["reported"] and g["saved"]
        saved = c.post(f"/api/jobs/{jid}/contract", files={"file": ("c.png", b"\x89PNG test", "image/png")},
                       data={"read": "false"}).json()
        assert saved["saved"] and "fields" not in saved
    # 저장한 값은 점검 전에도 남아 있다
    assert c.get(f"/api/jobs/{jid}/payslips").json()[0]["amount"] == 12000
    assert c.post(f"/api/jobs/{jid}/agent/payday?month=2026-09").json()["trace"]
    assert c.post(f"/api/jobs/{jid}/agent/quit").json()["settlement"]
    assert c.post(f"/api/jobs/{jid}/agent/guard").json()["trace"]
    assert c.post(f"/api/jobs/{jid}/agent/review").json()["trace"]
    ev = saved["evidence"]
    assert "fields" in c.post(f"/api/jobs/{jid}/agent/read?evidence_id={ev['id']}&kind=contract").json()
    assert c.post(f"/api/jobs/{jid}/agent/read?evidence_id={ev['id']}&kind=etc").status_code == 400


def test_punch_out_then_shift_check(c):
    job = c.post("/api/jobs", json={**JOB, "name": "가상분리점2"}).json()
    jid = job["id"]
    assert c.post(f"/api/jobs/{jid}/punch", json={"check": False}).json()["action"] == "in"
    out = c.post(f"/api/jobs/{jid}/punch", json={"check": False, "confirm": True}).json()
    assert out["action"] == "out" and "shift" not in out and out["shift_record"]
    assert c.post(f"/api/jobs/{jid}/agent/shift?record_id={out['shift_record']}").json()["trace"]
    assert c.post(f"/api/jobs/{jid}/agent/shift?record_id=999999").status_code == 404


def test_report_for_quit_job(c):
    """그만둔 사업장의 상담 사전 자료 (퇴직일과 지급 기한이 들어간다)."""
    jid = c.post("/api/jobs", json={**JOB, "name": "가상퇴직점", "start_date": "2026-08-01"}).json()["id"]
    assert c.post(f"/api/jobs/{jid}/quit", json={"quit_date": "2026-09-20", "check": False}).status_code == 200
    st = c.post(f"/api/jobs/{jid}/agent/quit").json()["settlement"]
    rep = c.post(f"/api/jobs/{jid}/report")
    assert rep.status_code == 200
    html = c.get(f"/api/reports/{rep.json()['id']}").text
    assert "퇴직일 2026년 9월 20일 (일)" in html
    # 자료의 판단은 화면(/settlement)과 같다: AI가 판단했으면 그 결과, 아니면 확인 중
    assert c.get(f"/api/jobs/{jid}/settlement").json()["settlement"]["status"] == st["status"]
    assert ("확인 중" in html.split("퇴직일")[1].split("</p>")[0]) == (st["status"] == "pending")

