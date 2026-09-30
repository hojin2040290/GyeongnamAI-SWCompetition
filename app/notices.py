"""알림 보내기와 정리. 예전 알림 때문에 헷갈리지 않게, 같은 종류의 새 알림이 오면 예전 알림을 지운다.

- 알림마다 종류(topic)를 코드가 정한다. AI가 보내는 알림도 그 실행의 종류를 따른다.
  예: 계약서 점검, 오늘 근무 점검, 급여 점검(달마다 따로), 퇴직 지급 기한, 상담 자료, 신고 후 보호, 게시물, 질문, 매일 자동 점검
- 새 알림을 보내면 같은 사업장, 같은 종류의 '다른 실행'에서 보낸 알림을 지운다 (한 번의 실행에서 보낸 알림끼리는 남긴다).
- 답을 기다리던 질문이 모두 끝나면 질문 알림을 지운다.
- 읽은 지 오래된 알림(KEEP_DAYS일)은 지운다. 종류가 없던 예전 알림은 제목으로 종류를 정해 같은 방식으로 정리한다.
알림은 증거 자료가 아니라 안내라서 지워도 된다 (증거는 기록, 원본 파일, 상담 사전 자료에 남는다).
"""
import re
from datetime import timedelta

from sqlmodel import Session, select

from app.calc.timeutil import now_kst
from app.models import Notification

DEDUP_HOURS = 24  # 같은 제목과 내용의 알림을 다시 보내지 않는 시간
KEEP_DAYS = 30  # 읽은 알림을 남겨 두는 날 수

EVENT_TOPIC = {
    "contract_check": "contract_check", "shift_check": "shift_check", "seek_check": "seek",
    "payday": "payday", "quit_check": "quit", "open_check": "open_record", "report": "report",
    "guard_on": "guard", "guard_off": "guard",
    "guard_search": "guard_posts", "guard_review": "guard_posts", "guard_preserve": "guard_posts",
    "advice": "advice", "daily": "daily",
}

# 종류가 없던 예전 알림: 제목으로 종류를 정한다
LEGACY = [(r"^계약서 점검", "contract_check"), (r"^오늘 근무 점검", "shift_check"),
          (r"^(\d{4}-\d{2}) 급여", r"payday:\1"), (r"^퇴직 후 임금", "quit"), (r"^퇴근을 누르지", "open_record"),
          (r"^상담 사전 자료", "report"), (r"^신고 후 보호", "guard"), (r"^(새 공개 게시물|보복)", "guard_posts"),
          (r"^에이전트가 물어볼", "questions"), (r"^오늘 자동 점검", "daily")]


def topic_of(event: str, month: str | None = None) -> str:
    base = EVENT_TOPIC.get(event, event)
    return f"{base}:{month}" if month else base


def legacy_topic(title: str) -> str:
    for pattern, topic in LEGACY:
        m = re.match(pattern, title or "")
        if m:
            return m.expand(topic)
    return f"etc:{title}"


def _same_topic(user_id: int, job_id, topic: str):
    return select(Notification).where(Notification.user_id == user_id, Notification.job_id == job_id,
                                      Notification.topic == topic)


def send(session: Session, user_id: int, job_id, title: str, body: str, topic: str, run_id: str = "") -> str:
    """알림을 보내고, 같은 종류의 예전 알림(다른 실행에서 보낸 것)을 지운다."""
    title, body = str(title).strip()[:100], str(body).strip()[:500]
    if not title or not body:
        raise ValueError("알림 제목과 내용이 필요해요")
    topic = topic or legacy_topic(title)
    since = now_kst() - timedelta(hours=DEDUP_HOURS)
    dup = session.exec(_same_topic(user_id, job_id, topic).where(
        Notification.title == title, Notification.body == body, Notification.created_at >= since)).first()
    for old in session.exec(_same_topic(user_id, job_id, topic)).all():
        if old is not dup and (not run_id or old.run_id != run_id):
            session.delete(old)
    if dup:
        dup.created_at, dup.read = now_kst(), False  # 같은 알림은 다시 만들지 않고 맨 위로
        session.add(dup)
        session.commit()
        return "같은 알림이 있어 새로 고침"
    session.add(Notification(user_id=user_id, job_id=job_id, title=title, body=body, topic=topic, run_id=run_id,
                             created_at=now_kst()))
    session.commit()
    return "보냄"


def clear(session: Session, user_id: int, job_id, topic: str) -> int:
    rows = session.exec(_same_topic(user_id, job_id, topic)).all()
    for n in rows:
        session.delete(n)
    session.commit()
    return len(rows)


def tidy(session: Session) -> dict:
    """예전 알림 정리: 종류가 없으면 제목으로 정하고, 같은 종류는 가장 최근 것만 남기고, 오래전에 읽은 알림은 지운다."""
    rows = session.exec(select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc())).all()
    seen, removed = set(), 0
    old_read = now_kst() - timedelta(days=KEEP_DAYS)
    for n in rows:
        if not n.topic:
            n.topic = legacy_topic(n.title)
            session.add(n)
        key = (n.user_id, n.job_id, n.topic, n.run_id if n.run_id else n.id)
        group = (n.user_id, n.job_id, n.topic)
        newest_run = next((k for k in seen if k[:3] == group), None)
        if (newest_run and newest_run != key) or (n.read and n.created_at < old_read):
            session.delete(n)
            removed += 1
            continue
        seen.add(key)
    session.commit()
    return {"removed": removed}
