"""법 기준표 대조.

역할 분담: 법 조항 해당 여부 판단은 AI가, 시간과 금액 계산은 코드가 한다.
- 이 파일의 규칙(judge, judge_records)은 법 기준값과 코드 계산으로 '검토할 항목'과 근거가 되는 사실을 만든다.
  규칙이 낸 결과(rule_status)는 화면에 보여 주지 않고, AI 판단을 검증하는 장치와 목표 성능 측정에만 쓴다.
- await_ai(): AI가 판단하기 전 상태로 바꾼다. 필요한 정보가 없는 항목만 '확인 필요', 나머지는 '확인 중'.
- cross_check(): AI 판단이 코드 계산과 맞지 않으면 '확인 필요'로 되돌린다.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.calc import bizno
from app.calc import records as rec
from app.calc import schedule as sch
from app.calc.age import age_on, is_youth_protection
from app.calc import params
from app.calc.params import P

OK, WARN, BAD, PENDING = "ok", "warn", "bad", "pending"  # PENDING: AI 판단 전 (확인 중)


@dataclass
class Item:
    law: str
    status: str
    text: str
    basis: list = field(default_factory=list)   # 판단에 쓴 사실
    needed: list = field(default_factory=list)  # 부족해서 확인이 필요한 정보
    ai_pending: bool = False                    # AI 연결 후 판단할 항목
    source: str = "input"                       # input(입력한 정보, 계약서), records(실제 출퇴근 기록)


@dataclass
class Facts:
    """판단에 쓰는 사실. 모두 사용자가 입력했거나 코드가 계산한 값."""
    birth: date
    on: date
    wage: Optional[int] = None
    probation: str = "unknown"
    probation_months: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    no_end: bool = False
    schedule: dict = field(default_factory=dict)
    size: str = "unknown"
    industry: str = ""
    work_desc: str = ""
    contract_written: Optional[bool] = None
    copy_received: Optional[bool] = None
    consent: str = ""
    contract_fields: dict = field(default_factory=dict)
    biz_no: Optional[str] = None  # None이면 확인하지 않음 (지원 전 확인)


def verify(items: list[Item], facts: Facts) -> list[Item]:
    """검증 장치: 근거가 없거나 필요한 정보가 비어 있는데 정상·위반으로 나온 결과는 확인 필요로 바꾼다."""
    for it in items:
        if it.status in (OK, BAD) and it.needed:
            it.status = WARN
        if it.status == BAD and not it.basis:
            it.status = WARN
            it.needed.append("판단 근거")
    return items


def _min_wage(year: int):
    return params.min_wage(year)[0]


def judge(facts: Facts, stage: str) -> list[Item]:
    """stage: seek(지원 전), contract(계약·근무 중)."""
    items: list[Item] = []
    age = age_on(facts.birth, facts.on)
    minor = P()["minor"]

    # 근로계약서 서면 작성과 교부
    wt = P()["written_terms"]
    if stage == "seek":
        items.append(Item(wt["law"], WARN, "일을 시작할 때 근로계약서를 서면으로 받아야 해요. 써 주는지 지원할 때 확인하세요.",
                          needed=["계약서 작성 여부"]))
    else:
        if facts.contract_written is False:
            items.append(Item(wt["law"], BAD, "근로계약서를 쓰지 않았어요. 근로조건은 서면으로 명시해 교부해야 해요.",
                              basis=["근로계약서 작성: 안 썼어요"]))
        elif facts.contract_written is True and facts.copy_received is False:
            items.append(Item(wt["law"], BAD, "계약서를 썼지만 사본을 받지 못했어요. 서면으로 교부해야 해요.",
                              basis=["계약서 사본: 못 받았어요"]))
        elif facts.contract_written is None:
            items.append(Item(wt["law"], WARN, "근로계약서를 썼는지 알려 주세요.", needed=["계약서 작성 여부"]))
        if facts.contract_fields:
            missing = [k for k in wt["items"] if not str(facts.contract_fields.get(k, "")).strip()]
            if missing:  # 빈 칸 자체가 근거 (정보 부족이 아님)
                items.append(Item(wt["law"], BAD, f"계약서에 {', '.join(missing)} 항목이 적혀 있지 않아요.",
                                  basis=[f"계약서 {k} 칸 비어 있음" for k in missing]))
            else:
                items.append(Item(wt["law"], OK, "계약서에 필수 항목이 모두 적혀 있어요.", basis=["확인한 계약서 내용"]))

    # 최저임금과 수습 감액
    mw = _min_wage(facts.on.year)
    law_mw = P()["min_wage"]["law"]
    if facts.wage is None:
        items.append(Item(law_mw, WARN, "시급 정보가 없어 최저임금과 비교하지 못했어요.", needed=["시급"]))
    elif mw is None:
        items.append(Item(law_mw, WARN, f"{facts.on.year}년 최저임금이 법 기준표에 없어요.", needed=["최저임금 기준값"]))
    else:
        pb = P()["probation"]
        basis = [f"시급 {facts.wage:,}원", f"{facts.on.year}년 최저임금 {mw:,}원 ({params.min_wage(facts.on.year)[1]})"]
        if facts.wage >= mw:
            items.append(Item(law_mw, OK, "시급이 최저임금 이상이에요.", basis=basis))
        elif facts.wage < mw * pb["rate"]:
            items.append(Item(law_mw, BAD, "시급이 최저임금에 못 미쳐요. 수습 감액을 적용해도 낮아요.", basis=basis))
        elif facts.probation != "yes":
            items.append(Item(law_mw, BAD, "시급이 최저임금에 못 미쳐요.", basis=basis + ["수습 아님 또는 모름"]))
        else:
            needed, reasons = [], []
            if facts.start_date and facts.end_date and not facts.no_end:
                if (facts.end_date - facts.start_date).days < pb["min_contract_days"]:
                    reasons.append("계약 기간이 1년 미만")
            elif not facts.no_end:
                needed.append("계약 기간")
            if facts.start_date and facts.probation_months:
                if facts.probation_months > pb["max_months"]:
                    reasons.append(f"수습 {facts.probation_months}개월 중 3개월을 넘는 기간")
            needed.append("업무가 단순노무에 해당하는지 (AI 판단 연결 전)")
            if reasons:
                items.append(Item(pb["law"], BAD, "수습 감액 조건을 갖추지 못한 것으로 보여요: " + ", ".join(reasons) + ".",
                                  basis=basis + reasons))
            else:
                items.append(Item(pb["law"], WARN, "수습 감액은 1년 이상 계약, 수습 3개월 이내, 단순노무가 아닐 때만 가능해요.",
                                  basis=basis, needed=needed, ai_pending=True))

    # 쉬는 시간
    br = P()["break"]
    for day, slot in facts.schedule.items():
        wm = sch.span_min(slot)
        b = sch.break_min(slot)
        need = max([r for t, r in br["rules"] if wm - (b or 0) >= t] or [0])
        if need == 0:
            continue
        if b is None:
            items.append(Item(br["law"], WARN, f"{day}요일 쉬는 시간을 몰라 확인하지 못했어요.", needed=[f"{day}요일 쉬는 시간"]))
        elif b < need:
            items.append(Item(br["law"], BAD, f"{day}요일 근무에 필요한 쉬는 시간({need}분)보다 짧아요.",
                              basis=[f"{day}요일 {slot['start']}~{slot['end']}, 쉬는 시간 {b}분"]))

    # 만 18세 미만 보호
    if age < minor["age"]:
        wk = sch.weekly_min(facts.schedule)
        long_days = [d for d, s in facts.schedule.items() if sch.work_min(s) > minor["daily_limit_min"]]
        over_days = [d for d, s in facts.schedule.items() if sch.work_min(s) > minor["daily_limit_min"] + minor["daily_ext_min"]]
        if over_days or wk > minor["weekly_limit_min"] + minor["weekly_ext_min"]:
            items.append(Item(minor["law_hours"], BAD, "만 18세 미만의 근로시간 한도를 넘어요.",
                              basis=[f"주 {wk // 60}시간 {wk % 60}분", *[f"{d}요일" for d in over_days]]))
        elif long_days or wk > minor["weekly_limit_min"]:
            items.append(Item(minor["law_hours"], WARN, "하루 7시간, 주 35시간을 넘어요. 당사자 합의가 있으면 하루 1시간, 주 5시간까지 늘릴 수 있어요.",
                              basis=[f"주 {wk // 60}시간 {wk % 60}분"], needed=["연장 합의 여부"]))
        night_days = [d for d, s in facts.schedule.items() if sch.slot_has_night(s, minor["night_start"], minor["night_end"])]
        if night_days:
            items.append(Item(minor["law_night"], BAD, "만 18세 미만은 밤 10시부터 오전 6시 사이 근무에 본인 동의와 고용노동부 인가가 필요해요. "
                              "인가를 받았는지 사업장에 확인해 보세요.",
                              basis=[f"{d}요일 {facts.schedule[d]['start']}~{facts.schedule[d]['end']}" for d in night_days]))
        if stage == "contract":
            if facts.consent == "안 냈어요":
                items.append(Item(minor["law_docs"], BAD, "만 18세 미만은 보호자 동의서와 가족관계증명서를 사업장에 갖춰야 해요.",
                                  basis=["서류 제출: 안 냈어요"]))
            elif facts.consent != "냈어요":
                items.append(Item(minor["law_docs"], WARN, "보호자 동의서와 가족관계증명서를 냈는지 확인해 주세요.", needed=["서류 제출 여부"]))
    wp = P()["work_permit"]
    if age < wp["age"]:
        items.append(Item(wp["law"], WARN, "만 15세 미만은 취직인허증이 있어야 일할 수 있어요.", needed=["취직인허증 여부"]))

    # 청소년 고용 금지 업소
    yp = P()["youth_protection"]
    if is_youth_protection(facts.birth, facts.on, yp["age"], yp["year_rule"]):
        items.append(Item(yp["law"], WARN, "청소년 고용이 금지된 업소인지 업종과 업무를 법 조항과 대조해 확인해야 해요.",
                          basis=[f"업종: {facts.industry or '미입력'}", f"하는 일: {facts.work_desc or '미입력'}"],
                          needed=["업소 해당 여부 (AI 판단 연결 전)"], ai_pending=True))

    # 사업자 정보: 번호 모양만 코드로 확인하고, 실제 등록 상태는 국세청 조회(연결 전)가 필요
    if facts.biz_no is not None:
        if not facts.biz_no:
            items.append(Item("사업자 정보", WARN, "사업자등록번호가 없어 사업자 상태를 확인하지 못했어요.",
                              needed=["사업자등록번호"]))
        elif not bizno.is_valid(facts.biz_no):
            items.append(Item("사업자 정보", WARN, "사업자등록번호의 검증 번호가 맞지 않아요. 번호를 다시 확인해 주세요.",
                              basis=[f"사업자등록번호 {facts.biz_no}"], needed=["올바른 사업자등록번호"]))
        else:
            items.append(Item("사업자 정보", WARN, "번호 모양은 맞지만, 실제로 등록된 사업자인지는 국세청 조회가 필요해요.",
                              basis=[f"사업자등록번호 {facts.biz_no}"], needed=["국세청 사업자 상태 조회 (API 연결 전)"]))

    # 가산수당 적용 여부
    pr = P()["premium"]
    if facts.size == "unknown":
        items.append(Item(pr["law"], WARN, "사업장 인원을 몰라 연장·야간·휴일 가산수당 적용 여부를 판단하지 않았어요.", needed=["사업장 인원"]))

    return verify(items, facts)


def _dates(days: list[str], limit: int = 5) -> list[str]:
    return days[:limit] + ([f"외 {len(days) - limit}건"] if len(days) > limit else [])


def _need_break(work: int, rules: list) -> int:
    return max([r for t, r in rules if work >= t] or [0])


def judge_records(birth: date, records: list, schedule: dict, focus: date | None = None) -> list[Item]:
    """실제 출퇴근 기록으로 판단. focus가 있으면 그날과 그날이 속한 주만 본다 (퇴근 직후 점검)."""
    minor, br = P()["minor"], P()["break"]
    facts = rec.day_facts(records, schedule, birth, minor["night_start"], minor["night_end"])
    if focus:
        week_start = focus - timedelta(days=focus.weekday())
        facts = [f for f in facts if week_start <= f.day < week_start + timedelta(days=7)]
    daily = [f for f in facts if focus is None or f.day == focus]
    items: list[Item] = []

    # 쉬는 시간: 기록된 근무 길이에 필요한 쉬는 시간과 계약상 쉬는 시간 비교
    short, unknown = [], []
    for f in daily:
        need = _need_break(f.span_min - (f.break_min or 0), br["rules"])
        if need == 0:
            continue
        if f.break_min is None:
            unknown.append(f.when())
        elif f.break_min < need:
            short.append(f"{f.when()} (필요 {need}분, 계약상 {f.break_min}분)")
    if short:
        items.append(Item(br["law"], BAD, "실제 근무 기록을 보면 근무 길이에 필요한 쉬는 시간보다 계약상 쉬는 시간이 짧아요.",
                          basis=_dates(short), source="records"))
    if unknown:
        items.append(Item(br["law"], WARN, "쉬는 시간을 몰라 실제 근무한 날의 쉬는 시간을 확인하지 못했어요.",
                          basis=_dates(unknown), needed=["그날 실제로 쉰 시간"], source="records"))

    # 만 18세 미만: 그날의 만 나이로 판단
    minor_daily = [f for f in daily if f.age < minor["age"]]
    night = [f.when() for f in minor_daily if f.night_min > 0]
    if night:
        items.append(Item(minor["law_night"], BAD, "만 18세 미만인 날 밤 10시부터 오전 6시 사이에 실제로 일한 기록이 있어요. "
                          "본인 동의와 고용노동부 인가가 있어야 해요. 인가를 받았는지 사업장에 확인해 보세요.",
                          basis=_dates(night), source="records"))
    over, long = [], []
    for d, fs in rec.by_day(minor_daily).items():
        total = sum(f.work_min for f in fs)
        label = f"{d.month}월 {d.day}일 {total // 60}시간 {total % 60}분"
        if total > minor["daily_limit_min"] + minor["daily_ext_min"]:
            over.append(label)
        elif total > minor["daily_limit_min"]:
            long.append(label)
    for wk, fs in rec.by_week([f for f in facts if f.age < minor["age"]]).items():
        total = sum(f.work_min for f in fs)
        label = f"{wk.month}월 {wk.day}일 주 {total // 60}시간 {total % 60}분"
        if total > minor["weekly_limit_min"] + minor["weekly_ext_min"]:
            over.append(label)
        elif total > minor["weekly_limit_min"]:
            long.append(label)
    if over:
        items.append(Item(minor["law_hours"], BAD, "실제 근무 기록이 만 18세 미만의 근로시간 한도(연장 합의를 해도 하루 8시간, 주 40시간)를 넘어요.",
                          basis=_dates(over), source="records"))
    elif long:
        items.append(Item(minor["law_hours"], WARN, "실제 근무 기록이 하루 7시간 또는 주 35시간을 넘어요. 연장 합의가 있었는지 확인해야 해요.",
                          basis=_dates(long), needed=["연장 합의 여부"], source="records"))
    return verify(items, None)


def await_ai(items: list[dict]) -> list[dict]:
    """AI가 판단하기 전: 규칙 결과는 rule_status로 숨기고, 정보가 부족한 항목만 확인 필요로 둔다."""
    for it in items:
        it.setdefault("rule_status", it["status"])
        user_info_missing = [n for n in it.get("needed", []) if "AI" not in n]
        if user_info_missing and not it.get("ai_pending"):
            it["status"] = WARN  # 판단에 필요한 정보가 없으면 추측하지 않고 확인 필요
        else:
            it["status"] = PENDING
    return items


def cross_check(items: list[dict]) -> list[dict]:
    """검증 장치: AI 판단을 코드 계산과 필요한 정보 여부로 다시 확인한다."""
    for it in items:
        rule = it.get("rule_status")
        if it["status"] == OK and rule == BAD:
            it["status"] = WARN
            it.setdefault("needed", []).append("AI 판단과 코드 계산 결과가 달라요")
        if it["status"] in (OK, BAD) and [n for n in it.get("needed", []) if "AI" not in n]:
            it["status"] = WARN
        if it["status"] not in (OK, WARN, BAD):
            it["status"] = PENDING
    return items


def questions(facts: Facts, items: list[Item]) -> list[str]:
    """지원 전 물어볼 질문."""
    qs = ["근로계약서를 쓰고 사본을 받을 수 있나요"]
    if facts.wage is None:
        qs.append("시급은 얼마인가요")
    if facts.probation == "yes":
        qs.append("수습 기간은 얼마나 되고, 그동안 시급은 얼마인가요")
    elif facts.probation == "unknown":
        qs.append("수습 기간이 있나요")
    if not facts.schedule:
        qs.append("일하는 요일과 시간은 어떻게 되나요")
    qs.append("쉬는 시간은 언제, 얼마나 있나요")
    qs.append("임금은 매달 며칠에 어떤 방법으로 주나요")
    if age_on(facts.birth, facts.on) < P()["minor"]["age"]:
        qs.append("보호자 동의서와 가족관계증명서를 언제까지 내면 되나요")
    return qs


def to_json(items: list[Item]) -> list[dict]:
    return [asdict(i) for i in items]
