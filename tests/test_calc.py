from datetime import date, datetime

import pytest

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


# ---------- 월급날 (그 달에 없는 날이면 그 달 마지막 날) ----------
def test_payday_month_end():
    from app.calc.pay import is_payday, payday_in, payday_text
    assert payday_in(2026, 2, 31) == date(2026, 2, 28) and payday_in(2028, 2, 31) == date(2028, 2, 29)  # 말일
    assert payday_in(2026, 2, 30) == date(2026, 2, 28) and payday_in(2026, 4, 30) == date(2026, 4, 30)
    assert payday_in(2026, 4, 31) == date(2026, 4, 30) and payday_in(2026, 9, 10) == date(2026, 9, 10)
    assert is_payday(31, date(2026, 2, 28)) and not is_payday(31, date(2026, 2, 27))
    assert is_payday(30, date(2026, 2, 28)) and is_payday(10, date(2026, 9, 10)) and not is_payday(None, date(2026, 9, 10))
    assert payday_text(31) == "말일" and payday_text(10) == "10일" and payday_text(None) == ""


def test_break_any_minutes():
    """쉬는 시간은 정해진 선택지가 아니어도 몇 분이든 적을 수 있다."""
    from app.calc import schedule as sch
    assert sch.parse_break("19분") == 19
    assert sch.parse_break("3시간") == 180
    assert sch.parse_break("1시간 15분") == 75
    assert sch.parse_break("1시간15분") == 75
    assert sch.parse_break("없음") == 0
    assert sch.parse_break("모름") is None and sch.parse_break("조금") is None and sch.parse_break("") is None
    assert [sch.break_text(m) for m in (0, 15, 60, 75, 180)] == ["없음", "15분", "1시간", "1시간 15분", "3시간"]
    # 계산에 그대로 쓰인다: 17:00~21:00에 19분 쉬면 221분
    assert sch.work_min({"start": "17:00", "end": "21:00", "brk": "19분"}) == 221
    # 저장할 때 모양을 맞추고, 일하는 시간보다 길면 막는다
    out = sch.clean({"월": {"start": "09:00", "end": "18:00", "brk": "90분"}, "화": {"start": "10:00", "end": "11:00", "brk": "모름"}})
    assert out["월"]["brk"] == "1시간 30분" and out["화"]["brk"] == "모름"
    for bad in ("4시간", "5시간 1분", "잠깐"):
        with pytest.raises(ValueError):
            sch.clean({"월": {"start": "17:00", "end": "21:00", "brk": bad}})


def test_time_display():
    """상담 자료와 증거에 쓰는 날짜·시각 표시: 연월일, 요일, 초까지 (한국 시간)."""
    from app.calc.timeutil import fmt_date, fmt_dt, fmt_month
    assert fmt_dt(datetime(2026, 10, 1, 2, 48, 5)) == "2026년 10월 1일 (목) 02:48:05"
    assert fmt_dt(datetime(2026, 10, 1, 2, 48, 5), sec=False) == "2026년 10월 1일 (목) 02:48"
    assert fmt_date(date(2026, 9, 30)) == "2026년 9월 30일 (수)"
    assert fmt_month("2026-09") == "2026년 9월" and fmt_month("이상한값") == "이상한값"
    assert fmt_dt(None) == "" and fmt_date(None) == ""
