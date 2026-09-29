"""임금 계산. 금액 계산은 전부 여기서 코드로 한다 (AI에게 맡기지 않음)."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.calc import schedule as sch
from app.calc.age import age_on
from app.calc.params import P
from app.calc.timeutil import night_minutes, weekday_key


@dataclass
class PayResult:
    month: str
    wage: int
    work_min: int = 0
    night_min: int = 0
    overtime_min: int = 0
    base: int = 0
    weekly_holiday: int = 0
    premium: int = 0
    total: int = 0
    notes: list = field(default_factory=list)
    weeks: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__


def record_work_min(clock_in: datetime, clock_out: datetime, slot) -> tuple[int, bool]:
    """기록 1건의 근무 분(쉬는 시간 제외)과 쉬는 시간을 알았는지 여부."""
    raw = int((clock_out - clock_in).total_seconds() // 60)
    brk = sch.break_min(slot) if slot else None
    return max(0, raw - (brk or 0)), brk is not None


def calc_month(records: list, schedule: dict, wage: int, size: str, birth: date, month: str) -> PayResult:
    y, m = map(int, month.split("-"))
    r = PayResult(month=month, wage=wage)
    minor = P()["minor"]
    prem = P()["premium"]
    wh = P()["weekly_holiday"]
    apply_premium = size == "5+"
    if size == "unknown":
        r.notes.append("사업장 인원을 몰라 가산수당은 계산하지 않았어요 (확인 필요).")
    unknown_break = False
    by_week = defaultdict(set)

    for rec in records:
        if not rec.clock_out or rec.clock_in.year != y or rec.clock_in.month != m:
            continue
        d = rec.clock_in.date()
        slot = schedule.get(weekday_key(d))
        mins, brk_known = record_work_min(rec.clock_in, rec.clock_out, slot)
        unknown_break |= not brk_known
        r.work_min += mins
        nmin = night_minutes(rec.clock_in, rec.clock_out, minor["night_start"], minor["night_end"])
        r.night_min += nmin
        daily_limit = minor["daily_limit_min"] if age_on(birth, d) < minor["age"] else prem["adult_daily_min"]
        r.overtime_min += max(0, mins - daily_limit)
        monday = d - timedelta(days=d.weekday())
        by_week[monday].add(weekday_key(d))

    r.base = round(r.work_min / 60 * wage)
    if unknown_break:
        r.notes.append("쉬는 시간을 모르는 날은 쉬는 시간 없이 계산했어요.")

    sched_week = sch.weekly_min(schedule)
    if schedule and sched_week >= wh["min_weekly_min"]:
        paid_hours = min(sched_week, wh["full_week_min"]) / wh["full_week_min"] * wh["paid_day_min"] / 60
        for monday, days in sorted(by_week.items()):
            full = set(schedule.keys()) <= days
            amt = round(paid_hours * wage) if full else 0
            r.weeks.append({"week": monday.isoformat(), "attended": sorted(days), "full": full, "weekly_holiday": amt})
            r.weekly_holiday += amt
        r.notes.append("주휴수당은 계약상 근무 요일을 모두 출근한 주에만 넣었어요.")
    elif schedule:
        r.notes.append("계약상 주 근무시간이 15시간 미만이라 주휴수당 대상이 아니에요.")
    else:
        r.notes.append("근무 요일과 시간이 등록되지 않아 주휴수당은 계산하지 않았어요.")

    if apply_premium:
        r.premium = round((r.night_min + r.overtime_min) / 60 * wage * prem["rate"])
    r.total = r.base + r.weekly_holiday + r.premium
    return r


def settlement_status(quit_date: date, today: date, paid) -> dict:
    """퇴직 후 임금 지급 기한 확인."""
    days = P()["settlement"]["days"]
    due = quit_date + timedelta(days=days)
    left = (due - today).days
    if paid is True:
        status = "ok"
    elif left >= 0:
        status = "warn"
    else:
        status = "bad"
    return {"quit_date": quit_date.isoformat(), "due": due.isoformat(), "left": left, "status": status,
            "claim_years": P()["wage_claim"]["years"], "law": P()["settlement"]["law"]}
