"""임금 계산 확인용 시험 DB 만들기 (가상 정보만 사용).

실행: python -m app.demo_db                 → data/demo.db 를 새로 만든다
      python -m app.demo_db --db data/x.db  → 다른 이름으로
      python -m app.demo_db --force         → 같은 이름의 파일이 있으면 지우고 다시 만든다
서버를 이 DB로 띄우기: DB_PATH=data/demo.db uvicorn app.main:app --port 8080
로그인: demo@example.com / test1234

계정과 사업장은 앱의 API로 만들고(지금 코드와 같은 모양), 근무 기록은 정해 둔 시각으로 넣는다.
금액은 모두 코드(app/calc/pay.py)가 계산한 값을 보여 준다.
"""
import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

EMAIL, PASSWORD, BIRTH = "demo@example.com", "test1234", "2009-05-01"  # 만 17세 (청소년)
MONTH = "2026-09"
SCHEDULE = {"월": {"start": "17:00", "end": "22:00", "brk": "30분"},
            "수": {"start": "17:00", "end": "22:00", "brk": "30분"},
            "금": {"start": "17:00", "end": "22:00", "brk": "30분"},
            "토": {"start": "10:00", "end": "16:00", "brk": "1시간"}}
# (출근, 퇴근, 설명). 2026년 9월 1일은 화요일
SHIFTS = [
    ("2026-09-02 17:00", "2026-09-02 22:00", "첫 주: 월요일 전에 일을 시작해 주휴수당 없음"),
    ("2026-09-04 17:00", "2026-09-04 22:00", ""),
    ("2026-09-05 10:00", "2026-09-05 16:00", ""),
    ("2026-09-07 17:00", "2026-09-07 22:00", "둘째 주: 계약한 요일 모두 출근 (주휴수당)"),
    ("2026-09-09 17:00", "2026-09-09 22:00", ""),
    ("2026-09-11 17:00", "2026-09-11 22:00", ""),
    ("2026-09-12 10:00", "2026-09-12 16:00", ""),
    ("2026-09-14 17:00", "2026-09-14 22:00", "셋째 주: 모두 출근, 야간과 하루 7시간 초과 포함 (주휴수당, 가산수당)"),
    ("2026-09-16 17:00", "2026-09-16 23:00", "22시 넘어 23시 퇴근 (야간 1시간)"),
    ("2026-09-18 17:00", "2026-09-18 22:00", ""),
    ("2026-09-19 10:00", "2026-09-19 19:00", "19시 퇴근 (쉬는 1시간 빼고 8시간, 청소년 하루 7시간 초과)"),
    ("2026-09-21 17:00", "2026-09-21 22:00", "넷째 주: 추석 연휴로 금, 토 결근 (주휴수당 없음)"),
    ("2026-09-23 17:00", "2026-09-23 22:00", ""),
    ("2026-09-28 17:00", "2026-09-28 22:00", "다섯째 주: 9월 안에서는 월, 수만"),
    ("2026-09-30 17:00", "2026-09-30 22:00", ""),
]
VOID = ("2026-09-10 17:00", "2026-09-10 17:01", "실수로 누른 출근 (계산에서 빠짐)")


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def build() -> dict:
    """지금 설정된 DB에 시험 계정, 사업장, 근무 기록, 받은 금액을 넣고 코드가 계산한 결과를 돌려준다."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from app.calc import pay as paycalc
    from app.calc.params import P
    from app.calc.timeutil import now_kst
    from app.db import engine, init_db
    from app.main import app
    from app.models import WorkRecord

    init_db()
    c = TestClient(app)  # 서버를 띄우지 않고 앱의 API를 부른다 (예약 작업은 돌지 않음)
    r = c.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD, "birth_date": BIRTH})
    if r.status_code != 200:
        raise SystemExit(f"계정을 만들지 못했어요: {r.text}")
    wage = P()["min_wage"]["by_year"]["2026"]
    job = c.post("/api/jobs", json={"name": "가상분식 시험점", "wage": wage, "start_date": "2026-09-01", "size": "5+",
                                    "payday": 10, "schedule": SCHEDULE, "contract_written": True, "copy_received": True,
                                    "probation": "no"})
    if job.status_code != 200:
        raise SystemExit(f"사업장을 만들지 못했어요: {job.text}")
    job_id = job.json()["id"]
    user_id = c.get("/api/me").json()["id"]
    with Session(engine) as s:
        for a, b, _ in SHIFTS:
            s.add(WorkRecord(user_id=user_id, job_id=job_id, clock_in=_dt(a), clock_out=_dt(b)))
        s.add(WorkRecord(user_id=user_id, job_id=job_id, clock_in=_dt(VOID[0]), clock_out=_dt(VOID[1]),
                         void_at=now_kst(), void_reason=VOID[2]))
        s.commit()
        recs = s.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.void_at == None)).all()  # noqa: E711
        result = paycalc.calc_month(recs, SCHEDULE, wage, "5+", date.fromisoformat(BIRTH), MONTH)
    paid = result.base  # 기본급만 받은 경우 (주휴수당, 가산수당을 못 받음)
    c.post(f"/api/jobs/{job_id}/payslip", data={"month": MONTH, "amount": str(paid), "check": "false"})
    return {"wage": wage, "job_id": job_id, "result": result.as_dict(), "paid": paid}


def show(out: dict) -> None:
    from app.calc import schedule as sch
    from app.calc.pay import record_work_min
    from app.calc.params import P
    from app.calc.timeutil import night_minutes, weekday_key
    minor = P()["minor"]
    print(f"\n계정: {EMAIL} / {PASSWORD} (생일 {BIRTH}, 만 17세)")
    print(f"사업장: 가상분식 시험점, 시급 {out['wage']:,}원, 5명 이상, 월급날 10일, 근무 시작 2026-09-01")
    print("계약 근무: " + ", ".join(f"{d} {v['start']}~{v['end']} (쉬는 시간 {v['brk']})" for d, v in SCHEDULE.items()))
    print(f"\n{MONTH} 근무 기록 (쉬는 시간은 계약의 그 요일 값으로 뺌)")
    for a, b, note in SHIFTS:
        i, o = _dt(a), _dt(b)
        slot = sch.slot_for(SCHEDULE, weekday_key(i.date()), i)
        mins, _ = record_work_min(i, o, slot)
        night = night_minutes(i, o, minor["night_start"], minor["night_end"])
        print(f"  {a[5:10]}({weekday_key(i.date())}) {a[11:]}~{b[11:]}  근무 {mins // 60}시간 {mins % 60:02d}분"
              + (f"  야간 {night}분" if night else "") + (f"  ← {note}" if note else ""))
    print(f"  {VOID[0][5:10]} {VOID[0][11:]}~{VOID[1][11:]}  ← {VOID[2]}")
    r = out["result"]
    print(f"\n코드가 계산한 {MONTH} 급여")
    print(f"  근무 {r['work_min'] // 60}시간 {r['work_min'] % 60}분 × {out['wage']:,}원 = 기본급 {r['base']:,}원")
    for w in r["weeks"]:
        print(f"  {w['week']} 주: 출근 {','.join(w['attended'])} → 주휴수당 {w['weekly_holiday']:,}원")
    print(f"  야간 {r['night_min']}분 + 하루 한도 초과 {r['overtime_min']}분 → 가산수당 {r['premium']:,}원")
    print(f"  합계 {r['total']:,}원")
    print(f"받은 금액(시험용): {out['paid']:,}원 (기본급만) → 차이 {r['total'] - out['paid']:,}원")
    for n in r["notes"]:
        print(f"  참고: {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description="임금 계산 확인용 시험 DB 만들기")
    ap.add_argument("--db", default="data/demo.db", help="만들 DB 파일 (기본 data/demo.db)")
    ap.add_argument("--force", action="store_true", help="같은 이름의 파일이 있으면 지우고 다시 만들기")
    args = ap.parse_args()
    root = Path(__file__).resolve().parent.parent
    db = Path(args.db) if Path(args.db).is_absolute() else root / args.db
    if db.name == "app.db":
        sys.exit("평소 쓰는 data/app.db에는 만들지 않아요. 다른 이름을 써 주세요.")
    if db.exists():
        if not args.force:
            sys.exit(f"{db} 가 이미 있어요. 지우고 다시 만들려면 --force를 붙여 주세요.")
        db.unlink()
    os.environ["DB_PATH"] = str(db)  # app을 불러오기 전에 정해야 이 파일을 쓴다
    os.environ.setdefault("LLM_FAKE", "false")
    show(build())
    print(f"\n만든 파일: {db}")
    print(f"서버 실행: DB_PATH={args.db} uvicorn app.main:app --port 8080")


if __name__ == "__main__":
    main()
