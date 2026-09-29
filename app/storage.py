"""업로드 파일 원본 저장. 압축이나 변환 없이 받은 그대로 보관한다."""
import hashlib
import re
import uuid
from pathlib import Path

from app.config import UPLOAD_DIR


def save_original(user_id: int, filename: str, data: bytes) -> tuple[str, str, int]:
    """저장 경로, sha256, 크기를 돌려준다."""
    safe = re.sub(r"[^\w.\-가-힣]", "_", filename or "file")[-80:]
    folder = UPLOAD_DIR / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}_{safe}"
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest(), len(data)


def read(path: str) -> bytes:
    return Path(path).read_bytes()
