"""증거 자료 저장 위치: 모두 data 폴더(테스트에서는 임시 폴더) 아래, 덮어쓰기 없음, 폴더를 옮겨도 찾기."""
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from app import config, evidence_check, storage
from app.db import engine, init_db
from app.models import Evidence


def test_relative_env_path_is_under_project(monkeypatch):
    """.env에 상대 경로를 적어도 서버를 켠 폴더가 아닌 프로젝트 폴더 기준으로 본다."""
    monkeypatch.setenv("UPLOAD_DIR", "data/uploads")
    monkeypatch.chdir("/")
    assert config._data_path("UPLOAD_DIR", Path("x")) == (config.BASE_DIR / "data" / "uploads").resolve()
    monkeypatch.delenv("UPLOAD_DIR")
    assert config._data_path("UPLOAD_DIR", config.DATA_DIR / "uploads") == (config.DATA_DIR / "uploads").resolve()


def test_saved_files_stay_in_folders_and_never_overwrite():
    a = Path(storage.save_original(7, "같은이름.png", b"one")[0])
    b = Path(storage.save_original(7, "같은이름.png", b"two")[0])
    assert a != b and a.read_bytes() == b"one" and b.read_bytes() == b"two"
    assert a.parent == config.UPLOAD_DIR / "7"
    r1 = Path(storage.save_report(7, 1, "20261001000000", "첫 번째"))
    r2 = Path(storage.save_report(7, 1, "20261001000000", "두 번째"))  # 같은 초에 두 번
    assert r1 != r2 and r1.read_text(encoding="utf-8") == "첫 번째" and r1.parent == config.REPORT_DIR


def test_locate_after_folder_move():
    path, _, _ = storage.save_original(8, "a.png", b"x")
    old = Path("/옛날/프로젝트/data/uploads/8") / Path(path).name  # 프로젝트를 옮기기 전 경로로 기록된 경우
    assert storage.locate(str(old)) == Path(path)
    with pytest.raises(FileNotFoundError):
        storage.locate("/없는/파일.png")


def test_evidence_check_copies_outside_files_into_data(tmp_path):
    init_db()
    outside = tmp_path / "잘못된위치" / "증거.png"
    outside.parent.mkdir()
    outside.write_bytes(b"evidence")
    with Session(engine) as s:
        ev = Evidence(user_id=9, kind="other", filename="증거.png", stored_path=str(outside),
                      sha256=hashlib.sha256(b"evidence").hexdigest(), size=8, uploaded_at=datetime(2026, 10, 1))
        s.add(ev)
        s.commit()
        ev_id = ev.id
        r = evidence_check.check_evidence(s, fix=False)
        assert any("증거.png" in x for x in r["outside"])
        r = evidence_check.check_evidence(s, fix=True)
        assert r["moved"] == 1
        moved = Path(s.get(Evidence, ev_id).stored_path)
    assert moved.parent == config.UPLOAD_DIR / "9" and moved.read_bytes() == b"evidence"
    assert outside.exists()  # 원래 파일은 지우지 않는다
    shutil.rmtree(outside.parent)
