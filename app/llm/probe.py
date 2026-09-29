"""vLLM 서버가 에이전트에 필요한 도구 호출(tool calling)을 받는지 확인한다.

사용법: python -m app.llm.probe
- 서버를 끄거나 다시 켜지 않는다. 이미 떠 있는 서버에 요청 두 번만 보낸다.
- 도구 호출이 안 되면 vLLM을 켤 때 필요한 옵션을 알려 준다 (서버 재시작은 사용자가 직접).
"""
import json
import sys

import httpx

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, LLM_TIMEOUT

TEST_TOOL = {"type": "function", "function": {
    "name": "get_today", "description": "오늘 날짜를 알려 준다",
    "parameters": {"type": "object", "properties": {}, "required": []}}}

HELP = ("vLLM을 켤 때 '--enable-auto-tool-choice --tool-call-parser <파서>' 옵션이 필요해요. "
        "파서는 모델에 맞게 고릅니다 (예: Qwen 계열은 hermes, Llama 3 계열은 llama3_json). "
        "서버 재시작은 직접 해 주세요.")


def _headers() -> dict:
    return {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}


def check_models(c: httpx.Client) -> list[str]:
    r = c.get(f"{LLM_BASE_URL.rstrip('/')}/models", headers=_headers())
    r.raise_for_status()
    return [m.get("id", "") for m in r.json().get("data", [])]


def check_tool_call(c: httpx.Client) -> tuple[bool, str]:
    """도구 하나를 주고 부르게 해 본다. (성공 여부, 설명)"""
    payload = {"model": LLM_MODEL, "temperature": 0, "tools": [TEST_TOOL], "tool_choice": "auto",
               "messages": [{"role": "user", "content": "오늘 날짜를 알고 싶어요. 도구를 써서 알아봐 주세요."}]}
    r = c.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", json=payload, headers=_headers())
    if r.status_code == 400:
        return False, f"서버가 도구 호출 요청을 거절했어요: {r.text[:300]}"
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if calls and calls[0]["function"]["name"] == "get_today":
        return True, f"도구 호출 성공: {json.dumps(calls[0]['function'], ensure_ascii=False)}"
    content = (msg.get("content") or "")[:300]
    if "tool_call" in content or "get_today" in content:
        return False, f"모델은 도구를 부르려 했지만 서버가 글로만 돌려줬어요 (파서 옵션 없음): {content}"
    return False, f"모델이 도구를 부르지 않았어요: {content}"


def main() -> int:
    print(f"LLM_ENABLED={LLM_ENABLED}, LLM_BASE_URL={LLM_BASE_URL}, LLM_MODEL={LLM_MODEL or '(없음)'}")
    if not (LLM_ENABLED and LLM_MODEL):
        print(".env에 LLM_ENABLED=true와 LLM_MODEL을 넣어 주세요.")
        return 1
    try:
        with httpx.Client(timeout=LLM_TIMEOUT) as c:
            models = check_models(c)
            print(f"[모델 목록] {', '.join(models) or '(없음)'}")
            if LLM_MODEL not in models:
                print(f"LLM_MODEL({LLM_MODEL})이 서버 모델 목록에 없어요. 이름을 확인해 주세요.")
                return 1
            ok, detail = check_tool_call(c)
    except httpx.HTTPError as exc:
        print(f"AI 서버에 연결하지 못했어요 ({type(exc).__name__}). 주소와 서버 상태를 확인해 주세요.")
        return 1
    print(f"[도구 호출] {detail}")
    if not ok:
        print(HELP)
        return 1
    print("에이전트를 쓸 준비가 됐어요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
