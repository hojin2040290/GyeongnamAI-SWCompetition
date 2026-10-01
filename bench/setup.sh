#!/bin/bash
# GPU 서버에서 벤치마크 준비 (한 번만). 저장 폴더(/home/work/llm_alba)에 저장소와 벤치마크용 가상환경을 만든다.
# 사용: LAW_OC=법제처키 bash setup.sh   (LAW_OC가 없으면 법 기준표 없이 시험해요)
set -e
BASE=/home/work/llm_alba
REPO=https://github.com/hojin2040290/GyeongnamAI-SWCompetition.git
if [ -d "$BASE/app/.git" ]; then
  git -C "$BASE/app" pull --ff-only
else
  git clone "$REPO" "$BASE/app"
fi
# 앱 코드를 돌릴 가상환경 (vLLM 가상환경은 건드리지 않는다)
if [ ! -x "$BASE/bench-venv/bin/python" ]; then
  "$BASE/venv/bin/python" -m venv "$BASE/bench-venv"
fi
"$BASE/bench-venv/bin/pip" install -q -r "$BASE/app/requirements.txt"
# 법 기준표: 시험 데이터가 평소 DB(app/data/app.db)에서 복사해 쓴다
if [ -n "$LAW_OC" ]; then
  (cd "$BASE/app" && LAW_OC="$LAW_OC" "$BASE/bench-venv/bin/python" -m app.law.fetch)
else
  echo "LAW_OC가 없어 법 기준표 없이 시험해요 (모든 모델이 같은 조건)."
fi
echo "준비 끝. 다음: cd $BASE/app && $BASE/venv/bin/python bench/run.py --preflight"
