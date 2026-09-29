"""판단 결과에 법 기준표(LawArticle)의 조문 원문을 붙인다.

조문 원문은 법제처 API로 불러와 DB에 저장한 것만 쓴다 (app/law/fetch.py).
아직 불러오지 않은 조문은 '법 기준표 미구축'으로 표시하고, 기억으로 채우지 않는다.
"""
import re

from sqlmodel import Session, select

from app.models import LawArticle

_LABEL = re.compile(r"^(?P<law>.+?)\s*제(?P<no>\d+)조(?:의(?P<branch>\d+))?")


def parse_label(label: str) -> tuple[str, str] | None:
    """'근로기준법 제70조', '최저임금법 제5조 제2항', '근로기준법 제76조의2' -> (법 이름, 조문 번호)."""
    m = _LABEL.match(label.strip())
    if not m:
        return None
    no = m["no"] + (f"의{m['branch']}" if m["branch"] else "")
    return m["law"].strip(), no


def find_article(session: Session, label: str) -> LawArticle | None:
    parsed = parse_label(label)
    if not parsed:
        return None
    law, no = parsed
    return session.exec(select(LawArticle).where(LawArticle.law_name == law, LawArticle.article_no == no)
                        .order_by(LawArticle.id.desc())).first()


def article_info(session: Session, label: str) -> dict:
    """화면과 상담 자료에 쓰는 조문 정보. 없으면 built=False."""
    if not parse_label(label):
        return {"built": False, "na": True, "label": label, "text": "", "note": "법 조문 항목 아님"}
    art = find_article(session, label)
    if not art:
        return {"built": False, "label": label, "text": "", "note": "법 기준표 미구축"}
    return {"built": True, "label": label, "title": art.title, "text": art.text,
            "fetched_at": art.fetched_at.isoformat()}


def attach_articles(session: Session, items: list[dict]) -> list[dict]:
    """판단 결과마다 조문 원문을 붙인다. 같은 조문은 한 번만 찾는다."""
    cache: dict[str, dict] = {}
    for it in items:
        label = it.get("law", "")
        if label not in cache:
            cache[label] = article_info(session, label)
        it["article"] = cache[label]
    return items


def table_status(session: Session) -> dict:
    """법 기준표 구축 상태 (법 이름별 조문 수)."""
    rows = session.exec(select(LawArticle)).all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.law_name] = counts.get(r.law_name, 0) + 1
    return {"built": bool(rows), "laws": counts}
