#!/bin/bash
# 알바지킴이용 vLLM 켜기 (GPU 서버). 모델: Qwen3.8-27B (bench/ 비교 결과로 선정, 2026-10-03)
# 실행 옵션은 Qwen 공식 GitHub의 vllm serve 예시와 vLLM 레시피를 따른다 (bench/models.json의 qwen38_27b docs).
# 사용: cp /home/work/alba/app/deploy/start_vllm.sh /home/work/alba/ && bash /home/work/alba/start_vllm.sh
BASE=/home/work/alba
source "$BASE/venv/bin/activate"
export HF_HOME="$BASE/hf"
# --served-model-name은 앱 .env의 LLM_MODEL과 같아야 한다
# --default-chat-template-kwargs: 생각 모드를 서버에서 끈다 (앱의 LLM_EXTRA_BODY를 빠뜨려도 꺼지게)
nohup vllm serve Qwen/Qwen3.8-27B \
  --served-model-name albajikimi \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 32768 --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --limit-mm-per-prompt '{"image":2}' \
  > "$BASE/vllm.log" 2>&1 &
echo $! > "$BASE/vllm.pid"
echo "켜는 중 (번호 $(cat "$BASE/vllm.pid")). 로그: tail -f $BASE/vllm.log"
