"""오래 걸리는 요청(AI 에이전트)을 요청 하나에서 떼어 뒤에서 실행한다.

cloudflared(Cloudflare)는 응답이 100초 안에 시작되지 않으면 연결을 끊는다 (무료 요금제에서는 바꿀 수 없음).
화면이 요청에 `X-Long-Task: 1`을 붙이면, 원래 처리를 뒤에서 그대로 실행하고 작업 번호만 바로 돌려준다 (202).
화면은 `GET /api/tasks/<번호>`로 결과를 묻는다: 끝나기 전에는 202 {"pending": true}, 끝나면 원래 응답(결과나 오류)을 그대로.
로그인 확인, 입력 검사, 오류 처리 등 기존 API 처리는 그대로 거친다. 작업은 로그인한 본인만 받을 수 있다.
"""
import asyncio
import contextvars
import json
import logging
import time
import uuid

HEADER = b"x-long-task"
POLL_PREFIX = "/api/tasks/"
KEEP_SEC = 1800  # 끝난 뒤 이 시간 안에 받아 가지 않은 결과는 지운다
TASKS: dict[str, dict] = {}
CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("long_task", default=None)  # 지금 뒤에서 실행 중인 작업 번호 (에이전트 대기줄 표에 붙인다)
log = logging.getLogger("uvicorn.error")


def _cleanup(now: float) -> None:
    for tid in [t for t, x in TASKS.items() if x["done"] and now - x["at"] > KEEP_SEC]:
        TASKS.pop(tid, None)


async def _json(send, status: int, data: dict, extra: list | None = None) -> None:
    body = json.dumps(data, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _owner(scope) -> int | None:
    return (scope.get("session") or {}).get("uid")


class LongTaskMiddleware:
    """SessionMiddleware 안쪽에 둔다 (로그인한 사용자를 알아야 해서)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if scope["method"] == "GET" and path.startswith(POLL_PREFIX):
            return await self.poll(scope, send, path[len(POLL_PREFIX):])
        if path.startswith("/api/") and any(k == HEADER and v == b"1" for k, v in scope["headers"]):
            return await self.start(scope, receive, send)
        await self.app(scope, receive, send)

    async def start(self, scope, receive, send) -> None:
        body, more = b"", True
        while more:  # 요청 본문을 미리 다 읽어 둔다 (뒤에서 실행할 때 다시 건넨다)
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        _cleanup(time.time())
        tid = uuid.uuid4().hex
        task = {"owner": _owner(scope), "done": False, "status": 500, "headers": [], "body": b"", "at": time.time()}
        inner = dict(scope, headers=[(k, v) for k, v in scope["headers"] if k != HEADER])
        task["job"] = asyncio.create_task(self.run(inner, body, task, tid))  # 참조를 남겨 도중에 사라지지 않게
        TASKS[tid] = task
        await _json(send, 202, {"task_id": tid, "pending": True})

    async def run(self, scope, body: bytes, task: dict, tid: str = "") -> None:
        given = False
        CURRENT.set(tid or None)  # 이 작업 안에서 선 대기줄 표에 작업 번호가 붙어, 화면이 자기 실행의 번호를 찾는다

        async def receive():
            nonlocal given
            if not given:
                given = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(KEEP_SEC)  # 화면과의 연결은 이미 끝났으므로 끊김을 알리지 않는다
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                task["status"], task["headers"] = msg["status"], list(msg.get("headers", []))
            elif msg["type"] == "http.response.body":
                task["body"] += msg.get("body", b"")

        try:
            await self.app(scope, receive, send)
        except Exception:  # noqa: BLE001 (main.py의 server_error와 같은 문구로 돌려준다)
            code = uuid.uuid4().hex[:6]
            log.exception("처리 중 오류 [%s] %s %s (뒤에서 실행)", code, scope["method"], scope["path"])
            data = json.dumps({"detail": f"처리 중 오류가 났어요. 잠시 뒤 다시 해 주세요 (오류 번호 {code})"},
                              ensure_ascii=False).encode()
            task.update(status=500, body=data, headers=[(b"content-type", b"application/json"),
                                                        (b"content-length", str(len(data)).encode())])
        finally:
            task["done"], task["at"] = True, time.time()
            task.pop("job", None)

    async def poll(self, scope, send, tid: str) -> None:
        task = TASKS.get(tid)
        if not task or task["owner"] != _owner(scope):
            return await _json(send, 404, {"detail": "작업을 찾지 못했어요. 서버가 다시 켜졌다면 다시 해 주세요"})
        if not task["done"]:
            return await _json(send, 202, {"pending": True}, [(b"x-task-pending", b"1")])
        TASKS.pop(tid, None)
        await send({"type": "http.response.start", "status": task["status"], "headers": task["headers"]})
        await send({"type": "http.response.body", "body": task["body"]})
