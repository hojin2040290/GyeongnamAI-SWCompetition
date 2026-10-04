"""베타 테스트 체험판(app/beta.py): 미션 판정이 안내한 기능에서만 끝나는지."""
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import beta, config
from app.db import engine, init_db
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _state(c: TestClient) -> dict:
    return {m["key"]: m["done"] for m in c.get("/api/beta/state").json()["missions"]}


def test_evidence_mission_only_from_docs_tab(monkeypatch):
    """급여 탭에서 명세서를 올려도 '자료 탭에서 입금 내역 올리기'는 끝나지 않는다 (예전에는 명세서만으로 완료됐다)."""
    monkeypatch.setattr(config, "BETA_GUIDE", True)
    init_db()
    c = TestClient(app)
    beta.build_account(c, lambda: Session(engine), "beta_ev_test@example.com", "working")
    job = c.get("/api/jobs").json()[0]["id"]
    assert _state(c) == {"punch": False, "payday": False, "evidence": False}
    r = c.post(f"/api/jobs/{job}/payslip/read", files={"file": ("p.png", PNG, "image/png")}, data={"read": "false"})
    assert r.status_code == 200
    r = c.post(f"/api/jobs/{job}/contract", files={"file": ("c.png", PNG, "image/png")}, data={"read": "false"})
    assert r.status_code == 200
    assert _state(c)["evidence"] is False
    r = c.post(f"/api/jobs/{job}/evidence", files={"file": ("d.png", PNG, "image/png")}, data={"kind": "deposit"})
    assert r.status_code == 200
    assert _state(c)["evidence"] is True


def test_payday_mission_points_to_case_month():
    """급여 미션의 바로 가기는 시험 데이터의 근무 기록이 있는 달로 연다 (급여 탭은 이번 달로 열린다)."""
    from app.demo_db import CASES
    month = next(c["month"] for c in CASES if c["key"] == beta.PERSONAS["working"]["case"])
    m = next(m for m in beta.PERSONAS["working"]["missions"] if m["key"] == "payday")
    assert m["month"] == month and "8월" in m["how"]
