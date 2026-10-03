"""(개발자용) 벤치마크 흐름을 GPU 없이 확인하는 가짜 OpenAI 호환 서버. 앱의 가짜 AI(app/llm/fake.py)로 답한다.

사용: python bench/run.py --serve-cmd "python bench/fake_server.py --port {port}" --suite-python python3 --out /tmp/bench_fake
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_FAKE_DELAY", "0")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from app.llm import fake  # noqa: E402

app = FastAPI()
# 모델마다 생길 수 있는 경우를 흉내 낸다 (bench/run.py의 자동 점검과 대처를 GPU 없이 확인하려고)
OPT = {"broken_vision": False,  # 사진 요청에 JSON이 아닌 글을 한도까지 되풀이 (EXAONE에서 본 모양)
       "tool_parser": "", "working_parser": "",  # 둘이 다르면 도구 호출을 파서가 못 읽은 것처럼 글로 답한다
       "think_unless": "",  # 이 chat_template_kwargs(예: skip_reasoning)를 보내지 않으면 생각 글이 답에 섞인다
       "record": ""}  # 받은 요청을 이 파일에 한 줄씩 남긴다 (생성 설정이 실제로 갔는지 확인)


@app.get("/v1/models")
def models():
    return {"data": [{"id": "bench"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    payload = await req.json()
    if OPT["record"]:
        with open(OPT["record"], "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in payload.items() if k != "messages"}, ensure_ascii=False) + "\n")
    data = fake.respond(payload)
    msg = data["choices"][0]["message"]
    if OPT["broken_vision"] and any(isinstance(m.get("content"), list) for m in payload.get("messages", [])):
        msg["content"] = "근로계약서 내용: " + "근로시간 09:00 " * 400
        data["choices"][0]["finish_reason"] = "length"
    if OPT["working_parser"] and OPT["tool_parser"] != OPT["working_parser"] and msg.get("tool_calls"):
        msg["content"] = "<tool_call>" + json.dumps(msg.pop("tool_calls")[0]["function"], ensure_ascii=False) + "</tool_call>"
    kw = OPT["think_unless"]
    if kw and kw not in (payload.get("chat_template_kwargs") or {}):
        msg["content"] = "<think>먼저 생각해 보면 {\"임금\": \"모름\"}</think>" + (msg.get("content") or "")
    text = (data["choices"][0]["message"].get("content") or "")
    data.setdefault("usage", {"prompt_tokens": len(json.dumps(payload, ensure_ascii=False)) // 3,
                              "completion_tokens": max(1, len(text) // 2)})
    if not payload.get("stream"):
        return JSONResponse(data)

    def gen():
        for i in range(0, len(text) or 1, 20):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 20]}}]}, ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps({"choices": [], "usage": data["usage"]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--broken-vision", action="store_true", help="사진 읽기 실패를 흉내 낸다")
    ap.add_argument("--tool-parser", default="", help="run.py가 넘긴 도구 파서 이름")
    ap.add_argument("--working-parser", default="", help="이 파서일 때만 도구 호출을 읽힌다")
    ap.add_argument("--fail-parser", default="", help="이 파서로 켜면 바로 멈춘다 (켜지지 않는 경우)")
    ap.add_argument("--think-unless", default="", help="이 chat_template_kwargs를 보내야 생각 글이 사라진다")
    ap.add_argument("--record", default="", help="받은 요청을 남길 파일")
    a = ap.parse_args()
    if a.fail_parser and a.tool_parser == a.fail_parser:
        sys.exit(f"지원하지 않는 도구 파서예요: {a.tool_parser}")
    OPT.update(broken_vision=a.broken_vision, tool_parser=a.tool_parser, working_parser=a.working_parser,
               think_unless=a.think_unless, record=a.record)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
