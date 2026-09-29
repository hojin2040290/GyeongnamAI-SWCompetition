"""AI 모델 호출 (vLLM, OpenAI 호환). 아직 연결하지 않았다.

LLM_ENABLED=true로 바꾸고 vLLM 주소를 .env에 넣으면 이 파일만 채워서 연결한다.
연결 전에는 available()이 False를 돌려주고, 각 기능은 AI 없이 동작하는 방식을 쓴다.
"""
from app.config import LLM_BASE_URL, LLM_ENABLED, LLM_MODEL


def available() -> bool:
    return LLM_ENABLED and bool(LLM_MODEL)


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """연결 후 구현: openai 라이브러리로 LLM_BASE_URL에 요청."""
    if not available():
        raise RuntimeError("AI 모델이 아직 연결되지 않았어요")
    from openai import OpenAI  # 연결할 때 requirements에 openai 추가
    client = OpenAI(base_url=LLM_BASE_URL, api_key="none")
    resp = client.chat.completions.create(model=LLM_MODEL, messages=messages, tools=tools or None)
    return resp.choices[0].message.model_dump()
