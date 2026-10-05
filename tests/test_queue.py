"""에이전트 대기줄(app/agent/queue.py): 1번부터 10번 자리, 1번만 실행, 끝나면 빠지고 뒤 번호가 하나씩 앞당겨짐."""
import threading
import time

from app.agent.queue import AgentQueue


def _start(q: AgentQueue, name: str, gate: threading.Event, order: list, uid: int = 1) -> threading.Thread:
    def job():
        order.append(f"{name} 시작")
        gate.wait(5)
        order.append(f"{name} 끝")
    th = threading.Thread(target=q.run, args=(uid, name, job), daemon=True)
    th.start()
    return th


def _wait_size(q: AgentQueue, n: int) -> None:
    for _ in range(200):
        if len(q.snapshot()) == n:
            return
        time.sleep(0.01)
    raise AssertionError(f"대기줄 길이가 {n}이 되지 않음: {q.snapshot()}")


def test_runs_in_order_and_numbers_move_up():
    q, order = AgentQueue(), []
    gates = [threading.Event() for _ in range(3)]
    ths = []
    for i, g in enumerate(gates):  # 1, 2, 3 차례로 줄을 선다
        ths.append(_start(q, f"일{i + 1}", g, order))
        _wait_size(q, i + 1)
    assert [x["pos"] for x in q.snapshot()] == [1, 2, 3] and [x["label"] for x in q.snapshot()] == ["일1", "일2", "일3"]
    assert order == ["일1 시작"]  # 1번만 실행 중
    gates[0].set(); _wait_size(q, 2)
    assert [x["label"] for x in q.snapshot()] == ["일2", "일3"]  # 1번이 빠지고 2번 → 1번, 3번 → 2번
    gates[1].set(); _wait_size(q, 1)
    gates[2].set(); _wait_size(q, 0)
    for th in ths:
        th.join(2)
    assert order == ["일1 시작", "일1 끝", "일2 시작", "일2 끝", "일3 시작", "일3 끝"]


def test_ten_slots_then_waits_for_a_spot():
    q, order = AgentQueue(slots=10), []
    gate = threading.Event()
    for i in range(10):
        _start(q, f"일{i + 1}", gate, order)
        _wait_size(q, i + 1)
    _start(q, "일11", gate, order)
    time.sleep(0.1)
    assert len(q.snapshot()) == 10 and "일11" not in [x["label"] for x in q.snapshot()]  # 자리가 없으면 줄에 못 섬
    gate.set()
    _wait_size(q, 0)
    assert order[-1] == "일11 끝"


def test_leaves_line_even_on_error_and_nested_runs_do_not_wait():
    q = AgentQueue()

    def boom():
        raise ValueError("실패")
    try:
        q.run(1, "오류 나는 일", boom)
    except ValueError:
        pass
    assert q.snapshot() == []  # 오류가 나도 빠진다
    # 1번 자리에서 실행 중인 일이 안에서 다른 점검을 부르면 다시 줄을 서지 않고 바로 실행한다 (매일 점검 → 급여 점검)
    assert q.run(1, "매일 점검", lambda: q.run(1, "급여 점검", lambda: "안쪽 실행")) == "안쪽 실행"
    assert q.snapshot() == []


def test_agent_runs_wait_their_turn(monkeypatch):
    """실제 에이전트 실행 두 개가 동시에 와도 차례로 돈다 (겹치지 않음)."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session

    from app.agent import core
    from app.agent.queue import QUEUE
    from app.db import engine, init_db
    from app.main import app
    init_db()
    c = TestClient(app)
    c.post("/api/auth/register", json={"email": "queue_test@example.com", "password": "test1234", "birth_date": "2009-05-01"})
    uid = c.get("/api/me").json()["id"]
    job = c.post("/api/jobs", json={"name": "가상대기 시험점", "wage": 10320, "start_date": "2026-08-03", "schedule": {}}).json()["id"]
    spans, real = [], core.Run.agent

    def slow_agent(self, goal, context, extra=None):  # 에이전트가 일하는 시간을 흉내 낸다
        start = time.monotonic(); time.sleep(0.3); spans.append((start, time.monotonic()))
        return None
    monkeypatch.setattr(core.Run, "agent", slow_agent)

    def check():
        with Session(engine) as s:
            core.run_contract_check(s, uid, job)
    ths = [threading.Thread(target=check) for _ in range(2)]
    for th in ths:
        th.start()
    time.sleep(0.1)
    assert len(QUEUE.snapshot()) == 2 and QUEUE.snapshot()[1]["label"] == "계약서 점검"  # 하나는 1번, 하나는 2번에서 기다림
    for th in ths:
        th.join(10)
    monkeypatch.setattr(core.Run, "agent", real)
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0  # 앞 실행이 끝난 뒤에 다음 실행이 시작됨
    assert QUEUE.snapshot() == []
