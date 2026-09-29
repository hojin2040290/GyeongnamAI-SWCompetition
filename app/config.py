"""환경 설정. .env 파일을 읽어 설정값을 제공한다 (외부 라이브러리 없이)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "app.db")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(BASE_DIR / "data" / "reports")))
LAW_PARAMS_PATH = BASE_DIR / "data" / "law_params.json"
TIMEZONE = "Asia/Seoul"

# AI 모델 (아직 연결하지 않음. true로 바꾸면 llm/client.py가 vLLM을 호출)
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() == "true"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", "")  # 사진 읽기용 모델 (비우면 LLM_MODEL 사용)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")            # vLLM에 키를 걸었을 때만
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))

# 외부 API 키 (없으면 해당 기능은 건너뜀)
LAW_OC = os.getenv("LAW_OC", "")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# 출퇴근 실수 막기 (법 기준값이 아닌 화면 동작 설정)
PUNCH_CONFIRM_SEC = int(os.getenv("PUNCH_CONFIRM_SEC", "60"))       # 출근 뒤 이 시간 안에 퇴근하면 한 번 더 확인
OPEN_RECORD_ALERT_HOURS = int(os.getenv("OPEN_RECORD_ALERT_HOURS", "16"))  # 퇴근 없이 이 시간이 지나면 알림

# 정기 점검 시각 (매일 이 시각에 에이전트가 스스로 시작)
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "9"))

for d in (UPLOAD_DIR, REPORT_DIR, DB_PATH.parent):
    Path(d).mkdir(parents=True, exist_ok=True)
