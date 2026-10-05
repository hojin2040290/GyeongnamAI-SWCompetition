"""처음 쓰는 사람 체험 안내(app/guide.py): 새로 가입한 계정만, 고른 상황별 미션, 시험 계정과 끈 계정에는 없음."""
from fastapi.testclient import TestClient

from app import config, guide
from app.db import init_db
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
BIRTH = "2009-03-15"


def _join(email: str, mode: str) -> TestClient:
    init_db()
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": BIRTH, "mode": mode})
    assert r.status_code == 200, r.text
    return c


def _done(c: TestClient) -> dict:
    return {m["key"]: m["done"] for m in c.get("/api/guide/state").json()["missions"]}


def _job(c: TestClient, **extra) -> int:
    body = {"name": "가상안내 시험점", "wage": 10320, "start_date": "2026-08-03", "schedule": {}, **extra}
    r = c.post("/api/jobs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


KEYS = ["seek", "job", "punch", "contract", "payday", "quitjob", "paid", "report"]


def test_new_account_gets_all_missions_whatever_mode():
    """고른 상황과 상관없이 모든 새 계정이 같은 체험 미션 8개를 받는다 (세 상황의 미션을 합친 것)."""
    for mode in ("seek", "work", "quit"):
        st = _join(f"guide_{mode}@example.com", mode).get("/api/guide/state").json()
        assert st["on"] and [m["key"] for m in st["missions"]] == KEYS and st["done_count"] == 0
        assert not st["intro_seen"] and "contractFile" in st["files"] and st["survey_url"]


def test_all_missions_judged_from_records():
    """8개를 모두 기록으로 판정하고, 다 해야 all_done (설문 안내). 명세서 사진만 올려서는 급여 미션이 끝나지 않는다."""
    c = _join("guide_work2@example.com", "work")
    assert not any(_done(c).values())
    r = c.post("/api/seek/check", json={"name": "가상안내 공고", "wage": 10320, "schedule": {}})
    assert r.status_code == 200, r.text
    job = _job(c)
    assert _done(c)["seek"] and _done(c)["job"]
    c.post(f"/api/jobs/{job}/punch", json={"check": False})
    c.post(f"/api/jobs/{job}/punch", json={"check": False, "confirm": True})
    r = c.post(f"/api/jobs/{job}/payslip/read", files={"file": ("p.png", PNG, "image/png")}, data={"read": "false"})
    assert r.status_code == 200
    assert _done(c)["punch"] and not _done(c)["payday"]
    c.post(f"/api/jobs/{job}/payslip", data={"month": "2026-08", "amount": "500000", "check": "false"})
    c.post(f"/api/jobs/{job}/check")
    assert _done(c)["contract"] and _done(c)["payday"] and not c.get("/api/guide/state").json()["all_done"]
    c.post(f"/api/jobs/{job}/quit", json={"quit_date": "2026-09-27", "check": False})  # 홈의 '이곳을 그만뒀어요'
    c.post(f"/api/jobs/{job}/paid", json={"paid": False, "check": False})
    assert _done(c)["quitjob"] and _done(c)["paid"] and not _done(c)["report"]
    assert c.post(f"/api/jobs/{job}/report").status_code == 200
    st = c.get("/api/guide/state").json()
    assert st["all_done"] and st["done_count"] == 8 and st["files"]  # 완료 창을 닫기 전까지는 예시 자료를 고를 수 있다
    st = c.post("/api/guide/mark", json={"key": "closed"}).json()
    assert st["on"] and st["files"] == {}  # 다 하고 닫으면 업로드는 바로 내 파일 고르기


def test_quit_missions():
    c = _join("guide_quit2@example.com", "quit")
    job = _job(c, status="quit", quit_date="2026-08-29")
    assert _done(c)["quitjob"] and not _done(c)["paid"]
    c.post(f"/api/jobs/{job}/paid", json={"paid": False, "check": False})
    assert _done(c)["paid"]


def test_mode_change_and_turn_off():
    c = _join("guide_change@example.com", "seek")
    me = c.get("/api/me").json()
    c.put("/api/me", json={"email": me["email"], "birth_date": BIRTH, "mode": "quit"})  # 처음 화면에서 상황을 다시 고름
    assert c.get("/api/guide/state").json()["mode"] == "quit"
    st = c.post("/api/guide/mark", json={"key": "off"}).json()
    assert st == {"on": False, "survey_url": config.SURVEY_URL, "demo": False}
    assert c.post("/api/guide/mark", json={"key": "bad"}).status_code == 422


def test_no_guide_when_turned_off_in_settings(monkeypatch):
    monkeypatch.setattr(config, "FIRST_GUIDE", False)
    assert _join("guide_none@example.com", "work").get("/api/guide/state").json()["on"] is False


def test_example_files_only_listed_ones():
    c = _join("guide_files@example.com", "work")
    assert c.get("/api/guide/files/알바5개/3_근로계약서.png").status_code == 200
    assert c.get("/api/guide/files/../app/config.py").status_code == 404
    assert c.get("/api/guide/files/00_README_정답.md").status_code == 404
    assert set(guide.FILLS) <= guide.ALLOWED_FILES and all((guide.MATERIAL / n).is_file() for n in guide.ALLOWED_FILES)


def test_demo_accounts_start_as_new_users():
    """시험 계정(app/demo_db.py)은 방금 가입한 처음 이용자: 일하는 곳이 없고 체험 안내가 처음부터 켜져 있다.
    --scenarios 계정(알바 5개와 기록)에는 체험 안내가 없다."""
    from app import demo_db
    init_db()
    c = TestClient(app)
    demo_db.build_account(c, "guide_demo@example.com")
    st = c.get("/api/guide/state").json()
    assert st["on"] is True and not st["intro_seen"] and not any(m["done"] for m in st["missions"])
    assert c.get("/api/jobs").json() == []
    s = TestClient(app)
    demo_db.build_account(s, "guide_demo_cases@example.com", scenarios=True)
    assert s.get("/api/guide/state").json()["on"] is False and len(s.get("/api/jobs").json()) == len(demo_db.CASES)


def test_remove_old_beta_accounts_only():
    """예전 베타 계정만 지우고 다른 계정은 그대로 둔다 (python -m app.guide --remove-old-beta)."""
    _join("beta1@example.com", "work")
    _join("owner3@example.com", "quit")
    keep = _join("guide_keep@example.com", "work")
    assert guide.remove_old_beta() == 2
    assert keep.get("/api/me").status_code == 200
    login = TestClient(app).post("/api/auth/login", json={"email": "beta1@example.com", "password": "test1234"})
    assert login.status_code != 200


def test_demo_mode_files_for_every_account(monkeypatch):
    """데모 모드면 안내가 꺼진 계정(시험 계정 등)도 업로드 칸마다 데모 자료 전체를, 그 칸에 맞는 것부터 받는다."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    c = _join("guide_demo_mode@example.com", "work")
    st = c.post("/api/guide/mark", json={"key": "off"}).json()
    assert st["on"] is False and st["demo"] is True and st["job_fill"]
    assert all(len(st["files"][k]) == len(guide.DEMO) for k in guide.UPLOAD_INPUTS)
    assert "근로계약서" in st["files"]["contractFile"][0]["label"] and "채용공고" in st["files"]["seekFile"][0]["label"]
    assert all(c.get(f["url"]).status_code == 200 for f in st["files"]["evFile"])
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert "files" not in c.get("/api/guide/state").json()  # 데모 모드를 끄면 업로드는 바로 내 파일 고르기


def test_demo_files_cover_every_store():
    """데모 자료: 시험 데이터의 알바 5곳마다 채용공고, 계약서, 근무표, 명세서, 입금 내역, 메시지가 있고 파일이 실제로 있다.
    채용공고마다 그 가게 조건으로 칸을 채운다. 사진 속 금액은 지금 계산과 같다."""
    from app import demo_db
    assert len(guide.DEMO) == 30 and all((guide.MATERIAL / n).is_file() for n, _, _ in guide.DEMO)
    for store in ("행복편의점", "가상분식", "가상카페", "가상베이커리", "가상치킨"):
        assert len([1 for _, label, _ in guide.DEMO if label.startswith(store + " ")]) == 6, store
    postings = [n for n, _, fit in guide.DEMO if fit == "seekFile"]
    assert len(postings) == 5 and all(n in guide.FILLS for n in postings)
    demo_db.check_images()  # 사진이 모두 있고 금액이 계산과 같지 않으면 멈춘다
