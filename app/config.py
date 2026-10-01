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
DATA_DIR = BASE_DIR / "data"  # 기록(DB), 증거 원본, 상담 사전 자료를 모두 이 폴더 아래에 둔다
# TEST_DATA=true: 시험 데이터(data/test, python -m app.demo_db로 만듦)로 실행한다. 평소 데이터(data/app.db 등)는 건드리지 않는다
TEST_DATA = os.getenv("TEST_DATA", "false").strip().lower() == "true"
TEST_DATA_DIR = DATA_DIR / "test"


def _data_path(env: str, default: Path) -> Path:
    """저장 위치. .env에 상대 경로(예: data/uploads)를 적어도 서버를 어디서 켰는지와 상관없이 프로젝트 폴더 기준으로 본다."""
    value = os.getenv(env, "").strip()
    path = Path(value).expanduser() if value else default
    return (path if path.is_absolute() else BASE_DIR / path).resolve()


_BASE = TEST_DATA_DIR if TEST_DATA else DATA_DIR  # DB_PATH 등을 따로 적으면 그 값을 쓴다
DB_PATH = _data_path("DB_PATH", _BASE / "app.db")
UPLOAD_DIR = _data_path("UPLOAD_DIR", _BASE / "uploads")
REPORT_DIR = _data_path("REPORT_DIR", _BASE / "reports")
LAW_PARAMS_PATH = BASE_DIR / "data" / "law_params.json"
TIMEZONE = "Asia/Seoul"

# AI 모델 (아직 연결하지 않음. true로 바꾸면 llm/client.py가 vLLM을 호출)
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() == "true"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", "")  # 사진 읽기용 모델 (비우면 LLM_MODEL 사용)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")            # vLLM에 키를 걸었을 때만
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
# 가짜 AI (시험용): 실제 모델 대신 app/llm/fake.py가 '테스트 답변입니다 (...)'로 답한다.
# auto(기본): 실제 모델 설정(LLM_ENABLED=true와 LLM_MODEL)이 없으면 가짜 AI를 쓴다. true: 늘 가짜 AI. false: 가짜 AI를 쓰지 않음
def fake_mode(value: str, enabled: bool, model: str) -> bool:
    value = (value or "auto").strip().lower()
    return value == "true" or (value == "auto" and not (enabled and model))


LLM_FAKE = fake_mode(os.getenv("LLM_FAKE", "auto"), LLM_ENABLED, LLM_MODEL)
# 가짜 AI가 요청을 받고 답하기까지 기다리는 시간(초). 실제 모델처럼 기다리는 모습(로딩 표시)을 확인하려고 둔다
LLM_FAKE_DELAY = max(0.0, float(os.getenv("LLM_FAKE_DELAY", "3")))

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

# 로그인 시도 제한: 같은 이메일로 LOGIN_MAX_FAILS번, 같은 접속 주소로 LOGIN_IP_MAX_FAILS번 틀리면
# LOGIN_LOCK_MIN분 동안 로그인을 막는다 (틀린 횟수도 LOGIN_LOCK_MIN분 안의 것만 센다)
LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
LOGIN_IP_MAX_FAILS = int(os.getenv("LOGIN_IP_MAX_FAILS", "20"))
LOGIN_LOCK_MIN = int(os.getenv("LOGIN_LOCK_MIN", "15"))

# 서버를 켤 때 법제처 API로 법령이 바뀌었는지 확인하고 바뀐 법령을 받아 법 기준표에 저장한 뒤 시작한다 (LAW_OC 필요)
LAW_REFRESH_ON_START = os.getenv("LAW_REFRESH_ON_START", "true").strip().lower() != "false"

# 정기 점검 시각 (매일 이 시각에 에이전트가 스스로 시작)
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", "9"))

for d in (UPLOAD_DIR, REPORT_DIR, DB_PATH.parent):
    Path(d).mkdir(parents=True, exist_ok=True)
