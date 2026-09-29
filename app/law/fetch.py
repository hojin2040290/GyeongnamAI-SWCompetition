"""법제처 국가법령정보 API로 조문을 불러와 법 기준표(LawArticle)에 저장한다.

사용법: .env에 LAW_OC(법제처 오픈API 신청 시 등록한 이메일 아이디)를 넣고
    python -m app.law.fetch
응답 형식과 요청 변수는 국가법령정보 공동활용 OPEN API 가이드
(https://open.law.go.kr/LSO/openApi/guideList.do)에서 확인해 맞춘다.
"""
import sys
import xml.etree.ElementTree as ET

import httpx
from sqlmodel import Session, delete

from app.calc.timeutil import now_kst
from app.config import LAW_OC
from app.db import engine, init_db
from app.models import LawArticle

LAWS = ["근로기준법", "최저임금법", "청소년 보호법", "정보통신망 이용촉진 및 정보보호 등에 관한 법률"]
SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"


def find_law_id(client: httpx.Client, name: str) -> str | None:
    r = client.get(SEARCH_URL, params={"OC": LAW_OC, "target": "law", "type": "XML", "query": name})
    root = ET.fromstring(r.content)
    for law in root.iter("law"):
        if (law.findtext("법령명한글") or "").strip() == name:
            return law.findtext("법령ID")
    return None


def fetch_articles(client: httpx.Client, law_id: str) -> list[tuple[str, str, str]]:
    r = client.get(SERVICE_URL, params={"OC": LAW_OC, "target": "law", "type": "XML", "ID": law_id})
    root = ET.fromstring(r.content)
    out = []
    for unit in root.iter("조문단위"):
        no = (unit.findtext("조문번호") or "").strip()
        title = (unit.findtext("조문제목") or "").strip()
        text = "\n".join(t.strip() for t in unit.itertext() if t.strip())
        if no:
            out.append((no, title, text))
    return out


def main() -> int:
    if not LAW_OC:
        print("LAW_OC가 없어요. .env에 법제처 오픈API OC 값을 넣어 주세요.")
        return 1
    init_db()
    with httpx.Client(timeout=30) as client, Session(engine) as s:
        for name in LAWS:
            law_id = find_law_id(client, name)
            if not law_id:
                print(f"{name}: 법령을 찾지 못했어요")
                continue
            arts = fetch_articles(client, law_id)
            s.exec(delete(LawArticle).where(LawArticle.law_name == name))
            for no, title, text in arts:
                s.add(LawArticle(law_name=name, article_no=no, title=title, text=text, fetched_at=now_kst()))
            s.commit()
            print(f"{name}: 조문 {len(arts)}개 저장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
