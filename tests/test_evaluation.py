"""목표 성능(위반 의심 누락 0건, 급여 계산 100% 일치)을 테스트로도 확인한다."""
import json

from evaluation.run import CASES, run_judge, run_pay


def test_no_missed_violations():
    r = run_judge(json.loads(CASES.read_text(encoding="utf-8"))["judge"])
    assert r["missed"] == 0, r["details"]


def test_pay_matches_hand_calculation():
    r = run_pay(json.loads(CASES.read_text(encoding="utf-8"))["pay"])
    assert r["match"] == r["cases"], r["details"]
