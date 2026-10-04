#!/bin/bash
# 알바지킴이용 vLLM 잠깐 끄기 (GPU 서버). 다시 켜기: bash /home/work/alba/start_vllm.sh
# 사용: cp /home/work/alba/app/deploy/stop_vllm.sh /home/work/alba/ && bash /home/work/alba/stop_vllm.sh
# start_vllm.sh가 남긴 번호(vllm.pid)의 프로세스에 끄라고 알리고, 정말 꺼질 때까지 기다린다.
BASE=/home/work/alba
PID=$(cat "$BASE/vllm.pid" 2> /dev/null)
if [ -z "$PID" ] || ! kill -0 "$PID" 2> /dev/null; then
  echo "vllm.pid의 프로세스가 없어요. 남은 vLLM이 있는지: pgrep -af \"vllm serve\""; exit 0
fi
kill "$PID"  # 정상 종료 요청 (GPU 메모리를 정리하고 꺼진다)
for _ in $(seq 1 60); do
  if ! kill -0 "$PID" 2> /dev/null && ! pgrep -f "vllm serve" > /dev/null; then
    rm -f "$BASE/vllm.pid"
    echo "꺼졌어요. GPU 메모리:"; nvidia-smi --query-gpu=memory.used --format=csv,noheader
    exit 0
  fi
  sleep 1
done
echo "1분이 지나도 꺼지지 않았어요. 남은 프로세스: "; pgrep -af "vllm serve"
echo "어떤 프로세스인지 확인한 뒤 직접 끄세요 (kill 번호)"; exit 1
