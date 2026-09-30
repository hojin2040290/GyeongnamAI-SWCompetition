"""로그인 시도 횟수 제한 (비밀번호 대입 막기).

같은 이메일로 LOGIN_MAX_FAILS번, 같은 접속 주소로 LOGIN_IP_MAX_FAILS번 틀리면 LOGIN_LOCK_MIN분 동안 로그인을 막는다.
로그인에 성공하면 그 이메일의 틀린 기록을 지운다. 기록은 DB에 남아 서버를 다시 켜도 이어진다.
"""
import math
from datetime import timedelta

from sqlmodel import Session, delete, select

from app import config
from app.calc.timeutil import now_kst
from app.models import LoginAttempt


def _key(email: str) -> str:
    return (email or "").strip().lower()


def _fails(s: Session, since, **where) -> list[LoginAttempt]:
    q = select(LoginAttempt).where(LoginAttempt.at >= since)
    for k, v in where.items():
        q = q.where(getattr(LoginAttempt, k) == v)
    return list(s.exec(q.order_by(LoginAttempt.at)))


def locked_minutes(s: Session, email: str, ip: str) -> int:
    """막혀 있으면 남은 분(올림), 아니면 0."""
    now = now_kst()
    window = timedelta(minutes=config.LOGIN_LOCK_MIN)
    for rows, limit in ((_fails(s, now - window, email=_key(email)), config.LOGIN_MAX_FAILS),
                        (_fails(s, now - window, ip=ip), config.LOGIN_IP_MAX_FAILS)):
        if len(rows) >= limit:
            until = rows[-limit].at + window  # 한도에 닿게 만든 실패들 중 가장 오래된 것부터 잠금 시간을 센다
            left = (until - now).total_seconds() / 60
            if left > 0:
                return max(1, math.ceil(left))
    return 0


def record_fail(s: Session, email: str, ip: str) -> int:
    """틀린 기록을 남기고, 그 이메일로 막히기 전까지 남은 횟수를 돌려준다."""
    now = now_kst()
    s.exec(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=1)))  # 오래된 기록 정리
    s.add(LoginAttempt(email=_key(email), ip=ip, at=now))
    s.commit()
    used = len(_fails(s, now - timedelta(minutes=config.LOGIN_LOCK_MIN), email=_key(email)))
    return max(0, config.LOGIN_MAX_FAILS - used)


def record_success(s: Session, email: str) -> None:
    s.exec(delete(LoginAttempt).where(LoginAttempt.email == _key(email)))
    s.commit()
