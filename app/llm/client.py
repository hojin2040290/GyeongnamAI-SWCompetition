"""AI 모델 호출 (vLLM, OpenAI 호환 API).

.env에 LLM_ENABLED=true, LLM_BASE_URL(예: http://localhost:8000/v1), LLM_MODEL을 넣으면 연결된다.
사진 읽기는 비전 모델(LLM_VISION_MODEL, 비우면 LLM_MODEL)이 글자 인식과 항목 정리를 한 번에 한다.
연결 전에는 available()이 False이고, 각 기능은 AI 없이 동작하는 방식(확인 중)으로 돌아간다.
"""
import base64
import json
import re

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT, LLM_VISION_MODEL


class LLMError(RuntimeError):
    """모델 호출이나 응답 해석에 실패했을 때."""


def available() -> bool:
    return LLM_ENABLED and bool(LLM_MODEL)


def vision_available() -> bool:
    return LLM_ENABLED and bool(LLM_VISION_MODEL or LLM_MODEL)


def status() -> dict:
    return {"enabled": LLM_ENABLED, "judge": available(), "vision": vision_available(),
            "model": LLM_MODEL, "vision_model": LLM_VISION_MODEL or LLM_MODEL}


def _post(payload: dict) -> dict:
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
    try:
        r = httpx.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", json=payload, headers=headers,
                       timeout=LLM_TIMEOUT)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"AI 모델에 연결하지 못했어요 ({type(exc).__name__})") from exc
    return r.json()


def chat(messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
    """대화 한 번. 모델이 돌려준 메시지(content, tool_calls)를 그대로 돌려준다."""
    if not available():
        raise LLMError("AI 모델이 아직 연결되지 않았어요")
    payload = {"model": model or LLM_MODEL, "messages": messages, "temperature": 0}
    if tools:
        payload["tools"] = tools
    data = _post(payload)
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError("AI 모델 응답 형식이 달라요") from exc


def parse_json(text: str):
    """모델 답에서 JSON만 꺼낸다 (```json 감싸기나 앞뒤 설명이 붙어도)."""
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    pairs = sorted((("{", "}"), ("[", "]")), key=lambda p: (text.find(p[0]) == -1, text.find(p[0])))
    for opener, closer in pairs:  # 먼저 나온 괄호부터 (배열 안의 객체만 꺼내지 않도록)
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("AI 모델 답을 JSON으로 읽지 못했어요")


def ask_json(system: str, user: str):
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    return parse_json(msg.get("content") or "")


def read_image_json(prompt: str, data: bytes, mime: str):
    """사진을 비전 모델에 보내고 JSON 답을 받는다."""
    if not vision_available():
        raise LLMError("사진을 읽을 AI 모델이 아직 연결되지 않았어요")
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                             {"type": "image_url", "image_url": {"url": url}}]}]
    payload = {"model": LLM_VISION_MODEL or LLM_MODEL, "messages": messages, "temperature": 0}
    data_ = _post(payload)
    try:
        return parse_json(data_["choices"][0]["message"]["content"])
    except (KeyError, IndexError) as exc:
        raise LLMError("AI 모델 응답 형식이 달라요") from exc
