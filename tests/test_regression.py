"""기존 기능이 무너지지 않았는지 한꺼번에 확인 (회귀 시험).

여러 사용자 상태(사업장 없음, 청소년, 만 15세 미만, 성인 5인 미만, 그만둔 사업장, 신고 후 보호, 예전 형식 기록)를
AI가 없을 때 만들고, AI(가짜)를 켠 뒤 모든 화면 API와 예약 작업(매일 점검, 다시 맡기기, 후속 확인, 알림 정리)을 불러
서버 오류(5xx)나 예외가 하나도 없는지 본다. 한 기능을 고치면서 다른 기능이 깨지는 일을 막으려는 시험이다.
"""
import json
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import notices, scheduler
from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.llm import client
from app.main import app
from app.models import AgentQuestion, AgentTask, Job, WorkRecord

IMG = (Path(__file__).resolve().parent.parent / "테스트자료" / "02_근로계약서.png").read_bytes()
SCHED = {"월": {"start": "17:00", "end": "22:30", "brk": "없음"},
         "토": [{"start": "10:00", "end": "14:00", "brk": "30분"}, {"start": "18:00", "end": "23:00", "brk": "1시간"}]}
USERS = [("rg_none@example.com", "2009-05-01", None), ("rg_teen@example.com", "2009-05-01", "teen"),
         ("rg_young@example.com", "2012-03-01", "young"), ("rg_adult@example.com", "1999-01-01", "adult"),
         ("rg_quit@example.com", "2008-01-01", "quit"), ("rg_legacy@example.com", "2010-02-01", "legacy")]
JOB_GETS = ["settlement", "records", "evidence", "contract/fields", "check", "payslips", "pay?month=2026-09",
            "pay?month=2026-02", "agent/log", "reports", "guard", "case", "questions", "overview"]
COMMON_GETS = ["/api/me", "/api/jobs", "/api/law/status", "/api/counsel", "/api/agent/last", "/api/agent/live",
               "/api/input-rules", "/api/ai/status", "/api/notifications"]


class Sweep:
    """요청마다 5xx와 예외를 모은다. long=True면 저장·점검 요청을 화면의 agent()처럼 뒤에서 실행하고 결과를 묻는다."""
    def __init__(self, c: TestClient, long: bool = False):
        self.c, self.bad, self.long, self.tasks = c, [], long, 0

    def __call__(self, method: str, url: str, **kw):
        try:
            if self.long and method != "GET":
                r = self.c.request(method, url, headers={"X-Long-Task": "1"}, **kw)
                if r.status_code == 202 and r.json().get("task_id"):
                    self.tasks += 1
                    r = self.wait(r.json()["task_id"], f"{method} {url}")
            else:
                r = self.c.request(method, url, **kw)
        except Exception as exc:  # noqa: BLE001 (예외도 문제로 모은다)
            self.bad.append(f"{method} {url} 예외 {exc!r}"[:300])
            return None
        if r.status_code >= 500:
            self.bad.append(f"{method} {url} {r.status_code} {r.text[:200]}")
        return r

    def wait(self, tid: str, what: str):
        for _ in range(600):
            r = self.c.get(f"/api/tasks/{tid}")
            if r.status_code != 202:
                if r.status_code == 404:
                    self.bad.append(f"{what} 뒤에서 실행한 작업을 찾지 못함")
                return r
            time.sleep(0.02)
        raise AssertionError(f"{what} 뒤에서 실행한 작업이 끝나지 않음")

    def json(self, method: str, url: str, **kw):
        r = self(method, url, **kw)
        try:
            return r.json() if r is not None else None
        except ValueError:
            return None


def login(c: TestClient, email: str, birth: str) -> None:
    c.cookies.clear()
    r = c.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": birth})
    if r.status_code != 200:
        c.post("/api/auth/login", json={"email": email, "password": "test1234"})


def add_rows(user_id: int, job_id: int) -> None:
    """출퇴근 기록(퇴근 안 누른 기록 포함), 답을 기다리는 질문, 때가 된 후속 확인."""
    d = datetime(2026, 9, 1)
    with Session(engine) as s:
        for k in range(20):
            s.add(WorkRecord(user_id=user_id, job_id=job_id, clock_in=d + timedelta(days=k, hours=17),
                             clock_out=d + timedelta(days=k, hours=23, minutes=k)))
        s.add(WorkRecord(user_id=user_id, job_id=job_id, clock_in=now_kst() - timedelta(hours=14)))
        s.add(AgentQuestion(user_id=user_id, job_id=job_id, event="contract_check", question="가상 질문: 쉬는 시간이 있었나요?",
                            answer="", status="open", created_at=now_kst()))
        s.add(AgentTask(user_id=user_id, job_id=job_id, kind="payday", month="2026-09", event="payday", note="가상 후속 확인",
                        due_at=now_kst() - timedelta(minutes=1), status="pending", created_at=now_kst()))
        s.commit()


def make_legacy(job_id: int) -> None:
    """예전 형식: 시간대 하나 dict, 쉬는 시간 "0", 월급날 0."""
    with Session(engine) as s:
        job = s.get(Job, job_id)
        job.schedule_json = json.dumps({"수": {"start": "18:00", "end": "22:00", "brk": "0"}}, ensure_ascii=False)
        job.payday = 0
        s.add(job)
        s.commit()


def use_everything(call: Sweep, j: int) -> None:
    """화면에서 누를 수 있는 기능을 모두 한 번씩 (저장 후 따로 점검하는 길과 한 번에 하는 길 모두)."""
    saved = call.json("POST", f"/api/jobs/{j}/contract", files={"file": ("c.png", IMG, "image/png")}, data={"read": "false"})
    if saved and saved.get("evidence"):
        r = call.json("POST", f"/api/jobs/{j}/agent/read?evidence_id={saved['evidence']['id']}&kind=contract")
        if r and r.get("fields"):
            call("PUT", f"/api/jobs/{j}/contract/fields", json={"fields": r["fields"]})
    call("POST", f"/api/jobs/{j}/contract", files={"file": ("c.png", IMG, "image/png")})
    call("POST", f"/api/jobs/{j}/check")
    for m in ("2026-08", "2026-09"):
        call("POST", f"/api/jobs/{j}/payslip", data={"month": m, "amount": "300000"})
        call("POST", f"/api/jobs/{j}/payslip", data={"month": m, "amount": "50000", "check": "false"})
        call("POST", f"/api/jobs/{j}/agent/payday?month={m}")
    call("POST", f"/api/jobs/{j}/payslip/read", files={"file": ("p.png", IMG, "image/png")})
    ps = call.json("GET", f"/api/jobs/{j}/payslips") or []
    if ps:
        call("PUT", f"/api/payslips/{ps[0]['id']}", json={"amount": 310000})
    call("POST", f"/api/jobs/{j}/evidence", files={"file": ("e.txt", b"gajja", "text/plain")}, data={"kind": "etc", "note": "가상 메모"})
    call("POST", f"/api/jobs/{j}/punch", json={"confirm": True})
    out = call.json("POST", f"/api/jobs/{j}/punch", json={"confirm": True, "check": False}) or {}
    if out.get("shift_record"):
        call("POST", f"/api/jobs/{j}/agent/shift?record_id={out['shift_record']}")
    recs = call.json("GET", f"/api/jobs/{j}/records")
    recs = recs.get("records", []) if isinstance(recs, dict) else recs or []
    if recs:
        call("POST", f"/api/jobs/{j}/records/{recs[0]['id']}/void", json={"reason": "가상 실수"})
    call("POST", f"/api/jobs/{j}/guard", json={"reported": True})
    call("PUT", f"/api/jobs/{j}/guard/keywords", json={"keywords": ["가상분식"]})
    call("PUT", f"/api/jobs/{j}/guard/message", json={"message": "가상 안내 문구"})
    call("POST", f"/api/jobs/{j}/guard/posts", json={"url": "https://example.invalid/p/1"})
    call("POST", f"/api/jobs/{j}/guard/posts", json={"url": "https://example.invalid/p/2", "check": False})
    call("POST", f"/api/jobs/{j}/agent/review")
    call("POST", f"/api/jobs/{j}/guard/search")
    qs = call.json("GET", f"/api/jobs/{j}/questions")
    qs = qs.get("questions", []) if isinstance(qs, dict) else qs or []
    for q in [q for q in qs if q.get("status") == "open"][:1]:
        call("POST", f"/api/questions/{q['id']}/answer", json={"answer": "가상 답: 없었어요", "check": False})
        call("POST", f"/api/questions/{q['id']}/rerun")
    call("POST", f"/api/jobs/{j}/report")
    call("POST", f"/api/jobs/{j}/agent/overview")  # 홈의 종합 점검 (모든 기록을 모아 판단)


def quit_job(call: Sweep, j: int) -> None:
    call("POST", f"/api/jobs/{j}/quit", json={"quit_date": "2026-09-20", "check": False})
    call("POST", f"/api/jobs/{j}/agent/quit")
    call("POST", f"/api/jobs/{j}/paid", json={"paid": False})
    call("POST", f"/api/jobs/{j}/report")


def read_everything(call: Sweep, j: int) -> None:
    for p in JOB_GETS:
        call("GET", f"/api/jobs/{j}/{p}")
    for rep in call.json("GET", f"/api/jobs/{j}/reports") or []:
        call("GET", rep["url"])
    for ev in (call.json("GET", f"/api/jobs/{j}/evidence") or [])[:30]:
        if isinstance(ev, dict) and ev.get("id") and ev.get("kind") != "post_link":
            call("GET", f"/api/evidence/{ev['id']}/file")


def common(call: Sweep) -> None:
    for p in COMMON_GETS:
        call("GET", p)
    call("PUT", "/api/me/prefs", json={"gps_consent": True})
    call("POST", "/api/seek/check", json={"name": "가상분식", "wage": 9000, "schedule": SCHED})
    notice = call.json("POST", "/api/evidence", files={"file": ("n.png", IMG, "image/png")}, data={"kind": "notice"})
    if isinstance(notice, dict) and notice.get("id"):  # 지원 전 확인: 공고 사진 읽기
        call("POST", f"/api/seek/read?evidence_id={notice['id']}")
    call("POST", "/api/notifications/read")


def scheduled_jobs() -> list[str]:
    """예약 작업을 직접 돌린다 (서버 로그에만 남고 화면에는 안 보이던 오류를 잡는다)."""
    bad = []
    for name in ("daily_check", "open_record_check", "retry_waiting", "run_due_followups"):
        try:
            getattr(scheduler, name)()
        except Exception:  # noqa: BLE001
            bad.append(f"예약 작업 {name}: {traceback.format_exc()[-400:]}")
    try:
        with Session(engine) as s:
            notices.tidy(s)
    except Exception:  # noqa: BLE001
        bad.append(f"알림 정리: {traceback.format_exc()[-400:]}")
    return bad


@pytest.fixture()
def c():
    init_db()
    with TestClient(app, raise_server_exceptions=False) as cl:
        yield cl


def test_everything_still_works_after_ai_turns_on(c, monkeypatch):
    call = Sweep(c)
    # 1) AI가 없을 때 기록을 만든다 (예전에 AI 없이 쓰던 사용자)
    monkeypatch.setattr(client, "LLM_FAKE", False)
    for email, birth, kind in USERS:
        login(c, email, birth)
        common(call)
        if not kind:
            continue
        j = call.json("POST", "/api/jobs", json={"name": f"가상가게 {kind}", "wage": 9000, "start_date": "2026-07-01",
                                                 "schedule": SCHED, "payday": 10, "size": "lt5" if kind == "adult" else "5+"})["id"]
        if kind == "legacy":
            make_legacy(j)
        add_rows(c.get("/api/me").json()["id"], j)
        use_everything(call, j)
        if kind == "quit":
            quit_job(call, j)
        if kind == "adult":  # 지운 사업장의 기록이 남아도 다른 기능이 깨지지 않게
            j2 = call.json("POST", "/api/jobs", json={"name": "가상지울가게", "wage": 9000, "schedule": SCHED})["id"]
            use_everything(call, j2)
            call("DELETE", f"/api/jobs/{j2}")
    call.bad += scheduled_jobs()
    # 2) AI(가짜)를 켠 뒤: 저장돼 있던 대기 결과, 다시 점검, 모든 조회와 예약 작업
    monkeypatch.setattr(client, "LLM_FAKE", True)
    call.bad += scheduled_jobs()
    for email, birth, _ in USERS:
        login(c, email, birth)
        common(call)
        for job in call.json("GET", "/api/jobs") or []:
            read_everything(call, job["id"])
            call("POST", f"/api/jobs/{job['id']}/check")
            call("POST", f"/api/jobs/{job['id']}/report")
            call("POST", f"/api/jobs/{job['id']}/agent/overview")
            read_everything(call, job["id"])
    assert not call.bad, "\n".join(call.bad)


def test_everything_works_through_long_task(c, monkeypatch):
    """화면의 AI 버튼처럼 저장·점검 요청을 뒤에서 실행해도(cloudflared 100초 대비) 모든 기능이 같은 결과로 돈다."""
    monkeypatch.setattr(client, "LLM_FAKE", True)
    call = Sweep(c, long=True)
    for email, birth, kind in [("rgl_teen@example.com", "2009-05-01", "teen"), ("rgl_quit@example.com", "2008-01-01", "quit")]:
        login(c, email, birth)
        common(call)
        j = call.json("POST", "/api/jobs", json={"name": f"가상뒤가게 {kind}", "wage": 9000, "start_date": "2026-07-01",
                                                 "schedule": SCHED, "payday": 10})["id"]
        add_rows(c.get("/api/me").json()["id"], j)
        use_everything(call, j)
        if kind == "quit":
            quit_job(call, j)
        read_everything(call, j)
    assert call.tasks >= 40, f"뒤에서 실행한 요청이 {call.tasks}개뿐이에요"
    assert not call.bad, "\n".join(call.bad)
