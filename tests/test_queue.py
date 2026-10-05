"""에이전트 대기줄(app/agent/queue.py): 사용자마다 하나, 1번부터 10번 자리, 1번만 실행, 끝나면 빠지고 뒤 번호가 하나씩 앞당겨짐."""
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


def _wait_size(q: AgentQueue, n: int, uid: int = 1) -> None:
    for _ in range(200):
        if len(q.snapshot(uid)) == n:
            return
        time.sleep(0.01)
    raise AssertionError(f"대기줄 길이가 {n}이 되지 않음: {q.snapshot(uid)}")


def test_runs_in_order_and_numbers_move_up():
    q, order = AgentQueue(), []
    gates = [threading.Event() for _ in range(3)]
    ths = []
    for i, g in enumerate(gates):  # 1, 2, 3 차례로 줄을 선다
        ths.append(_start(q, f"일{i + 1}", g, order))
        _wait_size(q, i + 1)
    assert [x["pos"] for x in q.snapshot(1)] == [1, 2, 3] and [x["label"] for x in q.snapshot(1)] == ["일1", "일2", "일3"]
    assert order == ["일1 시작"]  # 1번만 실행 중
    gates[0].set(); _wait_size(q, 2)
    assert [x["label"] for x in q.snapshot(1)] == ["일2", "일3"]  # 1번이 빠지고 2번 → 1번, 3번 → 2번
    gates[1].set(); _wait_size(q, 1)
    gates[2].set(); _wait_size(q, 0)
    for th in ths:
        th.join(2)
    assert order == ["일1 시작", "일1 끝", "일2 시작", "일2 끝", "일3 시작", "일3 끝"]


def test_each_user_has_own_line():
    """다른 사용자의 실행은 서로 기다리지 않는다. 같은 사용자의 실행만 차례로 돈다."""
    q, order = AgentQueue(), []
    g1, g2, g3 = threading.Event(), threading.Event(), threading.Event()
    _start(q, "사용자1 일1", g1, order, uid=1); _wait_size(q, 1, uid=1)
    _start(q, "사용자2 일1", g2, order, uid=2); _wait_size(q, 1, uid=2)
    _start(q, "사용자1 일2", g3, order, uid=1); _wait_size(q, 2, uid=1)
    time.sleep(0.05)
    assert order == ["사용자1 일1 시작", "사용자2 일1 시작"]  # 사용자2는 사용자1을 기다리지 않음
    assert [x["label"] for x in q.snapshot(1)] == ["사용자1 일1", "사용자1 일2"] and len(q.snapshot(2)) == 1
    g2.set(); _wait_size(q, 0, uid=2)
    assert "사용자1 일2 시작" not in order  # 사용자1의 두 번째 일은 사용자1의 첫 일을 기다림
    g1.set(); g3.set(); _wait_size(q, 0, uid=1)
    assert q.lines == {}  # 빈 줄은 남지 않음


def test_ten_slots_then_waits_for_a_spot():
    q, order = AgentQueue(slots=10), []
    gate = threading.Event()
    for i in range(10):
        _start(q, f"일{i + 1}", gate, order)
        _wait_size(q, i + 1)
    _start(q, "일11", gate, order)
    time.sleep(0.1)
    assert len(q.snapshot(1)) == 10 and "일11" not in [x["label"] for x in q.snapshot(1)]  # 자리가 없으면 줄에 못 섬
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
    assert q.snapshot(1) == []  # 오류가 나도 빠진다
    # 1번 자리에서 실행 중인 일이 안에서 다른 점검을 부르면 다시 줄을 서지 않고 바로 실행한다 (매일 점검 → 급여 점검)
    assert q.run(1, "매일 점검", lambda: q.run(1, "급여 점검", lambda: "안쪽 실행")) == "안쪽 실행"
    assert q.snapshot(1) == []


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
    jobs = [c.post("/api/jobs", json={"name": f"가상대기 시험점{n}", "wage": 10320, "start_date": "2026-08-03", "schedule": {}}).json()["id"]
            for n in (1, 2)]  # 다른 두 사업장 (같은 사업장의 같은 점검은 하나로 합쳐진다)
    spans, real = [], core.Run.agent

    def slow_agent(self, goal, context, extra=None):  # 에이전트가 일하는 시간을 흉내 낸다
        start = time.monotonic(); time.sleep(0.3); spans.append((start, time.monotonic()))
        return None
    monkeypatch.setattr(core.Run, "agent", slow_agent)

    def check(job):
        with Session(engine) as s:
            core.run_contract_check(s, uid, job)
    ths = [threading.Thread(target=check, args=(j,)) for j in jobs]
    for th in ths:
        th.start()
    time.sleep(0.1)
    assert len(QUEUE.snapshot(uid)) == 2 and QUEUE.snapshot(uid)[1]["label"] == "계약서 점검"  # 하나는 1번, 하나는 2번에서 기다림
    for th in ths:
        th.join(10)
    monkeypatch.setattr(core.Run, "agent", real)
    (a0, a1), (b0, b1) = sorted(spans)
    assert a1 <= b0  # 앞 실행이 끝난 뒤에 다음 실행이 시작됨
    assert QUEUE.snapshot(uid) == []


def test_same_work_waits_for_the_one_in_line():
    """같은 일이 이미 줄에 있으면 새로 서지 않고 그 결과를 함께 받는다 (새로고침마다 같은 점검이 쌓이던 문제)."""
    q, runs = AgentQueue(), []
    gate = threading.Event()

    def work():
        runs.append(1); gate.wait(5); return {"결과": len(runs)}
    out = []
    ths = [threading.Thread(target=lambda: out.append(q.run(1, "종합 점검", work, key=("종합 점검", "1", "7")))) for _ in range(3)]
    for th in ths:
        th.start()
    time.sleep(0.2)
    assert len(q.snapshot(1)) == 1  # 같은 일은 줄에 하나만
    gate.set()
    for th in ths:
        th.join(3)
    assert runs == [1] and out == [{"결과": 1}] * 3  # 한 번만 실행하고 결과는 셋 다 받음
    # 입력이 다르면(다른 사업장) 따로 선다
    gate2 = threading.Event()
    a = threading.Thread(target=q.run, args=(1, "종합 점검", lambda: gate2.wait(5)), kwargs={"key": ("종합 점검", "1", "7")})
    b2 = threading.Thread(target=q.run, args=(1, "종합 점검", lambda: gate2.wait(5)), kwargs={"key": ("종합 점검", "1", "8")})
    a.start(); b2.start(); time.sleep(0.2)
    assert len(q.snapshot(1)) == 2
    gate2.set(); a.join(3); b2.join(3)


def test_ticket_has_task_id_of_screen_request(monkeypatch):
    """화면이 뒤에서 실행시킨 요청(X-Long-Task)의 대기줄 표에는 그 작업 번호가 붙는다 (진행 칸이 자기 번호를 찾게).
    다른 사용자의 대기줄은 보이지 않는다."""
    from fastapi.testclient import TestClient

    from app.agent import core
    from app.db import init_db
    from app.main import app
    init_db()
    with TestClient(app) as c, TestClient(app) as other:  # 열어 둔 동안 뒤에서 실행하는 작업이 계속 돈다
        _task_id_case(c, other, monkeypatch, core)


def _task_id_case(c, other, monkeypatch, core) -> None:
    c.post("/api/auth/register", json={"email": "queue_task@example.com", "password": "test1234", "birth_date": "2009-05-01"})
    other.post("/api/auth/register", json={"email": "queue_task2@example.com", "password": "test1234", "birth_date": "2009-05-01"})
    job = c.post("/api/jobs", json={"name": "가상대기 작업점", "wage": 10320, "start_date": "2026-08-03", "schedule": {}}).json()["id"]
    gate = threading.Event()

    def slow_agent(self, goal, context, extra=None):
        gate.wait(5)
        return None
    monkeypatch.setattr(core.Run, "agent", slow_agent)
    tid = c.post(f"/api/jobs/{job}/check", headers={"X-Long-Task": "1"}).json()["task_id"]
    mine = []
    for _ in range(100):
        mine = c.get("/api/agent/queue").json()["mine"]
        if mine:
            break
        time.sleep(0.02)
    assert mine and mine[0]["pos"] == 1 and mine[0]["task"] == tid
    assert other.get("/api/agent/queue").json() == {"size": 0, "slots": 10, "mine": []}
    gate.set()
    for _ in range(200):
        if c.get(f"/api/tasks/{tid}").status_code != 202:
            break
        time.sleep(0.02)
