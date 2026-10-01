"""시험 DB 만들기(app/demo_db.py)가 지금 코드에서 동작하고, 계산 결과를 보여 주는지."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_demo_db_builds(tmp_path):
    db = tmp_path / "demo.db"
    out = subprocess.run([sys.executable, "-m", "app.demo_db", "--db", str(db)], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert db.exists() and "합계" in out.stdout and "주휴수당" in out.stdout and "실수로 누른" in out.stdout
    again = subprocess.run([sys.executable, "-m", "app.demo_db", "--db", str(db)], cwd=ROOT, capture_output=True, text=True)
    assert again.returncode != 0 and "--force" in again.stderr  # 있는 파일을 덮어쓰지 않는다
