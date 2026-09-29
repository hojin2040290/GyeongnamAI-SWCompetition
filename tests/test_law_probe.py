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


def test_error_message_is_shown():
    err = "<Response><result>실패</result><msg>사용자 정보 검증에 실패하였습니다.</msg></Response>"
    s = summarize(err)
    assert "<result> 실패" in s and "<msg> 사용자 정보 검증에 실패하였습니다." in s


def test_outline_and_first_ids():
    from app.law.probe import first_item_ids, outline
    body = "<법령><기본정보><법령명_한글>근로기준법</법령명_한글></기본정보><조문><조문단위><조문번호>70</조문번호></조문단위><조문단위><조문번호>71</조문번호></조문단위></조문></법령>"
    o = outline(body)
    assert "/법령/조문/조문단위 x2" in o and "/법령/조문/조문단위/조문번호 x2 = 70" in o
    lst = "<PrecSearch><totalCnt>1</totalCnt><prec id='1'><판례일련번호>123</판례일련번호><사건명>가상 사건</사건명></prec></PrecSearch>"
    assert first_item_ids(lst) == {"id": "1", "판례일련번호": "123"}
