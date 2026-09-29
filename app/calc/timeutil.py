"""시각 계산. 서버 시각은 항상 여기서 만든다 (브라우저 시간은 쓰지 않음)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import TIMEZONE

KST = ZoneInfo(TIMEZONE)
DAY_KEYS = ["월", "화", "수", "목", "금", "토", "일"]


def now_kst() -> datetime:
    """서버가 요청을 받은 순간의 한국 시각 (DB에는 시간대 정보 없이 저장)."""
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


def today_kst() -> date:
    return now_kst().date()


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def night_minutes(start: datetime, end: datetime, night_start: str, night_end: str) -> int:
    """start~end 구간 중 야간(night_start~다음날 night_end)에 해당하는 분."""
    ns, ne = to_min(night_start), to_min(night_end)
    total = 0
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        base = datetime.combine(day, datetime.min.time())
        win_start = base + timedelta(minutes=ns)
        win_end = base + timedelta(days=1, minutes=ne)
        s, e = max(start, win_start), min(end, win_end)
        if e > s:
            total += int((e - s).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def weekday_key(d: date) -> str:
    return DAY_KEYS[d.weekday()]
