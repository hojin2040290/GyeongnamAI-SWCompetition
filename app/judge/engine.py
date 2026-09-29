"""법 기준표 대조.

지금은 AI를 연결하지 않았으므로, 숫자로 확인할 수 있는 조항은 법 기준값(law_params.json)과
코드 계산으로 판단하고, 업종이나 업무처럼 문장을 해석해야 하는 조항은 'AI 판단 연결 전'으로
확인 필요 처리한다. AI 연결 후에는 LLMJudge가 같은 형식(Item)으로 결과를 돌려주면 된다.
"""
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from app.calc import schedule as sch
from app.calc.age import age_on, is_youth_protection
from app.calc.params import P

OK, WARN, BAD = "ok", "warn", "bad"


@dataclass
class Item:
    law: str
    status: str
    text: str
    basis: list = field(default_factory=list)   # 판단에 쓴 사실
    needed: list = field(default_factory=list)  # 부족해서 확인이 필요한 정보
    ai_pending: bool = False                    # AI 연결 후 판단할 항목


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
    return P()["min_wage"]["by_year"].get(str(year))


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
            if missing:
                items.append(Item(wt["law"], WARN, f"계약서에서 {', '.join(missing)} 항목을 찾지 못했어요.",
                                  basis=["확인한 계약서 내용"], needed=missing))
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
        basis = [f"시급 {facts.wage:,}원", f"{facts.on.year}년 최저임금 {mw:,}원"]
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
            items.append(Item(minor["law_night"], BAD, "만 18세 미만은 밤 10시부터 오전 6시 사이 근무에 본인 동의와 고용노동부 인가가 필요해요.",
                              basis=[f"{d}요일 {facts.schedule[d]['start']}~{facts.schedule[d]['end']}" for d in night_days],
                              needed=["고용노동부 인가 여부"] if stage == "contract" else []))
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

    # 가산수당 적용 여부
    pr = P()["premium"]
    if facts.size == "unknown":
        items.append(Item(pr["law"], WARN, "사업장 인원을 몰라 연장·야간·휴일 가산수당 적용 여부를 판단하지 않았어요.", needed=["사업장 인원"]))

    return verify(items, facts)


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
