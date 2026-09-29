"""근무 요일과 시간(계약상 소정근로시간) 계산."""
import json
from datetime import datetime, timedelta

from app.calc.timeutil import DAY_KEYS, night_minutes, to_min

BREAK_MIN = {"없음": 0, "30분": 30, "1시간": 60, "1시간 30분": 90, "2시간": 120}


def parse(schedule_json: str) -> dict:
    try:
        data = json.loads(schedule_json or "{}")
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if k in DAY_KEYS}


def span_min(slot: dict) -> int:
    a, b = to_min(slot["start"]), to_min(slot["end"])
    if b <= a:
        b += 1440
    return b - a


def break_min(slot: dict):
    """쉬는 시간(분). 모르면 None."""
    return BREAK_MIN.get(slot.get("brk", "모름"))


def work_min(slot: dict) -> int:
    return max(0, span_min(slot) - (break_min(slot) or 0))


def weekly_min(schedule: dict) -> int:
    return sum(work_min(s) for s in schedule.values())


def slot_has_night(slot: dict, night_start: str, night_end: str) -> bool:
    base = datetime(2000, 1, 3)  # 기준 날짜 (계산용)
    s = base + timedelta(minutes=to_min(slot["start"]))
    e = s + timedelta(minutes=span_min(slot))
    return night_minutes(s, e, night_start, night_end) > 0
