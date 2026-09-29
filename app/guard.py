"""신고 후 보복 대응: 보복 금지 안내 문구, 공개 게시물 검색, 증거 보존."""
import html
import re

import httpx
from sqlmodel import Session, select

from app.calc.params import P
from app.calc.timeutil import now_kst
from app.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
from app.models import Evidence, GuardPost, Job
from app.storage import save_original


SNIPPET_MAX = 1500  # 판별에 쓰는 게시물 글자 수


def warning_message(job: Job) -> str:
    """사업주에게 보낼 보복 금지 기본 안내. AI가 작성한 문구가 없을 때(AI 응답 대기 중) 쓴다."""
    laws = " 및 ".join(P()["retaliation"]["laws"])
    return (f"{job.owner or '사장'}님, 이번 신고와 관련해 알려드립니다. {laws}에 따라 "
            "신고를 이유로 근로자에게 불리한 처우를 하는 것과, 취업을 방해할 목적으로 명부를 만들거나 "
            "연락하는 것은 금지되어 있습니다. 원만하게 해결되기를 바랍니다.")


def message_source(job: Job) -> str:
    """지금 보여 주는 안내 문구가 어디서 왔는지: custom(사용자가 고침), ai(AI 작성), waiting(AI 응답 대기 중, 기본 문구)."""
    if job.guard_message.strip():
        return "custom"
    return "ai" if job.guard_ai_message.strip() else "waiting"


def current_message(job: Job) -> str:
    """사용자가 고친 문구, AI가 작성한 문구, 기본 문구 순서로 쓴다."""
    return job.guard_message.strip() or job.guard_ai_message.strip() or warning_message(job)


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def search_queries(job: Job, keywords: list[str]) -> list[str]:
    """검색어: 사업장 이름과 사용자가 등록한 검색어(본인 이름, 별명 등)를 함께 넣는다."""
    if not keywords:
        return [job.name]
    return [f"{job.name} {k}" for k in keywords]


def search_public_posts(session: Session, job: Job, keywords: list[str]) -> dict:
    """네이버 검색 API로 공개 게시물 검색. 키가 없으면 건너뛴다. 로그인이 필요한 공간은 검색하지 않는다."""
    queries = search_queries(job, keywords)
    if not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        return {"skipped": True, "queries": queries, "reason": "네이버 검색 API 키가 없어 검색을 건너뛰었어요"}
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    added = 0
    with httpx.Client(timeout=15) as c:
        for query in queries:
            for kind in ("blog", "cafearticle", "webkr"):
                r = c.get(f"https://openapi.naver.com/v1/search/{kind}.json", params={"query": query, "display": 10},
                          headers=headers)
                if r.status_code != 200:
                    continue
                for it in r.json().get("items", []):
                    url = it.get("link", "")
                    if not url or session.exec(select(GuardPost).where(GuardPost.job_id == job.id,
                                                                       GuardPost.url == url)).first():
                        continue
                    session.add(GuardPost(job_id=job.id, url=url, title=_plain(it.get("title", "")), source="search",
                                          snippet=_plain(it.get("description", ""))[:SNIPPET_MAX], found_at=now_kst()))
                    session.flush()
                    added += 1
    session.commit()
    return {"skipped": False, "queries": queries, "added": added}


def preserve(session: Session, user_id: int, job: Job, url: str, title: str = "") -> dict:
    """사용자가 알려 준 게시물의 주소와 확인 시각을 남기고, 가능하면 화면을 캡처해 보존한다."""
    p = GuardPost(job_id=job.id, url=url, title=title, source="user", found_at=now_kst())
    session.add(p)
    session.commit()
    try:
        ev = capture(session, user_id, p)
    except Exception as exc:  # 캡처 실패해도 주소와 시각은 남긴다
        ev = None
        p.title = p.title or f"캡처 실패: {type(exc).__name__}"
    if ev:
        p.evidence_id = ev.id
    session.add(p)
    session.commit()
    return {"id": p.id, "captured": ev is not None}


def capture(session: Session, user_id: int, post: GuardPost) -> Evidence | None:
    """게시물 화면을 캡처해 원본으로 보존. Playwright가 없으면 주소와 시각만 남긴다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(post.url, timeout=20000)
        png = page.screenshot(full_page=True)
        try:  # 판별에 쓸 글자 (화면에 보이는 공개 글만)
            post.snippet = page.inner_text("body")[:SNIPPET_MAX]
            post.title = post.title or page.title()[:200]
        except Exception:
            pass
        browser.close()
    path, digest, size = save_original(user_id, f"post_{post.id}.png", png)
    ev = Evidence(user_id=user_id, job_id=post.job_id, kind="post", filename=f"post_{post.id}.png", stored_path=path,
                  sha256=digest, size=size, uploaded_at=now_kst(), note=post.url)
    session.add(ev)
    session.commit()
    return ev
