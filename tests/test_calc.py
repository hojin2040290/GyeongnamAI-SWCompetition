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


# ---------- 하루 여러 번 근무 (쪼개기 근무) ----------
def test_split_shift_schedule_minutes():
    from app.calc import schedule as sch
    day = [{"start": "10:00", "end": "14:00", "brk": "없음"}, {"start": "18:00", "end": "22:00", "brk": "30분"}]
    assert sch.slots_of(day) == day and sch.slots_of(day[0]) == [day[0]] and sch.slots_of(None) == []
    assert sch.day_work_min(day) == 240 + 210
    assert sch.weekly_min({"월": day, "수": day[0]}) == 450 + 240  # 예전 형식(시간대 하나)과 섞여도 된다
    assert sch.day_text(day) == "10:00~14:00, 18:00~22:00"


def test_split_shift_record_uses_nearest_slot_break():
    """저녁에 출근한 기록은 저녁 시간대의 쉬는 시간(30분)으로 계산한다."""
    from datetime import datetime
    from app.calc import schedule as sch
    day = [{"start": "10:00", "end": "14:00", "brk": "없음"}, {"start": "18:00", "end": "22:00", "brk": "30분"}]
    assert sch.slot_for({"월": day}, "월", datetime(2026, 9, 7, 17, 55))["brk"] == "30분"
    assert sch.slot_for({"월": day}, "월", datetime(2026, 9, 7, 9, 58))["brk"] == "없음"
    assert sch.slot_for({"월": day}, "화", datetime(2026, 9, 8, 18, 0)) is None


def test_split_shift_judged_per_slot():
    """쉬는 시간은 시간대마다, 청소년 하루 한도는 그날 시간대를 합쳐서 본다."""
    from datetime import date
    from app.judge import engine
    day = [{"start": "09:00", "end": "12:30", "brk": "없음"}, {"start": "13:30", "end": "18:30", "brk": "없음"}]
    facts = engine.Facts(birth=date(2010, 5, 1), on=date(2026, 9, 7), wage=10030, probation="no",
                         schedule={"토": day}, size="lt5", contract_written=True, copy_received=True, consent="냈어요")
    items = engine.judge(facts, "contract")
    brk = [i for i in items if i.law == "근로기준법 제54조"]
    assert len(brk) == 1 and "13:30~18:30" in brk[0].basis[0]  # 4시간 이상인 시간대만 쉬는 시간이 필요
    hours = [i for i in items if i.law == "근로기준법 제69조"]
    assert hours and hours[0].status == "bad"  # 하루 3시간 30분 + 5시간: 연장해도 하루 8시간 한도를 넘음
