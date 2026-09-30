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


def slots_of(value) -> list[dict]:
    """한 요일의 근무 시간대 목록. 예전처럼 시간대 하나(dict)로 저장된 것도, 여러 개(list)도 받는다."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict) and v.get("start") and v.get("end")]
    return []


def day_work_min(value) -> int:
    """하루 근무 분 (시간대가 여러 개면 합친다)."""
    return sum(work_min(s) for s in slots_of(value))


def weekly_min(schedule: dict) -> int:
    return sum(day_work_min(v) for v in schedule.values())


def slot_for(schedule: dict, day: str, clock_in: datetime) -> dict | None:
    """출근 기록에 맞는 계약상 시간대: 그 요일 시간대 중 시작 시각이 출근 시각과 가장 가까운 것."""
    slots = slots_of(schedule.get(day))
    if not slots:
        return None
    t = clock_in.hour * 60 + clock_in.minute

    def gap(slot: dict) -> int:
        d = abs(to_min(slot["start"]) - t)
        return min(d, 1440 - d)
    return min(slots, key=gap)


def slot_text(slot: dict) -> str:
    return f"{slot['start']}~{slot['end']}"


def day_text(value) -> str:
    """'10:00~14:00, 18:00~22:00'처럼 그 요일 시간대를 모두 적는다."""
    return ", ".join(slot_text(x) for x in slots_of(value))


def slot_has_night(slot: dict, night_start: str, night_end: str) -> bool:
    base = datetime(2000, 1, 3)  # 기준 날짜 (계산용)
    s = base + timedelta(minutes=to_min(slot["start"]))
    e = s + timedelta(minutes=span_min(slot))
    return night_minutes(s, e, night_start, night_end) > 0
