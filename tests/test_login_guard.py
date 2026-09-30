"""로그인 시도 횟수 제한 테스트 (가상 계정)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app import config, login_guard
from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.main import app
from app.models import LoginAttempt

PW = "test1234"


@pytest.fixture()
def c():
    init_db()
    with TestClient(app) as cl:
        yield cl
    with Session(engine) as s:  # 다른 테스트의 로그인에 영향이 없게 실패 기록을 지운다
        s.exec(delete(LoginAttempt))
        s.commit()


def login(c, email, pw):
    return c.post("/api/auth/login", json={"email": email, "password": pw})


def test_email_locked_after_fails_even_with_right_password(c):
    email = "lock1@example.com"
    c.post("/api/auth/register", json={"email": email, "password": PW, "birth_date": "2009-03-02"})
    msgs = [login(c, email, "wrong").json()["detail"] for _ in range(config.LOGIN_MAX_FAILS)]
    assert "2번 더 틀리면" in msgs[-3] and "1번 더 틀리면" in msgs[-2] and "로그인할 수 없어요" in msgs[-1]
    r = login(c, email, PW)
    assert r.status_code == 429 and f"{config.LOGIN_LOCK_MIN}분 뒤에" in r.json()["detail"]
    assert login(c, email.upper(), PW).status_code == 429  # 대소문자를 바꿔도 같은 이메일로 센다


def test_lock_expires_and_success_resets(c, monkeypatch):
    email = "lock2@example.com"
    c.post("/api/auth/register", json={"email": email, "password": PW, "birth_date": "2009-03-02"})
    for _ in range(config.LOGIN_MAX_FAILS):
        login(c, email, "wrong")
    later = now_kst() + timedelta(minutes=config.LOGIN_LOCK_MIN + 1)
    monkeypatch.setattr(login_guard, "now_kst", lambda: later)
    assert login(c, email, PW).status_code == 200  # 잠금 시간이 지나면 다시 된다
    assert "번 더 틀리면" not in login(c, email, "wrong").json()["detail"]  # 성공하면 틀린 횟수가 처음부터


def test_ip_limit_across_emails(c, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_IP_MAX_FAILS", 6)
    for i in range(6):
        login(c, f"nobody{i}@example.com", "wrong")  # 여러 이메일을 돌아가며 틀려도
    r = login(c, "other@example.com", "wrong")
    assert r.status_code == 429  # 같은 접속 주소에서는 막힌다
