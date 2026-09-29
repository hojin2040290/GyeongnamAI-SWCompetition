"""법제처 국가법령정보 OPEN API로 법 기준표와 참고 자료를 만든다.

사용법: .env에 LAW_OC(신청할 때 정한 인증키)를 넣고
    python -m app.law.fetch
- 법 기준표: 근로 관련 법령의 현행(시행일 기준) 조문과 별표 -> LawArticle
- 최저임금 고시: 연도별 고용노동부 고시의 이름과 번호 -> LawDoc (금액은 law_params.json, 고시는 근거로만)
- 참고 자료: 점검 주제별 판례, 법제처 해석례, 고용노동부 해석, 노동위원회 결정문 -> LawDoc
- 매일 자동 점검은 refresh_if_changed()로 현행 판이 바뀐 법령만 다시 받는다.
법제처에 등록한 IP에서만 호출된다 (다른 곳에서는 '사용자 정보 검증에 실패' 오류).
"""
import json
import sys

import httpx
from sqlmodel import Session, delete, select

from app.calc import params
from app.calc.timeutil import now_kst
from app.config import LAW_OC
from app.db import engine, init_db
from app.law import parse as lp
from app.law.topics import TOPICS
from app.models import LawArticle, LawDoc, LawSource

BASE = "https://www.law.go.kr/DRF"

LAWS = ["근로기준법", "근로기준법 시행령", "최저임금법", "최저임금법 시행령", "청소년 보호법", "청소년 보호법 시행령",
        "정보통신망 이용촉진 및 정보보호 등에 관한 법률"]

PER_QUERY = 3  # 검색어마다 저장할 자료 수


def get(client: httpx.Client, path: str, **params) -> str:
    r = client.get(f"{BASE}/{path}", params={"OC": LAW_OC, "type": "XML", **params})
    r.raise_for_status()
    return r.text


# ---------- 법 기준표 ----------
def current_row(client: httpx.Client, name: str) -> dict | None:
    return lp.current_law(lp.parse_law_list(get(client, "lawSearch.do", target="eflaw", query=name)), name)


def store_law(s: Session, client: httpx.Client, row: dict) -> int:
    """한 법령의 조문과 별표를 새로 저장한다 (예전 판은 지움)."""
    body = lp.parse_law_body(get(client, "lawService.do", target="eflaw", ID=row["law_id"]))
    name = row["name"]
    s.exec(delete(LawArticle).where(LawArticle.law_name == name))
    for a in body["articles"] + body["tables"]:
        s.add(LawArticle(law_name=name, article_no=a["no"], title=a["title"], text=a["text"], fetched_at=now_kst()))
    src = s.exec(select(LawSource).where(LawSource.law_name == name)).first() or LawSource(
        law_name=name, law_id=row["law_id"], mst="", fetched_at=now_kst())
    src.law_id, src.mst, src.fetched_at = row["law_id"], row["mst"], now_kst()
    src.enforce_date, src.promul_no = body["enforce_date"] or row["enforce_date"], body["promul_no"] or row["promul_no"]
    s.add(src)
    s.commit()
    return len(body["articles"])


def build_law_table(s: Session, client: httpx.Client, names: list[str] = LAWS) -> dict[str, str]:
    result = {}
    for name in names:
        row = current_row(client, name)
        if not row:
            result[name] = "현행 법령을 찾지 못했어요"
            continue
        result[name] = f"조문 {store_law(s, client, row)}개 저장 (시행일 {row['enforce_date']})"
    return result


def refresh_if_changed(s: Session, client: httpx.Client, names: list[str] = LAWS) -> list[str]:
    """현행 판(법령일련번호)이 저장한 판과 다른 법령만 다시 받는다. 바뀐 법령 이름을 돌려준다."""
    changed = []
    for name in names:
        row = current_row(client, name)
        src = s.exec(select(LawSource).where(LawSource.law_name == name)).first()
        if row and (not src or src.mst != row["mst"]):
            store_law(s, client, row)
            changed.append(name)
    return changed


# ---------- 최저임금 고시 (근거로만) ----------
NOTICE_TOPIC = "최저임금 고시"


def notice_citation(row: dict) -> str:
    return f"{row['name']} (고용노동부 고시 제{row['number']}호)" if row.get("number") else row["name"]


def fetch_min_wage_notices(s: Session, client: httpx.Client) -> list[str]:
    """연도별 최저임금 고시의 이름과 번호를 근거로 저장한다. law_params.json에 그 해 값이 없으면 알려 준다."""
    rows = lp.pick_min_wage_notices(lp.parse_admrul_list(get(client, "lawSearch.do", target="admrul", query="최저임금")))
    s.exec(delete(LawDoc).where(LawDoc.topic == NOTICE_TOPIC))
    lines = []
    for row in sorted(rows, key=lambda r: r["year"]):
        s.add(LawDoc(kind="admrul", doc_id=row["id"], topic=NOTICE_TOPIC, title=row["name"], number=row["number"],
                     date=row["enforce_date"], summary=notice_citation(row), fetched_at=now_kst()))
        known = params.P()["min_wage"]["by_year"].get(str(row["year"]))
        if known:
            lines.append(f"{row['year']}년 {known:,}원 (law_params.json), 근거 고시: {notice_citation(row)}")
        else:
            lines.append(f"{row['year']}년 고시가 있는데 law_params.json에 {row['year']}년 값이 없어요. "
                         f"고시 원문(첨부파일)을 확인해 data/law_params.json의 min_wage.by_year에 채워 주세요.")
    s.commit()
    load_notices(s)
    return lines


def load_notices(s: Session) -> None:
    """저장한 고시를 판단 근거로 쓰도록 올린다 (서버 시작과 갱신 때)."""
    docs = s.exec(select(LawDoc).where(LawDoc.topic == NOTICE_TOPIC)).all()
    params.set_notices({int(d.date[:4]): d.summary for d in docs if d.date[:4].isdigit()})


# ---------- 참고 자료 ----------
def fetch_refs(s: Session, client: httpx.Client) -> dict[str, int]:
    counts = {}
    for topic, _laws, queries in TOPICS:
        s.exec(delete(LawDoc).where(LawDoc.topic == topic))
        n = 0
        for kind, query in queries.items():
            try:
                rows = lp.parse_doc_list(kind, get(client, "lawSearch.do", target=kind, query=query))[:PER_QUERY]
            except lp.LawAPIError:
                continue  # 신청하지 않은 자료 종류는 건너뜀
            for row in rows:
                body = lp.parse_doc_body(kind, get(client, "lawService.do", target=kind, ID=row["id"]))
                s.add(LawDoc(kind=kind, doc_id=row["id"], topic=topic, title=row["title"], number=row["number"],
                             date=row["date"], summary=body["summary"],
                             fields_json=json.dumps(body["fields"], ensure_ascii=False), fetched_at=now_kst()))
                n += 1
        counts[topic] = n
        s.commit()
    return counts


def main() -> int:
    if not LAW_OC:
        print("LAW_OC가 없어요. .env에 LAW_OC=인증키 를 넣어 주세요. (python -m app.law.probe 로 원인 확인)")
        return 1
    init_db()
    with httpx.Client(timeout=30, follow_redirects=True) as client, Session(engine) as s:
        try:
            print("[법 기준표]")
            for name, msg in build_law_table(s, client).items():
                print(f"  {name}: {msg}")
            print("[최저임금 고시 (금액은 law_params.json, 고시는 근거)]")
            for line in fetch_min_wage_notices(s, client) or ["고용노동부 최저임금 고시를 찾지 못했어요"]:
                print(f"  {line}")
            print("[참고 자료: 판례, 해석례, 결정문]")
            for topic, n in fetch_refs(s, client).items():
                print(f"  {topic}: {n}건")
        except lp.LawAPIError as exc:
            print(f"법제처 오류: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
