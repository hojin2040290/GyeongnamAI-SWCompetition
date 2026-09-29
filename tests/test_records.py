"""실제 출퇴근 기록으로 하는 판단 테스트. 모든 인물과 가게는 가상이다."""
from datetime import date, datetime

from app.calc.records import by_week, day_facts
from app.judge import engine
from app.law.lookup import parse_label
from app.models import WorkRecord

MINOR = date(2010, 5, 1)   # 2026년 가을 기준 만 16세
ADULT = date(2000, 1, 1)


def rec(start: str, end: str) -> WorkRecord:
    return WorkRecord(user_id=1, job_id=1, clock_in=datetime.fromisoformat(start), clock_out=datetime.fromisoformat(end))


def laws(items):
    return {(i.law, i.status) for i in items}


def test_day_facts_uses_contract_break_and_age_on_that_day():
    s = {"월": {"start": "10:00", "end": "15:00", "brk": "30분"}}
    # 2028-04-30은 만 17세, 2028-05-01(월)은 만 18세
    f = day_facts([rec("2028-05-01T10:00", "2028-05-01T15:00")], s, MINOR, "22:00", "06:00")[0]
    assert (f.age, f.span_min, f.break_min, f.work_min, f.night_min) == (18, 300, 30, 270, 0)


def test_open_record_is_ignored():
    r = WorkRecord(user_id=1, job_id=1, clock_in=datetime(2026, 9, 1, 10))
    assert day_facts([r], {}, ADULT, "22:00", "06:00") == []


def test_by_week_groups_on_monday():
    fs = day_facts([rec("2026-09-06T10:00", "2026-09-06T11:00"), rec("2026-09-07T10:00", "2026-09-07T11:00")],
                   {}, ADULT, "22:00", "06:00")
    assert [k.isoformat() for k in by_week(fs)] == ["2026-08-31", "2026-09-07"]


def test_minor_actual_night_work_is_caught():
    # 계약상은 21시까지지만 실제로 22시 30분까지 일함
    s = {"금": {"start": "17:00", "end": "21:00", "brk": "없음"}}
    items = engine.judge_records(MINOR, [rec("2026-09-25T17:00", "2026-09-25T22:30")], s)
    night = [i for i in items if i.law == "근로기준법 제70조"]
    assert night and night[0].status in ("bad", "warn") and night[0].source == "records"


def test_adult_night_work_is_not_minor_violation():
    s = {"금": {"start": "17:00", "end": "23:00", "brk": "30분"}}
    items = engine.judge_records(ADULT, [rec("2026-09-25T17:00", "2026-09-25T23:00")], s)
    assert "근로기준법 제70조" not in {i.law for i in items}


def test_minor_daily_and_weekly_hours():
    s = {d: {"start": "09:00", "end": "18:30", "brk": "1시간"} for d in ["월", "화", "수", "목", "금"]}
    recs = [rec(f"2026-09-{d:02d}T09:00", f"2026-09-{d:02d}T18:30") for d in range(21, 26)]  # 하루 8시간 30분
    assert ("근로기준법 제69조", "bad") in laws(engine.judge_records(MINOR, recs, s))


def test_minor_over_7_hours_needs_agreement():
    s = {"토": {"start": "10:00", "end": "18:00", "brk": "30분"}}  # 7시간 30분
    items = engine.judge_records(MINOR, [rec("2026-09-26T10:00", "2026-09-26T18:00")], s)
    assert ("근로기준법 제69조", "warn") in laws(items)


def test_break_shorter_than_needed_from_records():
    # 계약은 4시간이라 쉬는 시간이 없지만, 실제로 6시간을 일함
    s = {"수": {"start": "12:00", "end": "16:00", "brk": "없음"}}
    items = engine.judge_records(ADULT, [rec("2026-09-23T12:00", "2026-09-23T18:00")], s)
    assert ("근로기준법 제54조", "bad") in laws(items)


def test_break_unknown_on_unscheduled_day():
    items = engine.judge_records(ADULT, [rec("2026-09-27T10:00", "2026-09-27T16:00")], {})
    assert ("근로기준법 제54조", "warn") in laws(items)


def test_focus_only_checks_that_day():
    s = {"수": {"start": "12:00", "end": "16:00", "brk": "없음"}}
    recs = [rec("2026-09-23T12:00", "2026-09-23T18:00"), rec("2026-09-24T12:00", "2026-09-24T13:00")]
    assert engine.judge_records(ADULT, recs, s, focus=date(2026, 9, 24)) == []
    assert engine.judge_records(ADULT, recs, s, focus=date(2026, 9, 23))


def test_parse_law_label():
    assert parse_label("근로기준법 제70조") == ("근로기준법", "70")
    assert parse_label("최저임금법 제5조 제2항") == ("최저임금법", "5")
    assert parse_label("근로기준법 제76조의2") == ("근로기준법", "76의2")
    assert parse_label("청소년 보호법 제29조") == ("청소년 보호법", "29")
    assert parse_label("법 이름 없음") is None
