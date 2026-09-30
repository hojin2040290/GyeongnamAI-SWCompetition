"""환경 설정. .env 파일을 읽어 설정값을 제공한다 (외부 라이브러리 없이)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_line(line: str) -> tuple[str, str] | None:
    """KEY=VALUE 한 줄. 앞의 export, 값을 감싼 따옴표, 뒤의 # 설명을 허용한다."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip().removeprefix("export ").strip()
    value = value.strip()
    if value[:1] in ("\"", "'") and value.find(value[0], 1) > 0:
        value = value[1:value.find(value[0], 1)]  # 따옴표 안만 (뒤에 설명이 붙어도)
    elif " #" in value:
        value = value.split(" #", 1)[0].strip()
    return (key, value) if key else None


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: 파일 맨 앞 BOM 제거
        parsed = _parse_line(line)
        if not parsed:
            continue
        key, value = parsed
        if not os.environ.get(key):  # 터미널에 빈 값으로 잡혀 있어도 .env 값을 쓴다
            os.environ[key] = value


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

# AI 응답 대기 중인 일을 다시 맡기는 간격(분)과 하루 최대 횟수 (AI가 연결돼 있을 때만)
AI_RETRY_MIN = int(os.getenv("AI_RETRY_MIN", "10"))
AI_RETRY_PER_DAY = int(os.getenv("AI_RETRY_PER_DAY", "3"))

# 시연용 기능 (true일 때만 /api/dev/daily-check가 열림. 외부에 여는 서버에서는 false로 둔다)
DEV_TOOLS = os.getenv("DEV_TOOLS", "false").lower() == "true"

# 정기 점검 시각 (매일 이 시각에 에이전트가 스스로 시작)
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "9"))

for d in (UPLOAD_DIR, REPORT_DIR, DB_PATH.parent):
    Path(d).mkdir(parents=True, exist_ok=True)
