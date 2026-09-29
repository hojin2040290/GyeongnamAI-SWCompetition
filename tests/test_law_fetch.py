"""법제처 연결 테스트. 실제 응답(python -m app.law.probe로 확인)과 같은 구조의 가짜 응답을 쓴다. 내용은 테스트용 문구."""
from datetime import datetime

import httpx
import pytest
from sqlmodel import Session, select

from app.calc import params
from app.db import engine, init_db
from app.law import fetch, lookup
from app.law import parse as lp
from app.models import LawArticle, LawDoc, LawSource

STATE = {"mst": "286000"}


def law_list(name: str) -> str:
    rows = [("286771", "시행예정", "20270610"), (STATE["mst"], "현행", "20260820")]
    items = "".join(f"<law id='{i}'><법령일련번호>{m}</법령일련번호><현행연혁코드>{st}</현행연혁코드><법령명한글>{name}</법령명한글>"
                    f"<법령ID>00{i}{len(name)}</법령ID><공포번호>21373</공포번호><시행일자>{d}</시행일자></law>"
                    for i, (m, st, d) in enumerate(rows, 1))
    other = "<law id='9'><법령일련번호>1</법령일련번호><현행연혁코드>현행</현행연혁코드><법령명한글>다른법</법령명한글><법령ID>9</법령ID></law>"
    return f"<LawSearch>{items}{other}</LawSearch>"


LAW_BODY = """<법령><기본정보><법령ID>001872</법령ID><법령명_한글>근로기준법</법령명_한글><공포번호>21373</공포번호>
<시행일자>20260820</시행일자></기본정보><조문>
<조문단위><조문번호>5</조문번호><조문여부>전문</조문여부><조문내용>제5장 여성과 소년</조문내용></조문단위>
<조문단위><조문번호>70</조문번호><조문여부>조문</조문여부><조문제목>야간근로와 휴일근로의 제한</조문제목>
<조문시행일자>20260820</조문시행일자><조문내용>제70조(야간근로와 휴일근로의 제한)</조문내용>
<항><항번호>①</항번호><항내용>① 테스트용 항 내용 하나</항내용></항>
<항><항번호>②</항번호><항내용>② 테스트용 항 내용 둘</항내용><호><호번호>1.</호번호><호내용>1. 테스트용 호</호내용></호></항></조문단위>
<조문단위><조문번호>76</조문번호><조문가지번호>2</조문가지번호><조문여부>조문</조문여부><조문제목>직장 내 괴롭힘의 금지</조문제목>
<조문내용>제76조의2(직장 내 괴롭힘의 금지) 테스트용 본문</조문내용></조문단위>
</조문><별표><별표단위><별표번호>0001</별표번호><별표가지번호>00</별표가지번호><별표구분>별표</별표구분>
<별표제목>테스트 별표</별표제목><별표내용>별표 내용&lt;br/&gt;둘째 줄</별표내용></별표단위></별표></법령>"""

def admrul(i, name, ministry, status, date, number=""):
    return (f"<admrul id='{i}'><행정규칙일련번호>{i}</행정규칙일련번호><행정규칙명>{name}</행정규칙명><소관부처명>{ministry}</소관부처명>"
            f"<현행연혁구분>{status}</현행연혁구분><발령번호>{number}</발령번호><시행일자>{date}</시행일자></admrul>")


# 실제 검색 결과(2026년 9월)와 같은 이름들. 번호는 테스트용
ADMRUL_LIST = "<AdmRulSearch>" + "".join([
    admrul(1, "2026년 선원 최저임금 고시", "해양수산부", "현행", "20260101"),
    admrul(2, "2026년 적용 최저임금 고시", "고용노동부", "현행", "20260101", "2025-40"),
    admrul(3, "2027년 적용 최저임금 고시", "고용노동부", "연혁", "20270101", "2026-50"),
    admrul(4, "2027년 적용 최저임금안 고시", "고용노동부", "연혁", "20270101"),
    admrul(5, "최저임금법 제5조에 따른 단순노무직종 근로자 지정 고시", "고용노동부", "현행", "20180320"),
]) + "</AdmRulSearch>"
ADMRUL_BODY = "<AdmRulService><행정규칙기본정보><행정규칙명>테스트</행정규칙명></행정규칙기본정보><조문내용></조문내용></AdmRulService>"

PREC_LIST = "<PrecSearch><prec id='1'><판례일련번호>241229</판례일련번호><사건명>테스트 사건</사건명><사건번호>2021다00000</사건번호><선고일자>2024.07.25</선고일자></prec></PrecSearch>"
PREC_BODY = "<PrecService><판례정보일련번호>241229</판례정보일련번호><판시사항>테스트 판시사항&lt;br/&gt;둘째 줄</판시사항><판결요지>테스트 요지</판결요지></PrecService>"
MOEL_LIST = "<CgmExpc><cgmExpc id='1'><법령해석일련번호>12952</법령해석일련번호><안건명>테스트 해석</안건명><안건번호>근기 0000</안건번호><해석일자>2000.07.27</해석일자></cgmExpc></CgmExpc>"
MOEL_BODY = "<CgmExpcService><질의요지>테스트 질의</질의요지><회답>테스트 회답</회답></CgmExpcService>"
NLRC_LIST = "<Nlrc><nlrc id='1'><결정문일련번호>15265</결정문일련번호><제목>테스트 구제신청</제목><사건번호>2016부해OOO</사건번호><등록일>2016.05.09</등록일></nlrc></Nlrc>"
NLRC_BODY = "<NlrcService><판정사항>테스트 판정</판정사항><판정요지>테스트 판정 요지</판정요지><판정결과>전부인정</판정결과></NlrcService>"
EXPC_LIST = "<Expc><expc id='1'><법령해석례일련번호>333401</법령해석례일련번호><안건명>테스트 안건</안건명><안건번호>21-0000</안건번호><회신일자>2022.04.26</회신일자></expc></Expc>"
EXPC_BODY = "<ExpcService><질의요지>테스트 질의요지</질의요지><회답>테스트 회답</회답></ExpcService>"
ERROR = "<Response><result>사용자 정보 검증에 실패하였습니다.</result><msg>IP주소를 등록해 주세요.</msg></Response>"


def handler(request: httpx.Request) -> httpx.Response:
    q = dict(request.url.params)
    assert q["OC"] is not None and q["type"] == "XML"
    search = request.url.path.endswith("lawSearch.do")
    t = q["target"]
    body = {
        ("eflaw", True): law_list(q.get("query", "")), ("eflaw", False): LAW_BODY,
        ("admrul", True): ADMRUL_LIST, ("admrul", False): ADMRUL_BODY,
        ("prec", True): PREC_LIST, ("prec", False): PREC_BODY,
        ("moelCgmExpc", True): MOEL_LIST, ("moelCgmExpc", False): MOEL_BODY,
        ("nlrc", True): NLRC_LIST, ("nlrc", False): NLRC_BODY,
        ("expc", True): EXPC_LIST, ("expc", False): EXPC_BODY,
    }[(t, search)]
    return httpx.Response(200, text=body, headers={"content-type": "text/xml;charset=UTF-8"})


@pytest.fixture()
def client():
    init_db()
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        yield c
    params.set_notices({})


def test_parse_law_body():
    b = lp.parse_law_body(LAW_BODY)
    nos = [a["no"] for a in b["articles"]]
    assert nos == ["70", "76의2"]  # 장 제목(전문)은 빼고, 가지번호는 76의2
    art = b["articles"][0]
    assert art["text"].splitlines() == ["제70조(야간근로와 휴일근로의 제한)", "① 테스트용 항 내용 하나",
                                        "② 테스트용 항 내용 둘", "1. 테스트용 호"]
    assert "20260820" not in art["text"]  # 관리용 값은 넣지 않음
    assert b["tables"][0] == {"no": "별표1", "title": "테스트 별표", "text": "별표 내용\n둘째 줄"}


def test_current_law_skips_scheduled_version():
    rows = lp.parse_law_list(law_list("근로기준법"))
    assert lp.current_law(rows, "근로기준법")["status"] == "현행"
    assert lp.current_law(rows, "근로기준") is None


def test_min_wage_notice_picks_yearly_moel_notices_only():
    rows = lp.pick_min_wage_notices(lp.parse_admrul_list(ADMRUL_LIST))
    assert [(r["id"], r["year"]) for r in rows] == [("2", 2026), ("3", 2027)]  # 선원, 최저임금안, 단순노무직종 지정 제외


def test_error_response_raises():
    with pytest.raises(lp.LawAPIError, match="검증에 실패"):
        lp.parse_law_list(ERROR)


def test_build_table_min_wage_refs_and_refresh(client):
    with Session(engine) as s:
        res = fetch.build_law_table(s, client, ["근로기준법"])
        assert "조문 2개" in res["근로기준법"]
        info = lookup.article_info(s, "근로기준법 제70조")
        assert info["built"] and "테스트용 호" in info["text"]
        assert lookup.article_info(s, "근로기준법 제76조의2")["built"]

        lines = fetch.fetch_min_wage_notices(s, client)
        assert lines[0] == "2026년 10,320원 (law_params.json), 근거 고시: 2026년 적용 최저임금 고시 (고용노동부 고시 제2025-40호)"
        assert "2027년 고시가 있는데 law_params.json에 2027년 값이 없어요" in lines[1]  # 사람이 채우도록 알림
        # 금액은 law_params.json 그대로, 근거만 고시로
        assert params.min_wage(2026) == (10320, "근거 고시: 2026년 적용 최저임금 고시 (고용노동부 고시 제2025-40호)")
        assert params.min_wage(2027) == (None, "")

        counts = fetch.fetch_refs(s, client)
        assert counts["신고 후 불리한 처우"] == 3  # 노동위원회, 판례, 고용노동부 해석 1건씩
        refs = lookup.refs_for(s, "근로기준법 제104조 제2항")
        assert {r["kind"] for r in refs} == {"노동위원회 결정", "판례", "고용노동부 해석"}
        assert refs[0]["summary"] and "<br" not in refs[0]["summary"]

        assert fetch.refresh_if_changed(s, client, ["근로기준법"]) == []  # 같은 판이면 다시 받지 않음
        STATE["mst"] = "286999"
        assert fetch.refresh_if_changed(s, client, ["근로기준법"]) == ["근로기준법"]
        assert s.exec(select(LawSource).where(LawSource.law_name == "근로기준법")).first().mst == "286999"
        assert len(s.exec(select(LawArticle).where(LawArticle.law_name == "근로기준법")).all()) == 3  # 조문 2 + 별표 1
        STATE["mst"] = "286000"


def test_check_result_has_law_text_and_refs(client):
    """법 기준표를 만든 뒤 점검하면 결과에 조문 원문과 참고 자료가 붙고, 최저임금 근거에 고시 출처가 나온다."""
    from fastapi.testclient import TestClient
    from app.main import app
    with Session(engine) as s:
        fetch.build_law_table(s, client, ["근로기준법"])
        fetch.fetch_min_wage_notices(s, client)
        fetch.fetch_refs(s, client)
    with TestClient(app) as c:
        c.post("/api/auth/register", json={"email": "law@example.com", "password": "test1234", "birth_date": "2010-05-01"})
        jid = c.post("/api/jobs", json={"name": "가상법령점", "wage": 9000, "probation": "no",
                                        "schedule": {"금": {"start": "18:00", "end": "23:00", "brk": "30분"}}}).json()["id"]
        items = c.post(f"/api/jobs/{jid}/check").json()["items"]
        night = [i for i in items if i["law"] == "근로기준법 제70조"][0]
        assert night["article"]["built"] and night["refs"]
        mw = [i for i in items if i["law"] == "최저임금법 제5조"][0]
        assert any("고용노동부 고시 제2025-40호" in b for b in mw["basis"])
        status = c.get("/api/law/status").json()
        assert status["min_wage"]["value"] == 10320 and status["min_wage"]["year"] == 2026 and status["refs"] > 0
        assert status["min_wage_missing_years"] == [2027]
        html = c.get(c.post(f"/api/jobs/{jid}/report").json()["url"]).text
        assert "참고 판례와 해석" in html and "테스트 판시사항" in html
    with Session(engine) as s:  # 다른 테스트에 영향 없게 정리
        for m in (LawArticle, LawDoc, LawSource):  # noqa: B007
            for r in s.exec(select(m)).all():
                s.delete(r)
        s.commit()
    _ = datetime
