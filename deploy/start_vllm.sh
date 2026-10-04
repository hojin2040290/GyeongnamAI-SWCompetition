#!/bin/bash
# 알바지킴이용 vLLM 켜기 (GPU 서버). 모델: Qwen3.8-27B (bench/ 비교 결과로 선정, 2026-10-03)
# 실행 옵션은 Qwen 공식 GitHub의 vllm serve 예시와 vLLM 레시피를 따른다 (bench/models.json의 qwen38_27b docs).
# 사용: cp /home/work/alba/app/deploy/start_vllm.sh /home/work/alba/ && bash /home/work/alba/start_vllm.sh
#       켜지고 요청을 받을 수 있을 때까지 기다렸다가 '준비됨'을 띄운다. 끄기: bash /home/work/alba/stop_vllm.sh
# 모델 파일(hf)과 컴파일 결과(~/.cache/vllm)를 다시 쓰므로 두 번째부터는 처음보다 빨리 켜진다.
BASE=/home/work/alba
if pgrep -f "vllm serve" > /dev/null; then
  echo "vLLM이 이미 켜져 있어요 (pgrep -af \"vllm serve\"로 확인). 다시 켜려면 먼저 bash $BASE/stop_vllm.sh"; exit 1
fi
source "$BASE/venv/bin/activate"
export HF_HOME="$BASE/hf"
# --served-model-name은 앱 .env의 LLM_MODEL과 같아야 한다
# --default-chat-template-kwargs: 생각 모드를 서버에서 끈다 (앱의 LLM_EXTRA_BODY를 빠뜨려도 꺼지게)
# --gpu-memory-utilization 0.92: 모델을 올리고 남는 자리를 KV 캐시로 더 쓰되, 사진 읽기 등을 위한 여유를 남긴다 (0.85에서 올림)
# --max-model-len 32768은 줄이지 않는다: 에이전트가 반복마다 도구 결과를 쌓아 대화가 길어진다
# --host 127.0.0.1은 그대로: 바깥에 열지 않고 SSH 터널로만 쓴다 (모델 API에는 비밀번호가 없다)
nohup vllm serve Qwen/Qwen3.8-27B \
  --served-model-name albajikimi \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 32768 --gpu-memory-utilization 0.92 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --limit-mm-per-prompt '{"image":2}' \
  > "$BASE/vllm.log" 2>&1 &
echo $! > "$BASE/vllm.pid"
PID=$(cat "$BASE/vllm.pid")
echo "켜는 중 (번호 $PID). 요청을 받을 수 있을 때까지 기다려요. 로그: tail -f $BASE/vllm.log"
for _ in $(seq 1 360); do  # 최대 30분
  if curl -s 127.0.0.1:8000/v1/models > /dev/null; then echo "준비됨: 앱에서 다시 쓸 수 있어요"; exit 0; fi
  if ! kill -0 "$PID" 2> /dev/null; then echo "켜다가 멈췄어요. 로그 끝부분:"; tail -20 "$BASE/vllm.log"; exit 1; fi
  sleep 5
done
echo "30분이 지나도 준비되지 않았어요. 로그를 보세요: tail -50 $BASE/vllm.log"; exit 1
