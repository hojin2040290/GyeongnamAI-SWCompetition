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


def warning_message(job: Job) -> str:
    """사업주에게 보낼 보복 금지 안내. (AI 연결 후에는 상황에 맞게 AI가 작성)"""
    laws = " 및 ".join(P()["retaliation"]["laws"])
    return (f"{job.owner or '사장'}님, 이번 신고와 관련해 알려드립니다. {laws}에 따라 "
            "신고를 이유로 근로자에게 불리한 처우를 하는 것과, 취업을 방해할 목적으로 명부를 만들거나 "
            "연락하는 것은 금지되어 있습니다. 원만하게 해결되기를 바랍니다.")


def search_public_posts(session: Session, job: Job, user_name: str) -> dict:
    """네이버 검색 API로 공개 게시물 검색. 키가 없으면 건너뛴다. 로그인이 필요한 공간은 검색하지 않는다."""
    if not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        return {"skipped": True, "reason": "네이버 검색 API 키가 없어 검색을 건너뛰었어요"}
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    query = f"{job.name} {user_name}".strip()
    added = 0
    with httpx.Client(timeout=15) as c:
        for kind in ("blog", "cafearticle", "webkr"):
            r = c.get(f"https://openapi.naver.com/v1/search/{kind}.json", params={"query": query, "display": 10},
                      headers=headers)
            if r.status_code != 200:
                continue
            for it in r.json().get("items", []):
                url = it.get("link", "")
                if not url or session.exec(select(GuardPost).where(GuardPost.job_id == job.id, GuardPost.url == url)).first():
                    continue
                title = html.unescape(re.sub(r"<[^>]+>", "", it.get("title", "")))
                session.add(GuardPost(job_id=job.id, url=url, title=title, source="search", found_at=now_kst()))
                added += 1
    session.commit()
    return {"skipped": False, "added": added}


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
        browser.close()
    path, digest, size = save_original(user_id, f"post_{post.id}.png", png)
    ev = Evidence(user_id=user_id, job_id=post.job_id, kind="post", filename=f"post_{post.id}.png", stored_path=path,
                  sha256=digest, size=size, uploaded_at=now_kst(), note=post.url)
    session.add(ev)
    session.commit()
    return ev
