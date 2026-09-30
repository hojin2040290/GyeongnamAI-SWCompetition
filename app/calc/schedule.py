"""근무 요일과 시간(계약상 소정근로시간) 계산."""
import json
import re
from datetime import datetime, timedelta

from app.calc.timeutil import DAY_KEYS, night_minutes, to_min

BREAK_MIN = {"없음": 0, "30분": 30, "1시간": 60, "1시간 30분": 90, "2시간": 120}  # 화면의 빠른 선택지
_BREAK = re.compile(r"^(?:(\d{1,2})\s*시간)?\s*(?:(\d{1,4})\s*분)?$")


def parse_break(text) -> int | None:
    """쉬는 시간 글('없음', '19분', '3시간', '1시간 15분')을 분으로. '모름'이나 읽을 수 없는 값은 None."""
    text = str(text or "").strip()
    if text == "없음":
        return 0
    m = _BREAK.match(text)
    if not text or not m or not (m.group(1) or m.group(2)):
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def break_text(minutes: int) -> str:
    """분을 '없음', '19분', '3시간', '1시간 15분'으로."""
    h, m = divmod(max(0, minutes), 60)
    if not h and not m:
        return "없음"
    return " ".join(x for x in (f"{h}시간" if h else "", f"{m}분" if m else "") if x)


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
    return parse_break(slot.get("brk", "모름"))


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


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SLOTS_PER_DAY = 4  # 한 요일에 넣을 수 있는 시간대 수


def clean(schedule) -> dict:
    """화면에서 받은 근무 요일과 시간 검사. 요일, 시각(HH:MM), 쉬는 시간이 정해진 모양이 아니면 ValueError.
    시간대가 하나면 예전 형식(dict)으로, 여러 개면 목록으로 돌려준다."""
    if not isinstance(schedule, dict):
        raise ValueError("근무 요일과 시간 형식이 맞지 않아요")
    out = {}
    for day, value in schedule.items():
        if day not in DAY_KEYS:
            raise ValueError(f"알 수 없는 요일이에요: {str(day)[:10]}")
        raw = value if isinstance(value, list) else [value]
        if not raw or len(raw) > SLOTS_PER_DAY:
            raise ValueError(f"{day}요일 시간대는 1개부터 {SLOTS_PER_DAY}개까지 넣을 수 있어요")
        slots = []
        for x in raw:
            if not isinstance(x, dict) or not _HHMM.match(str(x.get("start", ""))) or not _HHMM.match(str(x.get("end", ""))):
                raise ValueError(f"{day}요일 시작, 끝 시각은 00:00 모양이어야 해요")
            brk = str(x.get("brk", "모름") or "모름").strip()
            if brk != "모름":  # 몇 분이든 적을 수 있다. 저장은 '1시간 15분' 모양으로 맞춘다
                mins = parse_break(brk)
                if mins is None:
                    raise ValueError(f"{day}요일 쉬는 시간은 '30분', '1시간 15분'처럼 적어 주세요")
                if mins >= span_min(x):
                    raise ValueError(f"{day}요일 쉬는 시간이 일하는 시간({x['start']}~{x['end']})보다 길어요")
                brk = break_text(mins)
            slots.append({"start": x["start"], "end": x["end"], "brk": brk})
        out[day] = slots[0] if len(slots) == 1 else slots
    return out
