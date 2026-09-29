"""법제처 OPEN API 응답(XML) 읽기. 네트워크 없이 글자만 다루므로 테스트하기 쉽다.

응답 구조는 python -m app.law.probe로 실제 응답을 확인해 맞췄다 (2026년 9월).
"""
import re
import xml.etree.ElementTree as ET


class LawAPIError(RuntimeError):
    """법제처가 오류 안내(<Response><result><msg>)를 돌려줬을 때."""


def root_of(text: str) -> ET.Element:
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise LawAPIError("법제처 응답이 XML이 아니에요") from exc
    if root.tag == "Response":  # 인증 실패 등
        msg = " ".join(" ".join((el.text or "").split()) for el in root)
        raise LawAPIError(msg or "법제처가 오류를 돌려줬어요")
    return root


def _t(el: ET.Element | None, tag: str) -> str:
    return " ".join((el.findtext(tag) or "").split()) if el is not None else ""


def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


# ---------- 법령 ----------
def parse_law_list(text: str) -> list[dict]:
    """현행법령 목록: 이름, 법령ID, 법령일련번호(MST), 현행 여부, 시행일자."""
    return [{"name": _t(law, "법령명한글"), "law_id": _t(law, "법령ID"), "mst": _t(law, "법령일련번호"),
             "status": _t(law, "현행연혁코드"), "enforce_date": _t(law, "시행일자"), "promul_no": _t(law, "공포번호")}
            for law in root_of(text).iter("law")]


def current_law(rows: list[dict], name: str) -> dict | None:
    """이름이 정확히 같고 지금 시행 중인 판 (시행예정 판은 제외)."""
    return next((r for r in rows if r["name"] == name and r["status"] == "현행"), None)


def article_no(unit: ET.Element) -> str:
    no = _t(unit, "조문번호")
    branch = _t(unit, "조문가지번호")
    return f"{no}의{branch}" if no and branch and branch not in ("0", "00") else no  # 제76조의2 -> 76의2


def article_text(unit: ET.Element) -> str:
    """조문 제목 줄, 항, 호, 목을 차례로 이어 붙인다 (시행일자 같은 관리용 값은 뺀다)."""
    lines = [_t(unit, "조문내용")]
    for hang in unit.findall("항"):
        h = _t(hang, "항내용")
        if h and h not in lines[0]:
            lines.append(h)
        for ho in hang.findall("호"):
            lines.append(_t(ho, "호내용"))
            lines += [_t(mok, "목내용") for mok in ho.findall("목")]
    return "\n".join(x for x in lines if x)


def parse_law_body(text: str) -> dict:
    """현행법령 본문: 기본 정보, 조문(전문 제외), 별표."""
    root = root_of(text)
    info = root.find("기본정보")
    articles = []
    for unit in root.iter("조문단위"):
        if _t(unit, "조문여부") == "전문":  # 장, 절 제목
            continue
        no = article_no(unit)
        if no:
            articles.append({"no": no, "title": _t(unit, "조문제목"), "text": article_text(unit)})
    tables = []
    for b in root.iter("별표단위"):
        num = _t(b, "별표번호").lstrip("0")
        branch = _t(b, "별표가지번호").lstrip("0")
        label = f"{_t(b, '별표구분') or '별표'}{num}" + (f"의{branch}" if branch else "")
        tables.append({"no": label, "title": _t(b, "별표제목"), "text": _clean(b.findtext("별표내용") or "")})
    return {"name": _t(info, "법령명_한글"), "law_id": _t(info, "법령ID"), "enforce_date": _t(info, "시행일자"),
            "promul_no": _t(info, "공포번호"), "articles": articles, "tables": tables}


# ---------- 행정규칙 (최저임금 고시) ----------
def parse_admrul_list(text: str) -> list[dict]:
    return [{"id": _t(a, "행정규칙일련번호"), "name": _t(a, "행정규칙명"), "kind": _t(a, "행정규칙종류"),
             "ministry": _t(a, "소관부처명"), "status": _t(a, "현행연혁구분"), "number": _t(a, "발령번호"),
             "enforce_date": _t(a, "시행일자")} for a in root_of(text).iter("admrul")]


MIN_WAGE_NOTICE = re.compile(r"^(\d{4})년 적용 최저임금 고시$")


def pick_min_wage_notices(rows: list[dict]) -> list[dict]:
    """고용노동부의 'OOOO년 적용 최저임금 고시'만 (선원 고시, 최저임금안 고시, 단순노무직종 지정 고시 등은 제외).
    연도는 이름에서 읽는다. 금액은 고시 첨부파일에만 있어 읽지 않는다 (금액은 law_params.json)."""
    out = []
    for r in rows:
        m = MIN_WAGE_NOTICE.match(r["name"])
        if m and r["ministry"] == "고용노동부":
            out.append({**r, "year": int(m.group(1))})
    return out


# ---------- 판례, 해석례, 결정문 ----------
# kind: (목록 항목 태그, 번호 태그, 제목 태그, 번호 표시 태그, 날짜 태그, 본문에서 요약으로 쓸 태그들)
DOC_KINDS = {
    "prec": ("prec", "판례일련번호", "사건명", "사건번호", "선고일자", ["판시사항", "판결요지", "참조조문"]),
    "expc": ("expc", "법령해석례일련번호", "안건명", "안건번호", "회신일자", ["질의요지", "회답"]),
    "moelCgmExpc": ("cgmExpc", "법령해석일련번호", "안건명", "안건번호", "해석일자", ["질의요지", "회답"]),
    "nlrc": ("nlrc", "결정문일련번호", "제목", "사건번호", "등록일", ["판정사항", "판정요지", "판정결과"]),
}
DOC_LABEL = {"prec": "판례", "expc": "법제처 법령해석", "moelCgmExpc": "고용노동부 해석", "nlrc": "노동위원회 결정"}


def parse_doc_list(kind: str, text: str) -> list[dict]:
    item, id_tag, title_tag, num_tag, date_tag, _ = DOC_KINDS[kind]
    return [{"id": _t(el, id_tag), "title": _t(el, title_tag), "number": _t(el, num_tag), "date": _t(el, date_tag)}
            for el in root_of(text).iter(item) if _t(el, id_tag)]


def parse_doc_body(kind: str, text: str) -> dict:
    """본문에서 요약 칸들(판결요지, 회답, 판정요지 등)만 모은다."""
    root = root_of(text)
    fields = {tag: _clean(root.findtext(tag) or "") for tag in DOC_KINDS[kind][5]}
    summary = next((v for v in fields.values() if v), "")
    return {"fields": {k: v for k, v in fields.items() if v}, "summary": summary[:600]}
