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


@app.get("/v1/models")
def models():
    return {"data": [{"id": "bench"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    payload = await req.json()
    data = fake.respond(payload)
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
    uvicorn.run(app, host="127.0.0.1", port=ap.parse_args().port, log_level="warning")
