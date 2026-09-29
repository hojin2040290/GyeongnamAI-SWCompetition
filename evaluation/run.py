"""목표 성능 측정: 위반 의심 사례 누락 0건, 급여 계산 결과 100% 일치.

사용법: python -m evaluation.run
정답은 evaluation/cases.json에 사람이 직접 적었다. 판단과 계산은 실제 서비스와 같은 함수를 쓴다.
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

from app.calc.pay import calc_month
from app.judge import engine
from app.models import WorkRecord

CASES = Path(__file__).with_name("cases.json")
FLAGGED = ("warn", "bad")


def _records(pairs: list) -> list[WorkRecord]:
    return [WorkRecord(user_id=0, job_id=0, clock_in=datetime.fromisoformat(a), clock_out=datetime.fromisoformat(b))
            for a, b in pairs]


def _d(v):
    return date.fromisoformat(v) if v else None


def judge_case(c: dict) -> list[engine.Item]:
    j = c["job"]
    facts = engine.Facts(birth=_d(c["birth"]), on=_d(c["on"]), wage=j.get("wage"), probation=j.get("probation", "unknown"),
                         probation_months=j.get("probation_months"), start_date=_d(j.get("start_date")),
                         end_date=_d(j.get("end_date")), no_end=j.get("no_end", False), schedule=j.get("schedule", {}),
                         size=j.get("size", "unknown"), industry=j.get("industry", ""), work_desc=j.get("work_desc", ""),
                         contract_written=j.get("contract_written"), copy_received=j.get("copy_received"),
                         consent=j.get("consent", ""), contract_fields=j.get("contract_fields", {}))
    items = engine.judge(facts, c.get("stage", "contract"))
    return items + engine.judge_records(facts.birth, _records(c.get("records", [])), facts.schedule)


def run_judge(cases: list[dict]) -> dict:
    total = missed = strict_total = strict_ok = false_alarm = absent_total = 0
    rows = []
    for c in cases:
        items = judge_case(c)
        flagged = {i.law: i.status for i in items if i.status in FLAGGED}
        for law in c.get("expect", []):
            total += 1
            hit = law in flagged
            missed += not hit
            if not hit:
                rows.append(f"  누락 {c['id']} {law}: {c['desc']}")
        for law, want in c.get("strict", {}).items():
            strict_total += 1
            got = max((i.status for i in items if i.law == law), key=["ok", "warn", "bad"].index, default="없음")
            strict_ok += got == want
            if got != want:
                rows.append(f"  결과 다름 {c['id']} {law}: 기대 {want}, 결과 {got}")
        for law in c.get("absent", []):
            absent_total += 1
            if law in flagged:
                false_alarm += 1
                rows.append(f"  오탐 {c['id']} {law}: {c['desc']}")
    return {"expected": total, "missed": missed, "strict": f"{strict_ok}/{strict_total}",
            "false_alarm": f"{false_alarm}/{absent_total}", "details": rows}


def run_pay(cases: list[dict]) -> dict:
    match = 0
    rows = []
    for c in cases:
        r = calc_month(_records(c["records"]), c["schedule"], c["wage"], c["size"], _d(c["birth"]), c["month"])
        got = {k: getattr(r, k) for k in c["answer"]}
        if got == c["answer"]:
            match += 1
        else:
            rows.append(f"  불일치 {c['id']} {c['desc']}: 정답 {c['answer']}, 계산 {got}")
    return {"cases": len(cases), "match": match, "rate": round(match / len(cases) * 100, 1) if cases else 0.0,
            "details": rows}


def main() -> int:
    data = json.loads(CASES.read_text(encoding="utf-8"))
    j, p = run_judge(data["judge"]), run_pay(data["pay"])
    print("※ 판단 측정은 AI 판단을 확인하는 검증 장치(코드 규칙) 기준이에요. AI 연결 후에는 AI 결과로 다시 측정해요.")
    print(f"[위반 의심 사례 누락] 잡아야 할 조항 {j['expected']}건 중 누락 {j['missed']}건 (목표 0건)")
    print(f"  결과까지 정확히 맞은 항목 {j['strict']}, 정상 사례 오탐 {j['false_alarm']}")
    print(*j["details"], sep="\n") if j["details"] else None
    print(f"[급여 계산 일치율] {p['match']}/{p['cases']} = {p['rate']}% (목표 100%)")
    print(*p["details"], sep="\n") if p["details"] else None
    ok = j["missed"] == 0 and p["match"] == p["cases"]
    print("목표 달성" if ok else "목표 미달")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
