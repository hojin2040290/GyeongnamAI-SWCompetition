"""업로드 파일 원본 저장. 압축이나 변환 없이 받은 그대로 보관한다.

모든 증거 원본은 UPLOAD_DIR(기본 data/uploads)/<사용자 번호>/ 아래에 저장한다.
파일 이름에 고유값을 붙이고, 같은 이름의 파일이 있으면 덮어쓰지 않고 멈춘다.
"""
import hashlib
import re
import uuid
from pathlib import Path

from app.config import BASE_DIR, REPORT_DIR, UPLOAD_DIR


def save_original(user_id: int, filename: str, data: bytes) -> tuple[str, str, int]:
    """저장 경로, sha256, 크기를 돌려준다."""
    safe = re.sub(r"[^\w.\-가-힣]", "_", filename or "file")[-80:]
    folder = UPLOAD_DIR / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}_{safe}"
    with open(path, "xb") as f:  # x: 이미 있으면 오류 (덮어쓰기 금지)
        f.write(data)
    return str(path), hashlib.sha256(data).hexdigest(), len(data)


def save_report(user_id: int, job_id: int, stamp: str, doc: str) -> str:
    """상담 사전 자료를 REPORT_DIR에 새 파일로 저장한다 (같은 초에 여러 번 만들어도 덮어쓰지 않음)."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"report_{user_id}_{job_id}_{stamp}_{uuid.uuid4().hex[:6]}.html"
    with open(path, "x", encoding="utf-8") as f:
        f.write(doc)
    return str(path)


def locate(stored: str, base: Path = UPLOAD_DIR) -> Path:
    """저장해 둔 경로의 파일. 프로젝트 폴더를 옮겨 경로가 달라졌으면 저장 폴더(base)에서 같은 파일을 찾는다."""
    p = Path(stored)
    candidates = [p, base / p.parent.name / p.name, base / p.name]
    if not p.is_absolute():
        candidates.insert(1, BASE_DIR / p)  # 예전에 상대 경로로 저장된 기록
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"원본 파일을 찾을 수 없어요: {p.name}")


def read(path: str) -> bytes:
    return locate(path).read_bytes()
