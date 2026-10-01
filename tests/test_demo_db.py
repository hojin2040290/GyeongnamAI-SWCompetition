"""시험 데이터 만들기(app/demo_db.py): 지금 코드에서 동작하고, 평소 데이터는 건드리지 않으며, 사진 속 금액이 계산과 같은지."""
import shutil
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
        assert (target / "app.db").exists() and len(list((target / "uploads").rglob("*.png"))) == 14
        for name in ("행복편의점 도계점", "가상분식 시험점", "가상카페 시험점", "가상베이커리 시험점", "가상치킨 시험점"):
            assert name in out.stdout
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
        assert sqlite3.connect(db).execute("select email from user").fetchall() == [("test@example.com",)]
        with sqlite3.connect(db) as c:
            c.execute("update job set name='바꾼 이름' where id=2")
        main.prepare_test_data(log)  # 이미 있으면 다시 만들지 않는다
        assert sqlite3.connect(db).execute("select name from job where id=2").fetchone() == ("바꾼 이름",)
    finally:
        shutil.rmtree(target, ignore_errors=True)
