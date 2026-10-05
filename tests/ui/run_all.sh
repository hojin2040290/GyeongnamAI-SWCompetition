#!/bin/bash
# 화면 확인(회귀 시험) 전부 실행: 스크립트마다 새 DB로 서버를 띄우고, 390px 화면에서 사람이 누르는 순서대로 확인한다.
# 서버는 띄운 프로세스 번호(PID)로만 끈다. 캡처와 로그는 임시 폴더에 남는다 (data/는 건드리지 않음).
# 사용: bash tests/ui/run_all.sh            (전부)
#       bash tests/ui/run_all.sh part7 part9 (일부만)
#       JOBS=1 bash tests/ui/run_all.sh      (한 번에 하나씩. 기본은 JOBS=3: 파트마다 다른 포트로 동시에 돌린다)
# 준비: cd tests/ui && npm install && npx playwright install chromium
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; UI="$ROOT/tests/ui"
PY="${PYTHON:-python3}"; PORT="${PORT:-8897}"; JOBS="${JOBS:-3}"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/albajikimi_ui_XXXX")"
[ -z "${CHROMIUM_PATH:-}" ] && [ -x /opt/pw-browsers/chromium-1194/chrome-linux/chrome ] && export CHROMIUM_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome
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
  "part15 true  3 -            -"
  "part16 true  1 FIRST_GUIDE=true -"
  "part17 true  1 DEMO_MODE=true -"
  "part18 auto  0 LLM_ENABLED=true,LLM_MODEL=albajikimi,LLM_BASE_URL=http://127.0.0.1:9/v1,LLM_TIMEOUT=3 -"
  "part19 true  2 -            -"
  "part20 true  2 -            -"
)

run() {  # 결과는 $D/summary.txt에 남기고, 다 끝난 뒤 PLAN 순서대로 출력한다
  local port=$1; shift
  local name=$1 fake=$2 delay=$3 srv=$4 env=$5 D="$OUT/$1" t0=$SECONDS; mkdir -p "$D"
  local BASE="http://localhost:$port"
  [ "$srv" = "-" ] && srv=""; [ "$env" = "-" ] && env=""
  srv=${srv//,/ }  # 서버 설정이 여럿이면 쉼표로 이어 적는다 (PLAN 한 줄은 띄어쓰기로 칸을 나누므로)
  if [ "$name" = part10 ]; then  # AI 없이 저장된 대기 결과가 있는 DB
    DB_PATH="$D/app.db" UPLOAD_DIR="$D/up" REPORT_DIR="$D/rep" LLM_FAKE=false LLM_FAKE_DELAY=0 \
      PYTHONPATH=. "$PY" "$UI/stale_check.py" > /dev/null 2>&1
  fi
  # 처음 쓰는 사람 체험 안내는 part16에서만 켠다 (안내 창이 다른 파트의 클릭을 가리지 않게). 파트별 설정($srv)은 맨 뒤에 둬 기본값을 이긴다
  env FIRST_GUIDE=false LAW_REFRESH_ON_START=false DB_PATH="$D/app.db" UPLOAD_DIR="$D/up" REPORT_DIR="$D/rep" LLM_FAKE=$fake LLM_ENABLED=false \
    LLM_FAKE_DELAY=$delay $srv "$PY" -m uvicorn app.main:app --port "$port" > "$D/server.log" 2>&1 &
  local pid=$!  # 띄운 서버의 프로세스 번호 (끝나면 이 번호로만 끈다)
  for _ in $(seq 1 50); do curl -s -o /dev/null "$BASE/api/version" && break; sleep 0.2; done
  (cd "$D" && env $env BASE="$BASE" DB="$D/app.db" node "$UI/$name.js" > "$D/out.txt" 2>&1)
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  local line; line=$(grep '^== ' "$D/out.txt" || echo "== 중단됨")
  { echo "$name: ${line#== } ($((SECONDS - t0))초)"
    grep -E "실패:|중단" "$D/out.txt" | head -10 | sed 's/^/    /'
    grep -E "Traceback|처리 중 오류" "$D/server.log" | head -3 | sed 's/^/    서버 로그: /'; } > "$D/summary.txt"
}

T0=$SECONDS; n=0; NAMES=()
for row in "${PLAN[@]}"; do
  set -- $row
  if [ -n "$SELECT" ] && [[ " $SELECT " != *" $1 "* ]]; then continue; fi
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done  # 동시에 JOBS개까지
  run $((PORT + n)) "$@" & n=$((n + 1)); NAMES+=("$1")
done
wait
for name in "${NAMES[@]}"; do
  cat "$OUT/$name/summary.txt" 2>/dev/null || echo "$name: 결과 없음"
  grep -qE "실패 0" "$OUT/$name/out.txt" 2>/dev/null || FAILED=1
done
echo "모두 $((SECONDS - T0))초 (동시에 ${JOBS}개). 캡처와 로그: $OUT"
exit $FAILED
