from datetime import date, datetime

from app.calc import schedule as sch
from app.calc.age import age_on, is_youth_protection
from app.calc.pay import calc_month, settlement_status
from app.calc.timeutil import night_minutes
from app.judge import engine
from app.models import WorkRecord


def rec(d, a, b):
    s = datetime.fromisoformat(f"{d}T{a}")
    e = datetime.fromisoformat(f"{d}T{b}")
    if e <= s:
        e = e.replace(day=e.day + 1)
    return WorkRecord(user_id=1, job_id=1, clock_in=s, clock_out=e)


def test_age_on_birthday_boundary():
    assert age_on(date(2009, 3, 14), date(2027, 3, 13)) == 17
    assert age_on(date(2009, 3, 14), date(2027, 3, 14)) == 18


def test_youth_protection_year_rule():
    # 2008년생은 2027년 1월 1일부터 청소년 보호법의 청소년이 아님
    assert is_youth_protection(date(2008, 12, 31), date(2026, 12, 31), 19, True)
    assert not is_youth_protection(date(2008, 12, 31), date(2027, 1, 1), 19, True)


def test_night_minutes_cross_midnight():
    s, e = datetime(2026, 9, 25, 21, 0), datetime(2026, 9, 26, 1, 0)
    assert night_minutes(s, e, "22:00", "06:00") == 180


def test_schedule_week_minutes():
    s = {"수": {"start": "18:00", "end": "22:00", "brk": "없음"}, "토": {"start": "14:00", "end": "22:30", "brk": "30분"}}
    assert sch.weekly_min(s) == 240 + 480


def test_pay_weekly_holiday_full_week():
    s = {d: {"start": "10:00", "end": "14:00", "brk": "없음"} for d in ["월", "화", "수", "목"]}  # 주 16시간
    recs = [rec(f"2026-09-{d:02d}", "10:00", "14:00") for d in (7, 8, 9, 10)]
    r = calc_month(recs, s, 10000, "lt5", date(2000, 1, 1), "2026-09")
    assert r.work_min == 960 and r.base == 160000
    # 16/40 x 8시간 x 10000원 = 32000원
    assert r.weekly_holiday == 32000 and r.premium == 0


def test_pay_premium_only_5plus():
    s = {"금": {"start": "20:00", "end": "23:00", "brk": "없음"}}
    recs = [rec("2026-09-04", "20:00", "23:00")]
    assert calc_month(recs, s, 10000, "5+", date(2000, 1, 1), "2026-09").premium == 5000  # 야간 1시간 x 0.5
    assert calc_month(recs, s, 10000, "lt5", date(2000, 1, 1), "2026-09").premium == 0


def test_settlement():
    st = settlement_status(date(2026, 9, 1), date(2026, 9, 20), None)
    assert st["due"] == "2026-09-15" and st["status"] == "bad"
    assert settlement_status(date(2026, 9, 1), date(2026, 9, 10), None)["status"] == "warn"


def test_judge_minor_night_and_wage():
    f = engine.Facts(birth=date(2009, 3, 14), on=date(2026, 9, 29), wage=9000, probation="no",
                     schedule={"금": {"start": "18:00", "end": "23:30", "brk": "30분"}}, size="unknown")
    items = engine.judge(f, "seek")
    laws = {(i.law, i.status) for i in items}
    assert ("근로기준법 제70조", "bad") in laws
    assert ("최저임금법 제5조", "bad") in laws


def test_verify_downgrades_without_basis():
    it = engine.Item("테스트", engine.BAD, "근거 없음")
    out = engine.verify([it], None)
    assert out[0].status == engine.WARN
