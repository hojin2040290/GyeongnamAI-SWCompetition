"""베타 테스트 체험판 (.env의 BETA_GUIDE=true일 때만 동작).

- 상황별 베타 계정: 알바를 안 해 본 사람(none), 지금 하는 사람(working), 해 본 적 있는 사람(quit).
  테스터용 beta1~9, 개발자 확인용 owner1~3. 비밀번호는 모두 test1234. 시험 데이터(app/demo_db.py)의 사례를 그대로 쓴다.
- 미션 3개: 상황마다 해 볼 기능. 서버가 기록으로 판정한다 (계정을 만든 뒤에 한 일만 센다).
- 테스트 자료: 업로드를 누르면 그 상황에 맞는 가상 자료를 보여 주고 '올릴게요'로 올린다 (실제 개인정보를 쓰지 않게).
- 계정 만들기: python -m app.beta (서버를 켤 때 BETA_GUIDE=true이고 계정이 모자라면 알아서 실행한다)
BETA_GUIDE=false면 API는 꺼짐만 알리고, 화면에는 아무것도 보이지 않는다.
"""
import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import config
from app.auth import current_user
from app.calc.timeutil import now_kst
from app.db import get_session
from app.models import BetaState, CheckRun, Evidence, Job, Report, User, WorkRecord

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "테스트자료"
PASSWORD = "test1234"

# 계정과 상황: 테스터 9명(상황마다 3명) + 개발자 확인용 3개
ACCOUNTS = ([(f"beta{n}@example.com", "none") for n in (1, 2, 3)]
            + [(f"beta{n}@example.com", "working") for n in (4, 5, 6)]
            + [(f"beta{n}@example.com", "quit") for n in (7, 8, 9)]
            + [("owner1@example.com", "none"), ("owner2@example.com", "working"), ("owner3@example.com", "quit")])

# 상황마다: 이야기, 미션(판정 열쇠, 할 일, 방법, 바로 가기 화면), 업로드할 때 보여 줄 테스트 자료(입력칸 id → 파일)
PERSONAS: dict[str, dict] = {
    "none": {
        "title": "알바를 아직 안 해 본 사람",
        "story": "곧 첫 알바를 구하려고 해요. 지원하기 전에 조건을 확인하고, 받은 근로계약서를 점검해 보세요.",
        "missions": [
            {"key": "seek", "title": "지원 전 확인으로 공고 조건 점검하기", "go": "seek",
             "how": "시작 화면의 '지원하기 전이에요'(앱 안에서는 일하는 곳 선택 창의 지원 전 확인)에서 시급과 근무 시간을 적고 점검해요"},
            {"key": "contract", "title": "일할 곳을 등록하고 계약서 사진으로 점검하기", "go": "check",
             "how": "일할 곳을 등록한 뒤 계약서 탭에서 '계약서 사진 올리기'를 누르면 테스트용 계약서가 나와요"},
            {"key": "trace", "title": "'에이전트 동작 보기'를 열어 AI가 판단한 과정 보기", "go": "check",
             "how": "점검 결과 아래의 '에이전트 동작 보기'를 눌러 펼쳐요"},
        ],
        "files": {
            "contractFile": [("알바5개/3_근로계약서.png", "가상카페 근로계약서 (조건을 잘 지킨 계약서)"),
                             ("02_근로계약서.png", "행복편의점 근로계약서 (문제가 있는 계약서)")],
            "evFile": [("02_근로계약서.png", "행복편의점 근로계약서")],
        },
    },
    "working": {
        "title": "지금 알바를 하는 사람",
        "story": "가상분식에서 일하고 있어요. 8월 급여가 생각보다 적게 들어온 것 같아요.",
        "missions": [
            {"key": "punch", "title": "출근하기와 퇴근하기 눌러 보기", "go": "home",
             "how": "홈 맨 위의 출근하기를 누르고, 조금 뒤 퇴근하기를 눌러요"},
            {"key": "payday", "title": "급여 탭에서 명세서 사진을 올리고 점검하기", "go": "pay",
             "how": "급여 탭에서 8월을 고르고 명세서를 올린 뒤 '지금 점검'을 눌러요"},
            {"key": "evidence", "title": "자료 탭에서 입금 내역을 증거로 올리기", "go": "docs",
             "how": "자료 탭의 '자료 올리기'를 누르면 테스트용 입금 내역이 나와요"},
        ],
        "files": {
            "payFile": [("알바5개/2_급여명세서.png", "가상분식 8월 급여명세서"), ("알바5개/2_입금내역.png", "가상분식 입금 내역")],
            "evFile": [("알바5개/2_입금내역.png", "가상분식 입금 내역"), ("알바5개/2_급여명세서.png", "가상분식 8월 급여명세서")],
            "contractFile": [("알바5개/2_근로계약서.png", "가상분식 근로계약서")],
        },
        "case": "2",
    },
    "quit": {
        "title": "알바를 해 본 적 있는 사람",
        "story": "가상치킨을 8월 29일에 그만뒀는데 마지막 월급을 아직 못 받았어요. 사장님 메시지도 걱정돼요.",
        "missions": [
            {"key": "paid", "title": "홈의 퇴직 정산에서 '아직 못 받았어요' 누르기", "go": "home",
             "how": "홈의 AI 에이전트 종합 점검 카드에 있는 퇴직 후 임금 정산 줄에서 눌러요"},
            {"key": "guard", "title": "보호 탭에서 '신고했어요'를 켜고 보복 금지 안내 받기", "go": "guard",
             "how": "보호 탭에서 신고했어요를 켜면 사업주에게 보낼 안내 문구가 만들어져요"},
            {"key": "report", "title": "자료 탭에서 상담 사전 자료 만들기", "go": "docs",
             "how": "자료 탭에서 사장님 메시지 캡처를 올린 뒤 상담 사전 자료 만들기를 눌러요"},
        ],
        "files": {
            "evFile": [("알바5개/5_사업주_메시지_캡처.png", "가상치킨 사장님 메시지 캡처"), ("알바5개/5_근로계약서.png", "가상치킨 근로계약서")],
            "contractFile": [("알바5개/5_근로계약서.png", "가상치킨 근로계약서")],
        },
        "case": "5",
    },
}
ALLOWED_FILES = {name for p in PERSONAS.values() for files in p["files"].values() for name, _ in files}

router = APIRouter(prefix="/api/beta")


# ---------- 미션 판정 ----------
def _marks(st: BetaState) -> list[str]:
    try:
        return [str(x) for x in json.loads(st.marks or "[]")]
    except ValueError:
        return []


def _after(s: Session, model, user_id: int, since: datetime, stamp: str, **where) -> bool:
    q = select(model).where(model.user_id == user_id, getattr(model, stamp) >= since)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return s.exec(q).first() is not None


def mission_done(s: Session, user: User, st: BetaState, key: str) -> bool:
    """미션 하나를 했는지 기록으로 판정한다 (계정을 만든 뒤에 한 일만)."""
    since = st.started_at
    jobs = s.exec(select(Job).where(Job.user_id == user.id)).all()
    if key == "trace":
        return "trace" in _marks(st)
    if key in ("seek", "contract", "payday"):
        return _after(s, CheckRun, user.id, since, "created_at", kind=key)
    if key == "punch":
        rows = s.exec(select(WorkRecord).where(WorkRecord.user_id == user.id, WorkRecord.clock_in >= since)).all()
        return any(r.clock_out is not None for r in rows)
    if key == "evidence":
        return _after(s, Evidence, user.id, since, "uploaded_at")
    if key == "paid":
        return any(j.status == "quit" and j.paid_after_quit is not None for j in jobs)
    if key == "guard":
        return any(j.reported for j in jobs)
    if key == "report":
        return _after(s, Report, user.id, since, "created_at")
    return False


def _files(persona: dict) -> dict:
    return {inp: [{"name": name, "label": label, "url": f"/api/beta/files/{name}"} for name, label in files]
            for inp, files in persona["files"].items()}


def state_of(s: Session, user: User) -> dict:
    if not config.BETA_GUIDE:
        return {"on": False}
    st = s.exec(select(BetaState).where(BetaState.user_id == user.id)).first()
    if not st or st.persona not in PERSONAS:
        return {"on": True, "persona": None, "form_url": config.BETA_FORM_URL}
    p = PERSONAS[st.persona]
    missions = [{**m, "done": mission_done(s, user, st, m["key"])} for m in p["missions"]]
    marks = _marks(st)
    return {"on": True, "persona": st.persona, "title": p["title"], "story": p["story"], "missions": missions,
            "done_count": sum(m["done"] for m in missions), "all_done": all(m["done"] for m in missions),
            "intro_seen": "intro" in marks, "closed": "closed" in marks, "files": _files(p),
            "form_url": config.BETA_FORM_URL}


# ---------- API ----------
@router.get("/config")
def beta_config():
    """로그인 전에도 보는 설정 (첫 화면의 스마트폰 안내)."""
    return {"on": config.BETA_GUIDE, "form_url": config.BETA_FORM_URL if config.BETA_GUIDE else ""}


@router.get("/state")
def beta_state(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return state_of(s, u)


class MarkIn(BaseModel):
    key: str


@router.post("/mark")
def beta_mark(data: MarkIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """화면에서만 알 수 있는 일을 남긴다: intro(첫 안내 봄), trace(동작 보기 엶), closed(완료 창 닫음)."""
    if not config.BETA_GUIDE:
        return {"on": False}
    if data.key not in ("intro", "trace", "closed"):
        raise HTTPException(422, "남길 수 없는 항목이에요")
    st = s.exec(select(BetaState).where(BetaState.user_id == u.id)).first()
    if st and data.key not in _marks(st):
        st.marks = json.dumps(_marks(st) + [data.key])
        s.add(st)
        s.commit()
    return state_of(s, u)


@router.get("/files/{name:path}")
def beta_file(name: str, u: User = Depends(current_user)):
    """상황별 테스트 자료 (정해 둔 가상 자료만)."""
    if not config.BETA_GUIDE or name not in ALLOWED_FILES:
        raise HTTPException(404, "자료를 찾지 못했어요")
    path = (MATERIAL / name).resolve()
    if MATERIAL.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "자료를 찾지 못했어요")
    return FileResponse(path, media_type="image/png", filename=path.name)


# ---------- 계정 만들기 (python -m app.beta) ----------
def _case(persona: str) -> dict | None:
    """시험 데이터 사례를 미션에 맞게 고친다: 자료는 미션에서 올리고, 퇴직 정산 받음 여부와 신고는 미션에서 정한다."""
    from app.demo_db import CASES
    key = PERSONAS[persona].get("case")
    if not key:
        return None
    case = copy.deepcopy(next(c for c in CASES if c["key"] == key))
    case["docs"] = []
    case.pop("paid_after_quit", None)
    case["reported"] = False
    return case


def build_account(c, s_factory, email: str, persona: str) -> None:
    from app.demo_db import BIRTH, _ok, build_case
    _ok(c.post("/api/auth/register", json={"email": email, "password": PASSWORD, "birth_date": BIRTH}), f"{email} 가입")
    uid = _ok(c.get("/api/me"), "내 정보")["id"]
    case = _case(persona)
    if case:
        build_case(c, uid, case)
    with s_factory() as s:
        s.add(BetaState(user_id=uid, persona=persona, started_at=now_kst()))
        s.commit()


def missing_accounts(db: Path) -> list[tuple[str, str]]:
    from app.demo_db import existing_emails
    have = existing_emails(db)
    return [(e, p) for e, p in ACCOUNTS if e not in have]


def main() -> None:
    os.environ["BETA_BUILDING"] = "1"  # 이 실행 안에서 서버 시작 처리가 다시 계정을 만들지 않게
    from fastapi.testclient import TestClient

    from app.db import engine, init_db
    from app.main import app
    init_db()
    todo = missing_accounts(config.DB_PATH)
    for email, persona in todo:
        build_account(TestClient(app), lambda: Session(engine), email, persona)  # 계정마다 새 클라이언트 (세션이 섞이지 않게)
        print(f"{email} ({PERSONAS[persona]['title']}) / {PASSWORD}")
    print(f"베타 계정 {len(todo)}개를 만들었어요 (저장 위치: {config.DB_PATH})")


if __name__ == "__main__":
    sys.exit(main())
