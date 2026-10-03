#!/bin/bash
# 화면 확인(회귀 시험) 전부 실행: 스크립트마다 새 DB로 서버를 띄우고, 390px 화면에서 사람이 누르는 순서대로 확인한다.
# 서버는 띄운 프로세스 번호(PID)로만 끈다. 캡처와 로그는 임시 폴더에 남는다 (data/는 건드리지 않음).
# 사용: bash tests/ui/run_all.sh            (전부)
#       bash tests/ui/run_all.sh part7 part9 (일부만)
# 준비: cd tests/ui && npm install && npx playwright install chromium
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; UI="$ROOT/tests/ui"
PY="${PYTHON:-python3}"; PORT="${PORT:-8897}"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/albajikimi_ui_XXXX")"
[ -z "${CHROMIUM_PATH:-}" ] && [ -x /opt/pw-browsers/chromium-1194/chrome-linux/chrome ] && export CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome
export BASE="http://localhost:$PORT"
SELECT="$*"; FAILED=0
cd "$ROOT"

# 이름  가짜AI  대기초  서버에 더 줄 설정  스크립트에 줄 설정
PLAN=(
  "part1  true  0 -            -"
  "part2  true  0 -            -"
  "part3  false 0 -            -"
  "part5  true  0 DEV_TOOLS=true MODE=fake"
  "part6  true  1 -            -"
  "part7  true  3 -            -"
  "part8  true  1 -            -"
  "part9  true  1 -            -"
  "part10 true  3 -            -"
  "part11 true  1 -            -"
  "part12 true  3 -            -"
  "part13 true  3 -            -"
  "part14 true  3 -            -"
)

run() {
  local name=$1 fake=$2 delay=$3 srv=$4 env=$5 D="$OUT/$1"; mkdir -p "$D"
  [ "$srv" = "-" ] && srv=""; [ "$env" = "-" ] && env=""
  if [ "$name" = part10 ]; then  # AI 없이 저장된 대기 결과가 있는 DB
    DB_PATH="$D/app.db" UPLOAD_DIR="$D/up" REPORT_DIR="$D/rep" LLM_FAKE=false LLM_FAKE_DELAY=0 \
      PYTHONPATH=. "$PY" "$UI/stale_check.py" > /dev/null 2>&1
  fi
  env $srv LAW_REFRESH_ON_START=false DB_PATH="$D/app.db" UPLOAD_DIR="$D/up" REPORT_DIR="$D/rep" LLM_FAKE=$fake LLM_ENABLED=false \
    LLM_FAKE_DELAY=$delay "$PY" -m uvicorn app.main:app --port "$PORT" > "$D/server.log" 2>&1 &
  local pid=$!  # 띄운 서버의 프로세스 번호 (끝나면 이 번호로만 끈다)
  for _ in $(seq 1 50); do curl -s -o /dev/null "$BASE/api/version" && break; sleep 0.2; done
  (cd "$D" && env $env DB="$D/app.db" node "$UI/$name.js" > "$D/out.txt" 2>&1)
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  local line; line=$(grep '^== ' "$D/out.txt" || echo "== 중단됨")
  echo "$name: ${line#== }"
  grep -E "실패:|중단" "$D/out.txt" | head -10 | sed 's/^/    /'
  grep -E "Traceback|처리 중 오류" "$D/server.log" | head -3 | sed 's/^/    서버 로그: /'
  grep -qE "실패 0" "$D/out.txt" || FAILED=1
}

for row in "${PLAN[@]}"; do
  set -- $row
  if [ -n "$SELECT" ] && [[ " $SELECT " != *" $1 "* ]]; then continue; fi
  run "$@"
done
echo "캡처와 로그: $OUT"
exit $FAILED
