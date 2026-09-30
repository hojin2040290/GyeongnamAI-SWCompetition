"""알림 정리: 같은 종류의 새 알림이 오면 예전 알림을 지우고, 한 번의 실행에서 보낸 알림끼리는 남긴다."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import notices
from app.calc.timeutil import now_kst
from app.db import engine, init_db
from app.main import app
from app.models import AgentQuestion, Notification

UID, JOB = 9001, 9001  # 가상 사용자와 사업장 번호 (알림 표만 쓴다)


def _titles(s: Session, job_id=JOB) -> list[str]:
    return [n.title for n in s.exec(select(Notification).where(Notification.user_id == UID, Notification.job_id == job_id)
                                    .order_by(Notification.id)).all()]


def test_new_notice_replaces_old_same_kind():
    init_db()
    with Session(engine) as s:
        notices.send(s, UID, JOB, "계약서 점검 완료", "확인 필요 3건", "contract_check", "run1")
        notices.send(s, UID, JOB, "위반이 의심돼요", "야간근로", "contract_check", "run1")  # 같은 실행: 둘 다 남김
        assert _titles(s) == ["계약서 점검 완료", "위반이 의심돼요"]
        notices.send(s, UID, JOB, "계약서 점검 완료", "확인 필요 1건", "contract_check", "run2")  # 다음 실행: 예전 것 지움
        assert _titles(s) == ["계약서 점검 완료"]
        notices.send(s, UID, JOB, "2026-08 급여 점검 결과", "적게 받음", "payday:2026-08", "r3")
        notices.send(s, UID, JOB, "2026-09 급여 점검 결과", "같음", "payday:2026-09", "r4")  # 달이 다르면 따로
        notices.send(s, UID, JOB, "오늘 자동 점검 (10월 1일)", "없음", "daily")
        notices.send(s, UID, JOB, "오늘 자동 점검 (10월 2일)", "없음", "daily")  # 어제 것 대신
        assert _titles(s) == ["계약서 점검 완료", "2026-08 급여 점검 결과", "2026-09 급여 점검 결과", "오늘 자동 점검 (10월 2일)"]
        notices.send(s, UID, JOB + 1, "오늘 자동 점검 (10월 2일)", "없음", "daily")  # 다른 사업장은 건드리지 않음
        assert "오늘 자동 점검 (10월 2일)" in _titles(s)


def test_same_notice_is_not_duplicated():
    with Session(engine) as s:
        notices.clear(s, UID, JOB, "open_record")
        for _ in range(3):
            notices.send(s, UID, JOB, "퇴근을 누르지 않은 것 같아요", "16시간 지남", "open_record", "")
        assert _titles(s).count("퇴근을 누르지 않은 것 같아요") == 1


def test_tidy_legacy_and_old_read():
    with Session(engine) as s:
        old = now_kst() - timedelta(days=5)
        for i, title in enumerate(["계약서 점검 완료", "계약서 점검 완료", "상담 사전 자료를 만들었어요 (사건 요약은 AI 응답 대기 중)",
                                   "상담 사전 자료를 만들었어요"]):
            s.add(Notification(user_id=UID, job_id=JOB + 5, title=title, body=str(i), created_at=old + timedelta(hours=i)))
        s.add(Notification(user_id=UID, job_id=JOB + 5, title="아주 예전 알림", body="x", read=True,
                           created_at=now_kst() - timedelta(days=notices.KEEP_DAYS + 1)))
        s.commit()
        notices.tidy(s)
        rows = s.exec(select(Notification).where(Notification.job_id == JOB + 5)).all()
        assert sorted(n.body for n in rows) == ["1", "3"]  # 종류마다 가장 최근 것만, 오래전에 읽은 알림은 지움
        assert {n.topic for n in rows} == {"contract_check", "report"}


def test_question_notice_cleared_when_answered():
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"email": "notice.q@example.com", "password": "test1234", "birth_date": "2009-05-01"})
        c.post("/api/auth/login", json={"email": "notice.q@example.com", "password": "test1234"})
        j = c.post("/api/jobs", json={"name": "가상분식 알림점"}).json()["id"]
        uid = c.get("/api/me").json()["id"]
        with Session(engine) as s:
            q = AgentQuestion(user_id=uid, job_id=j, event="contract_check", question="서류를 냈나요?", options_json="[]",
                              status="open", created_at=now_kst())
            s.add(q)
            s.commit()
            notices.send(s, uid, j, "에이전트가 물어볼 게 있어요", "서류를 냈나요?", "questions", "r")
            qid = q.id
        assert any(n["title"].startswith("에이전트가 물어볼") for n in c.get(f"/api/notifications?job_id={j}").json())
        c.delete(f"/api/questions/{qid}")
        assert not any(n["title"].startswith("에이전트가 물어볼") for n in c.get(f"/api/notifications?job_id={j}").json())
        # 사업장을 지우면 그 사업장 알림도 지운다
        with Session(engine) as s:
            notices.send(s, uid, j, "계약서 점검 완료", "x", "contract_check", "r")
        c.delete(f"/api/jobs/{j}")
        with Session(engine) as s:
            assert not s.exec(select(Notification).where(Notification.job_id == j)).all()
