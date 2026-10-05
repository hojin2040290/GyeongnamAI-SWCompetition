"""에이전트 실행 대기줄 (서버 전체에 하나).

- 자리는 1번부터 10번까지. 에이전트 실행은 시작하기 전에 빈 자리 중 맨 뒤에 서고, 1번이 되면 실행한다.
- 1번 실행이 끝나면 빠지고, 뒤에 선 실행들의 번호가 하나씩 앞당겨진다 (2번 → 1번, 3번 → 2번 …).
- 10자리가 다 차 있으면 자리가 날 때까지 기다렸다가 선다.
- 이미 1번 자리에서 실행 중인 일이 안에서 다른 점검을 부르면(예: 매일 점검이 급여 점검을 부름) 다시 줄을 서지 않는다
  (같은 스레드면 이어서 실행. 다시 서면 자기 자신을 기다리다 멈춘다).
화면은 GET /api/agent/queue로 내 실행의 번호를 보고 진행 칸에 '대기줄 n번째'를 보여 준다.
"""
import functools
import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable

SLOTS = 10  # 대기줄 자리 수 (1번 ~ 10번)


@dataclass
class Ticket:
    no: int
    user_id: int | None
    label: str
    thread: int = field(default_factory=threading.get_ident)


class AgentQueue:
    def __init__(self, slots: int = SLOTS):
        self.slots = slots
        self.line: list[Ticket] = []  # 0번 칸이 대기줄 1번 (실행 중)
        self.cond = threading.Condition()
        self.numbers = itertools.count(1)
        self.local = threading.local()  # 이 스레드가 지금 1번 자리에서 실행 중인지 (안에서 부른 점검은 줄을 다시 서지 않게)

    def position(self, ticket: Ticket) -> int:
        """대기줄 번호 (1부터). 줄에 없으면 0."""
        with self.cond:
            return self.line.index(ticket) + 1 if ticket in self.line else 0

    def enter(self, user_id: int | None, label: str) -> Ticket:
        """빈 자리 중 맨 뒤에 서고, 1번이 될 때까지 기다린다."""
        with self.cond:
            self.cond.wait_for(lambda: len(self.line) < self.slots)  # 10자리가 다 차 있으면 자리가 날 때까지
            ticket = Ticket(next(self.numbers), user_id, label)
            self.line.append(ticket)
            self.cond.notify_all()
            self.cond.wait_for(lambda: self.line[0] is ticket)  # 1번이 될 때까지
            return ticket

    def leave(self, ticket: Ticket) -> None:
        """실행이 끝나면 빠진다. 뒤의 번호는 하나씩 앞당겨진다."""
        with self.cond:
            if ticket in self.line:
                self.line.remove(ticket)
            self.cond.notify_all()

    def snapshot(self) -> list[dict]:
        """지금 대기줄 (번호, 사용자, 하는 일)."""
        with self.cond:
            return [{"pos": i + 1, "user_id": t.user_id, "label": t.label} for i, t in enumerate(self.line)]

    def running_here(self) -> bool:
        return getattr(self.local, "depth", 0) > 0

    def run(self, user_id: int | None, label: str, fn: Callable, *args, **kwargs):
        """줄을 서서 1번이 되면 fn을 실행하고, 끝나면(오류가 나도) 빠진다. 이미 1번에서 실행 중인 스레드면 바로 실행."""
        if self.running_here():
            return fn(*args, **kwargs)
        ticket = self.enter(user_id, label)
        self.local.depth = 1
        try:
            return fn(*args, **kwargs)
        finally:
            self.local.depth = 0
            self.leave(ticket)


QUEUE = AgentQueue()


def queued(label: str, user_of: Callable | None = None):
    """에이전트 실행 함수에 붙인다. 사용자 번호는 user_id 인자(core.run_*는 (session, user_id, ...))나 user_of(args)로 찾는다."""
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            uid = user_of(args) if user_of else kwargs.get("user_id", args[1] if len(args) > 1 else None)
            return QUEUE.run(uid, label, fn, *args, **kwargs)
        return inner
    return wrap
