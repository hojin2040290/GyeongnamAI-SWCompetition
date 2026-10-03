"""AI 모델 호출 (vLLM, OpenAI 호환 API).

.env에 LLM_ENABLED=true, LLM_BASE_URL(예: http://localhost:8000/v1), LLM_MODEL을 넣으면 연결된다.
사진 읽기는 비전 모델(LLM_VISION_MODEL, 비우면 LLM_MODEL)이 글자 인식과 항목 정리를 한 번에 한다.
연결 전에는 available()이 False이고, 각 기능은 AI 없이 동작하는 방식(확인 중)으로 돌아간다.
"""
import base64
import json
import re

import httpx

from app.config import (LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_EXTRA_BODY, LLM_FAKE, LLM_MAX_TOKENS, LLM_MODEL,
                        LLM_SAMPLING, LLM_TIMEOUT, LLM_VISION_MODEL)
from app.llm import fake


class LLMError(RuntimeError):
    """모델 호출이나 응답 해석에 실패했을 때."""


def available() -> bool:
    return LLM_FAKE or (LLM_ENABLED and bool(LLM_MODEL))


def vision_available() -> bool:
    return LLM_FAKE or (LLM_ENABLED and bool(LLM_VISION_MODEL or LLM_MODEL))


def status() -> dict:
    if LLM_FAKE:
        return {"enabled": True, "judge": True, "vision": True, "fake": True,
                "model": "가짜 AI (시험용)", "vision_model": "가짜 AI (시험용)"}
    return {"enabled": LLM_ENABLED, "judge": available(), "vision": vision_available(),
            "model": LLM_MODEL, "vision_model": LLM_VISION_MODEL or LLM_MODEL}


def extra_body() -> dict:
    """.env의 LLM_EXTRA_BODY (모델마다 필요한 요청 옵션). 잘못된 JSON이면 쓰지 않는다."""
    try:
        v = json.loads(LLM_EXTRA_BODY) if LLM_EXTRA_BODY else {}
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


# LLM_SAMPLING으로 바꿀 수 있는 생성 설정 (그 밖의 칸은 무시한다)
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repetition_penalty")


def sampling() -> dict:
    """.env의 LLM_SAMPLING (모델 공식 문서의 권장 생성 설정). 잘못된 JSON이면 쓰지 않는다."""
    try:
        v = json.loads(LLM_SAMPLING) if LLM_SAMPLING else {}
    except json.JSONDecodeError:
        return {}
    return {k: x for k, x in v.items() if k in SAMPLING_KEYS} if isinstance(v, dict) else {}


def _post(payload: dict) -> dict:
    payload = {**extra_body(), **payload}
    for k, v in sampling().items():
        if v is None:
            payload.pop(k, None)  # 모델 기본값(generation_config.json)을 쓴다
        else:
            payload[k] = v
    if LLM_MAX_TOKENS and "max_tokens" not in payload:
        payload["max_tokens"] = LLM_MAX_TOKENS
    if LLM_FAKE:  # 시험용 가짜 AI: 같은 응답 모양으로 답한다
        return fake.respond(payload)
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
    try:
        r = httpx.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", json=payload, headers=headers,
                       timeout=LLM_TIMEOUT)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"AI 모델에 연결하지 못했어요 ({type(exc).__name__})") from exc
    try:
        return r.json()
    except ValueError as exc:
        raise LLMError("AI 모델 응답을 읽지 못했어요") from exc


def chat(messages: list[dict], tools: list[dict] | None = None, model: str | None = None,
         force: str | None = None) -> dict:
    """대화 한 번. 모델이 돌려준 메시지(content, tool_calls)를 그대로 돌려준다.
    force: 이 도구를 반드시 부르게 한다 (반복의 마지막에 finish로 끝내게 할 때)."""
    if not available():
        raise LLMError("AI 모델이 아직 연결되지 않았어요")
    payload = {"model": model or LLM_MODEL, "messages": messages, "temperature": 0}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "function", "function": {"name": force}} if force else "auto"
    data = _post(payload)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("AI 모델 응답 형식이 달라요") from exc
    if isinstance(msg, dict) and msg.get("content"):
        msg = {**msg, "content": strip_reasoning(msg["content"])}  # 생각 글이 섞여 와도 사용자에게 보이지 않게
    return msg


# 생각 파서가 없거나 맞지 않을 때 답에 섞여 오는 생각 글 (Qwen·EXAONE <think>, Mistral [THINK], Gemma 4 thought 채널)
_REASONING = [(r"<think>", r"</think>"), (r"\[THINK\]", r"\[/THINK\]"), (r"<\|channel>thought", r"<channel\|>")]


def strip_reasoning(text: str) -> str:
    """답에서 생각 글을 뺀다. 여는 표시 없이 닫는 표시만 있으면(대화 틀이 미리 열어 둔 경우) 그 앞을 모두 뺀다."""
    text = text or ""
    for opener, closer in _REASONING:
        text = re.sub(opener + r".*?" + closer, "", text, flags=re.S)
        m = list(re.finditer(closer, text))
        if m:
            text = text[m[-1].end():]
        text = re.sub(opener + r".*", "", text, flags=re.S)  # 닫지 못하고 끝난 생각 글
    return re.sub(r"<\|(begin|end)_of_box\|>", "", text).strip()  # GLM 답 상자 표시


def parse_json(text: str):
    """모델 답에서 JSON만 꺼낸다 (```json 감싸기, 앞뒤 설명, 생각 글이 붙어도)."""
    text = strip_reasoning(text)
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
