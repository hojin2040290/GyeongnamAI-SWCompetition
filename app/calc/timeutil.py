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


WEEKDAYS = "월화수목금토일"


def _as_dt(v):
    """날짜, 시각, ISO 글('2026-10-01', '2026-10-01T02:48:05')을 모두 받는다 (JSON에 저장된 값은 글이다)."""
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def fmt_date(d) -> str:
    """'2026년 10월 1일 (목)'. 없으면 빈 글."""
    if not d:
        return ""
    d = _as_dt(d)
    return f"{d.year}년 {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"


def fmt_dt(dt, sec: bool = True) -> str:
    """'2026년 10월 1일 (목) 02:48:05' (한국 시간). 증거와 상담 자료에 쓰므로 초까지 적는다."""
    if not dt:
        return ""
    dt = _as_dt(dt)
    return f"{fmt_date(dt)} {dt:%H:%M:%S}" if sec else f"{fmt_date(dt)} {dt:%H:%M}"


def fmt_month(month: str) -> str:
    """'2026-09' → '2026년 9월'. 모양이 다르면 그대로."""
    try:
        y, m = str(month).split("-")
        return f"{int(y)}년 {int(m)}월"
    except ValueError:
        return str(month)
