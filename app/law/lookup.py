"""판단 결과에 법 기준표(LawArticle)의 조문 원문을 붙인다.

조문 원문은 법제처 API로 불러와 DB에 저장한 것만 쓴다 (app/law/fetch.py).
아직 불러오지 않은 조문은 '법 기준표 미구축'으로 표시하고, 기억으로 채우지 않는다.
"""
import re

from sqlmodel import Session, select

from app.law.parse import DOC_LABEL
from app.law.topics import TOPICS
from app.calc import params
from app.models import LawArticle, LawDoc, LawSource

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


def _param_laws(v) -> set[str]:
    """law_params.json에 출처와 함께 적어 둔 조항 이름들."""
    out: set[str] = set()
    if isinstance(v, dict):
        for k, x in v.items():
            if k.startswith("law") and isinstance(x, str):
                out.add(x)
            elif k == "laws" and isinstance(x, list):
                out.update(i for i in x if isinstance(i, str))
            else:
                out |= _param_laws(x)
    elif isinstance(v, list):
        for x in v:
            out |= _param_laws(x)
    return out


def known_law(session: Session, label: str) -> bool:
    """AI가 근거로 댄 조항을 받아들일지: 법 기준표에 있으면 받는다.
    그 법의 기준표가 아직 없으면 law_params.json에 출처와 함께 적힌 조항만 받는다."""
    parsed = parse_label(label or "")
    if not parsed:
        return False
    if find_article(session, label):
        return True
    built = session.exec(select(LawArticle).where(LawArticle.law_name == parsed[0])).first()
    return not built and any(parse_label(x) == parsed for x in _param_laws(params.P()))


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
    sources = {r.law_name: r.enforce_date for r in session.exec(select(LawSource)).all()}
    notices = session.exec(select(LawDoc).where(LawDoc.kind == "admrul").order_by(LawDoc.date.desc())).all()
    # law_params.json에 금액이 있는 가장 최근 해 (다음 해 고시만 있고 금액이 아직 없는 경우는 건너뜀)
    notice = next((d for d in notices if d.date[:4].isdigit() and params.min_wage(int(d.date[:4]))[0]), None)
    year = int(notice.date[:4]) if notice else None
    value = params.min_wage(year)[0] if year else None
    missing = [int(d.date[:4]) for d in notices if d.date[:4].isdigit() and not params.min_wage(int(d.date[:4]))[0]]
    return {"built": bool(rows), "laws": counts, "enforce_dates": sources,
            "refs": len(session.exec(select(LawDoc).where(LawDoc.kind != "admrul")).all()),
            "min_wage": {"year": year, "value": value, "source": notice.summary} if notice else None,
            "min_wage_missing_years": missing}


def topics_for(label: str) -> list[str]:
    """조항 이름(예: 근로기준법 제104조 제2항)에 해당하는 점검 주제."""
    parsed = parse_label(label)
    if not parsed:
        return []
    return [topic for topic, laws, _ in TOPICS if any(parse_label(x) == parsed for x in laws)]


def refs_for(session: Session, label: str, limit: int = 4) -> list[dict]:
    """미리 받아 둔 판례, 해석례, 결정문 중 이 조항 주제에 맞는 것 (종류별로 골고루)."""
    topics = topics_for(label)
    if not topics:
        return []
    rows = session.exec(select(LawDoc).where(LawDoc.topic.in_(topics)).order_by(LawDoc.id)).all()
    picked, seen_kinds = [], set()
    for r in rows:  # 먼저 종류마다 하나씩, 남으면 순서대로
        if r.kind not in seen_kinds:
            picked.append(r)
            seen_kinds.add(r.kind)
    picked += [r for r in rows if r not in picked]
    return [{"kind": DOC_LABEL.get(r.kind, r.kind), "title": r.title, "number": r.number, "date": r.date,
             "summary": r.summary} for r in picked[:limit]]


def attach_refs(session: Session, items: list[dict]) -> list[dict]:
    cache: dict[str, list[dict]] = {}
    for it in items:
        label = it.get("law", "")
        if label not in cache:
            cache[label] = refs_for(session, label)
        it["refs"] = cache[label]
    return items
