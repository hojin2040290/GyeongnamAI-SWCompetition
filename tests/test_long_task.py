"""오래 걸리는 요청을 뒤에서 실행 (cloudflared 100초 대비, app/long_task.py).

X-Long-Task: 1을 붙이면 작업 번호를 바로 받고(202), GET /api/tasks/<번호>로 원래 응답을 그대로 받는다.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

JOB = {"name": "가상분식 뒤실행점", "wage": 12000, "size": "lt5", "probation": "no", "payday": 10,
       "contract_written": True, "copy_received": True,
       "schedule": {"토": {"start": "18:00", "end": "23:00", "brk": "30분"}}}
LONG = {"X-Long-Task": "1"}


def _login(cl: TestClient, email: str) -> int:
    cl.post("/api/auth/register", json={"email": email, "password": "test1234", "birth_date": "2009-03-02"})
    cl.post("/api/auth/login", json={"email": email, "password": "test1234"})
    return cl.post("/api/jobs", json={**JOB, "name": JOB["name"] + email[:3]}).json()["id"]


def _wait(cl: TestClient, tid: str, limit: float = 30):
    end = time.time() + limit
    while time.time() < end:
        r = cl.get(f"/api/tasks/{tid}")
        if r.status_code != 202:
            return r
        assert r.json() == {"pending": True}
        time.sleep(0.05)
    raise AssertionError("작업이 끝나지 않았어요")


@pytest.fixture(scope="module")
def env():
    init_db()
    with TestClient(app) as a, TestClient(app) as b:
        yield a, _login(a, "long_a@example.com"), b, _login(b, "long_b@example.com")


def test_long_task_returns_same_result_as_direct(env):
    """계약서 점검: 뒤에서 실행해도 결과는 바로 실행한 것과 같은 모양이다."""
    a, ja, _, _ = env
    direct = a.post(f"/api/jobs/{ja}/check")
    started = a.post(f"/api/jobs/{ja}/check", headers=LONG)
    assert started.status_code == 202 and started.json()["pending"] is True
    done = _wait(a, started.json()["task_id"])
    assert done.status_code == 200 == direct.status_code
    assert set(done.json()) == set(direct.json())
    # 결과는 한 번만 받는다
    assert a.get(f"/api/tasks/{started.json()['task_id']}").status_code == 404


def test_long_task_passes_errors_through(env):
    """원래 처리의 오류(남의 사업장 404, 입력 검사 422)도 그대로 돌려준다."""
    a, _, _, jb = env
    r = _wait(a, a.post(f"/api/jobs/{jb}/check", headers=LONG).json()["task_id"])
    assert r.status_code == 404
    r = _wait(a, a.post("/api/seek/check", json={"wage": "글자"}, headers=LONG).json()["task_id"])
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_long_task_is_only_for_its_owner(env):
    """다른 사용자는 작업 번호를 알아도 결과를 받지 못한다."""
    a, ja, b, _ = env
    tid = a.post(f"/api/jobs/{ja}/check", headers=LONG).json()["task_id"]
    assert b.get(f"/api/tasks/{tid}").status_code == 404
    assert _wait(a, tid).status_code == 200


def test_unknown_task_and_normal_requests(env):
    a, ja, _, _ = env
    assert a.get("/api/tasks/없는번호").status_code == 404
    assert "작업을 찾지 못했어요" in a.get("/api/tasks/없는번호").json()["detail"]
    assert a.post(f"/api/jobs/{ja}/check").status_code == 200  # 머리가 없으면 지금처럼 바로 처리


def test_long_task_unexpected_error_is_korean_500(env, monkeypatch):
    """뒤에서 실행하다 예상 못 한 오류가 나도 지금과 같은 한국어 문구와 오류 번호로 돌려준다."""
    from app.agent import core
    a, ja, _, _ = env
    monkeypatch.setattr(core, "run_contract_check", lambda *x, **k: 1 / 0)
    r = _wait(a, a.post(f"/api/jobs/{ja}/check", headers=LONG).json()["task_id"])
    assert r.status_code == 500 and "오류 번호" in r.json()["detail"]
