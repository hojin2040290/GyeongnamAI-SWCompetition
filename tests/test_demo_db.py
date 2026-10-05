"""시험 데이터 만들기(app/demo_db.py): 지금 코드에서 동작하고, 평소 데이터는 건드리지 않으며, 사진 속 금액이 계산과 같은지."""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import demo_db

ROOT = Path(__file__).resolve().parent.parent


def test_case_images_match_records():
    """알바마다 올릴 사진이 모두 있고, 사진 속 금액(manifest)이 지금 코드의 계산과 같다."""
    demo_db.check_images()  # 맞지 않으면 SystemExit


def test_demo_db_builds_in_test_folder_only():
    target = ROOT / "data" / "test_pytest"
    shutil.rmtree(target, ignore_errors=True)
    app_db = ROOT / "data" / "app.db"
    before = app_db.stat().st_mtime if app_db.exists() else None
    try:
        out = subprocess.run([sys.executable, "-m", "app.demo_db", "--dir", "data/test_pytest"], cwd=ROOT,
                             capture_output=True, text=True, timeout=180)
        assert out.returncode == 0, out.stderr[-800:]
        assert (target / "app.db").exists() and not list((target / "uploads").rglob("*.png"))  # 사진은 미리 올리지 않음 (시연 때 업로드)
        assert "계정마다 사례 하나" in out.stdout
        with sqlite3.connect(target / "app.db") as db:  # 계정 5개, 계정 n에 사례 n의 일하는 곳과 출퇴근 기록, 체험 안내 시작
            assert db.execute("select count(*) from user").fetchone()[0] == 5
            rows = db.execute("select u.email, j.name from user u join job j on j.user_id=u.id order by u.id").fetchall()
            assert rows == [(e, c["job"]["name"]) for e, c in zip(demo_db.EMAILS, demo_db.CASES)]
            assert all(db.execute("select count(*) from workrecord where user_id=?", (u,)).fetchone()[0] > 0
                       for (u,) in db.execute("select id from user"))
            assert db.execute("select count(*) from guidestate").fetchone()[0] == 5
        again = subprocess.run([sys.executable, "-m", "app.demo_db", "--dir", "data/test_pytest"], cwd=ROOT,
                               capture_output=True, text=True)
        assert again.returncode != 0 and "--force" in again.stderr  # 있는 데이터를 덮어쓰지 않는다
        bad = subprocess.run([sys.executable, "-m", "app.demo_db", "--dir", "data"], cwd=ROOT, capture_output=True, text=True)
        assert bad.returncode != 0  # data 폴더 자체에는 만들지 않는다
        assert (app_db.stat().st_mtime if app_db.exists() else None) == before  # 평소 DB는 그대로
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_server_prepares_test_data_when_missing(monkeypatch):
    """TEST_DATA=true로 켰는데 시험 데이터가 없으면 서버가 만든다 (python -m app.demo_db를 따로 안 해도 됨).
    이미 있는 시험 데이터는 건드리지 않는다."""
    import logging
    import sqlite3

    from app import config, main
    target = ROOT / "data" / "test_pytest_auto"
    shutil.rmtree(target, ignore_errors=True)
    monkeypatch.setattr(config, "TEST_DATA_DIR", target)
    monkeypatch.setattr(config, "DB_PATH", (target / "app.db").resolve())
    log = logging.getLogger("test")
    try:
        main.prepare_test_data(log)
        db = target / "app.db"
        assert [r[0] for r in sqlite3.connect(db).execute("select email from user order by id")] == [
            "test@example.com", "test2@example.com", "test3@example.com", "test4@example.com", "test5@example.com"]
        with sqlite3.connect(db) as c:  # 계정 5개, 계정마다 사례 하나 (체험 안내 시작)
            assert c.execute("select count(*) from job").fetchone() == (5,)
            assert c.execute("select count(*) from guidestate").fetchone() == (5,)
            c.execute("update guidestate set marks='[\"intro\"]' where user_id=(select id from user where email='test2@example.com')")
        main.prepare_test_data(log)  # 이미 있으면 다시 만들지 않는다 (시연하며 진행한 상태는 그대로)
        assert sqlite3.connect(db).execute("select count(*) from guidestate where marks like '%intro%'").fetchone() == (1,)
        with sqlite3.connect(db) as c:  # 예전 코드로 만든 시험 DB (계정이 test@example.com 하나뿐)
            c.execute("delete from guidestate where user_id in (select id from user where email!='test@example.com')")
            c.execute("delete from job where user_id in (select id from user where email!='test@example.com')")
            c.execute("delete from user where email!='test@example.com'")
        main.prepare_test_data(log)  # 없는 시험 계정만 더한다
        with sqlite3.connect(db) as c:
            assert c.execute("select count(*) from user").fetchone() == (5,)
            assert c.execute("select count(*) from guidestate").fetchone() == (5,)
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_outside_venv_stops_before_deleting(monkeypatch, tmp_path):
    """가상환경 밖의 파이썬(서버 라이브러리 없음)으로 --force를 하면 시험 데이터를 지우기 전에 멈춘다."""
    import importlib.util
    target = ROOT / "data" / "test_pytest_novenv"
    shutil.rmtree(target, ignore_errors=True)
    (target / "uploads").mkdir(parents=True)
    (target / "app.db").write_bytes(b"keep")
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m == "fastapi" else real(m, *a))
    monkeypatch.setattr(sys, "argv", ["demo_db", "--force", "--dir", "data/test_pytest_novenv"])
    try:
        try:
            demo_db.main()
            raise AssertionError("멈추지 않음")
        except SystemExit as e:
            assert ".venv" in str(e) and "fastapi" in str(e)
        assert (target / "app.db").read_bytes() == b"keep"  # 지우지 않았다
    finally:
        shutil.rmtree(target, ignore_errors=True)
