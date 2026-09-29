"""법제처 연결 확인 스크립트의 응답 요약 테스트 (가상 XML)."""
from app.law.probe import find_law_id, summarize

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?><LawSearch><totalCnt>1</totalCnt>
<law id="1"><법령ID>999999</법령ID><법령명한글>근로기준법</법령명한글><시행일자>20260101</시행일자></law></LawSearch>"""


def test_summarize_and_find_id():
    s = summarize(SAMPLE)
    assert "최상위 <LawSearch>" in s and "<법령명한글> 근로기준법" in s
    assert find_law_id(SAMPLE, "근로기준법") == "999999"
    assert find_law_id(SAMPLE, "최저임금법") is None
    assert summarize("<html>오류</html").startswith("XML 아님")
