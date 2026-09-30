"""실제 출퇴근 기록을 근무일별 사실로 정리한다 (판단은 judge/engine.py가 한다)."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.calc import schedule as sch
from app.calc.age import age_on
from app.calc.timeutil import night_minutes, weekday_key


@dataclass
class DayFact:
    """출퇴근 기록 1건에서 나온 사실."""
    day: date
    clock_in: datetime
    clock_out: datetime
    age: int              # 그날의 만 나이
    span_min: int         # 출근부터 퇴근까지 (쉬는 시간 포함)
    break_min: int | None  # 계약상 그 요일 쉬는 시간, 모르면 None
    work_min: int         # 쉬는 시간을 뺀 근로시간
    night_min: int        # 밤 10시~오전 6시 사이 분 (쉬는 시간을 빼지 않은 값)

    def when(self) -> str:
        return f"{self.day.month}월 {self.day.day}일 {self.clock_in:%H:%M}~{self.clock_out:%H:%M}"


def day_facts(records: list, schedule: dict, birth: date, night_start: str, night_end: str) -> list[DayFact]:
    """퇴근까지 기록된 출퇴근만 정리한다. 쉬는 시간은 기록되지 않으므로 계약상 쉬는 시간을 쓴다."""
    out = []
    for r in records:
        if not r.clock_out or r.clock_out <= r.clock_in:
            continue
        d = r.clock_in.date()
        slot = sch.slot_for(schedule, weekday_key(d), r.clock_in)  # 하루 여러 번 근무하면 가까운 시간대
        brk = sch.break_min(slot) if slot else None
        span = int((r.clock_out - r.clock_in).total_seconds() // 60)
        out.append(DayFact(day=d, clock_in=r.clock_in, clock_out=r.clock_out, age=age_on(birth, d), span_min=span,
                           break_min=brk, work_min=max(0, span - (brk or 0)),
                           night_min=night_minutes(r.clock_in, r.clock_out, night_start, night_end)))
    return out


def by_week(facts: list[DayFact]) -> dict[date, list[DayFact]]:
    """월요일을 기준으로 주별로 묶는다."""
    weeks: dict[date, list[DayFact]] = defaultdict(list)
    for f in facts:
        weeks[f.day - timedelta(days=f.day.weekday())].append(f)
    return dict(sorted(weeks.items()))


def by_day(facts: list[DayFact]) -> dict[date, list[DayFact]]:
    """하루에 여러 번 출퇴근한 경우를 합친다."""
    days: dict[date, list[DayFact]] = defaultdict(list)
    for f in facts:
        days[f.day].append(f)
    return dict(sorted(days.items()))
