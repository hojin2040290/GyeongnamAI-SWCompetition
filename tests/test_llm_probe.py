"""vLLM 도구 호출 확인 명령 테스트 (가짜 서버 응답)."""
import httpx

from app.llm import probe


def _client(message: dict, status: int = 200) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "fake"}]})
        return httpx.Response(status, json={"choices": [{"message": message}]} if status == 200 else {"error": "x"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_tool_call_supported(monkeypatch):
    monkeypatch.setattr(probe, "LLM_MODEL", "fake")
    msg = {"role": "assistant", "content": None,
           "tool_calls": [{"id": "1", "type": "function", "function": {"name": "get_today", "arguments": "{}"}}]}
    with _client(msg) as c:
        assert probe.check_models(c) == ["fake"]
        ok, detail = probe.check_tool_call(c)
    assert ok and "성공" in detail


def test_tool_call_returned_as_text():
    with _client({"role": "assistant", "content": '<tool_call>{"name": "get_today"}</tool_call>'}) as c:
        ok, detail = probe.check_tool_call(c)
    assert not ok and "파서" in detail


def test_tool_call_rejected():
    with _client({}, status=400) as c:
        ok, detail = probe.check_tool_call(c)
    assert not ok and "거절" in detail
