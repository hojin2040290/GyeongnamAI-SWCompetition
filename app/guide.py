"""처음 쓰는 사람 체험 안내 (새로 가입한 계정만).

- 가입할 때 고른 상황(지원 전, 일하는 중, 그만둠)에 맞는 미션 3개를 보여 준다. 서버가 기록으로 판정한다 (가입한 뒤에 한 일만).
- 체험하는 동안 업로드를 누르면 '내 기기에서 고르기'와 예시 자료(테스트자료의 가상 자료)를 함께 보여 준다.
- 미션을 다 하면 설문 안내 (SURVEY_URL). 설문은 일하는 곳 선택 창에도 늘 있다.
- 시험 계정(app/demo_db.py)은 만들 때 안내를 끈다. 사용자도 '체험 안내 끄기'로 끌 수 있다.
- 예전 베타 계정(beta1~9, owner1~3) 지우기: python -m app.guide --remove-old-beta
"""
import json
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import config
from app.auth import current_user
from app.calc.timeutil import now_kst
from app.db import get_session
from app.models import CheckRun, GuideState, Job, Payslip, Report, User, WorkRecord

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "테스트자료"

# 예시 자료의 조건 (가상카페 시험점, 시험 데이터 사례 3): 채용공고나 계약서 예시를 고르면 이 값으로 입력칸을 채운다
CAFE = {"name": "가상카페 시험점", "industry": "음식점, 카페", "work_desc": "음료 제조, 계산", "wage": 11000,
        "start_date": "2026-08-04", "probation": "no",
        "schedule": {d: {"start": "15:00", "end": "18:30", "brk": "없음"} for d in ("화", "목")}}
FILLS = {"예시/채용공고_가상카페.png": CAFE, "알바5개/3_근로계약서.png": CAFE}

# 업로드 칸(input id)마다 보여 줄 예시 자료
FILES = {
    "seekFile": [("예시/채용공고_가상카페.png", "가상카페 채용공고 (시급 11,000원, 화·목 15:00~18:30)")],
    "contractFile": [("알바5개/3_근로계약서.png", "가상카페 근로계약서 (조건을 잘 지킨 계약서)"),
                     ("02_근로계약서.png", "행복편의점 근로계약서 (문제가 있는 계약서)")],
    "payFile": [("알바5개/2_급여명세서.png", "가상분식 8월 급여명세서"), ("알바5개/2_입금내역.png", "가상분식 입금 내역")],
    "evFile": [("알바5개/2_입금내역.png", "가상분식 입금 내역"), ("알바5개/5_사업주_메시지_캡처.png", "가상치킨 사장님 메시지 캡처"),
               ("알바5개/2_급여명세서.png", "가상분식 8월 급여명세서")],
}
JOB_FILL = ("알바5개/3_근로계약서.png", "가상카페 근로계약서")  # 일하는 곳 등록 화면이 비어 있을 때 보여 주는 예시

CONTRACT = {"key": "contract", "title": "계약서 사진으로 점검하기", "go": "check",
            "how": "계약서 탭의 '근로계약서 사진 올리기'로 사진을 올리고(예시 자료도 있어요) '이 내용과 근무 기록으로 점검하기'를 눌러요"}
# 상황(가입할 때 고른 mode)마다: 제목과 미션 3개 (판정 열쇠, 할 일, 방법, 바로 가기 화면)
MODES: dict[str, dict] = {
    "seek": {"title": "아르바이트를 구하고 있어요", "missions": [
        {"key": "seek", "title": "지원 전 확인으로 공고 조건 점검하기", "go": "seek",
         "how": "'채용공고 사진 올리기'로 공고를 올리거나(예시 공고는 칸을 채워 줘요) 조건을 적고 '지원 전 확인하기'를 눌러요"},
        {"key": "job", "title": "일할 곳 등록하기", "go": "job",
         "how": "확인 결과 아래 '이곳에서 일하게 됐어요, 등록하기'를 누르고 나머지를 확인한 뒤 '시작하기'를 눌러요"},
        CONTRACT]},
    "work": {"title": "지금 아르바이트를 하고 있어요", "missions": [
        {"key": "punch", "title": "출근하기와 퇴근하기 눌러 보기", "go": "home",
         "how": "홈의 '출근하기'를 누르고 조금 뒤 '퇴근하기'를 눌러요. '방금 출근했어요' 확인 창이 뜨면 확인을 눌러요"},
        CONTRACT,
        {"key": "payday", "title": "받은 급여를 올리고 비교하기", "go": "pay",
         "how": "급여 탭에서 달을 고르고 '명세서나 입금 내역' 칸에 명세서를 올려요. 받은 금액을 확인하고 '저장하고 비교하기'를 눌러요"}]},
    "quit": {"title": "아르바이트를 그만뒀어요", "missions": [
        {"key": "quitjob", "title": "그만둔 곳 등록하기", "go": "job",
         "how": "일하는 곳 등록 화면에서 '그만뒀어요'와 그만둔 날을 적고 '시작하기'를 눌러요"},
        {"key": "paid", "title": "남은 임금을 받았는지 답하기", "go": "home",
         "how": "홈의 'AI 에이전트 종합 점검' 카드 안 '퇴직 후 임금 정산'에서 '남은 임금 받았어요'나 '아직 못 받았어요'를 눌러요"},
        {"key": "report", "title": "상담 사전 자료 만들기", "go": "docs",
         "how": "자료 탭에서 '상담 사전 자료 만들기'를 눌러요. 다 만들면 자료가 열려요"}]},
}
ALLOWED_FILES = {name for files in FILES.values() for name, _ in files} | {JOB_FILL[0]}
MARKS = ("intro", "closed", "off")  # 첫 안내 봄, 완료 창 닫음, 안내 끔

router = APIRouter(prefix="/api/guide")


# ---------- 시작과 판정 ----------
def start(s: Session, user: User) -> None:
    """가입하자마자 체험 안내를 시작한다 (가입 뒤에 한 일만 미션으로 센다)."""
    s.add(GuideState(user_id=user.id, mode=user.mode if user.mode in MODES else "work", started_at=now_kst()))
    s.commit()


def set_mode(s: Session, user: User) -> None:
    """처음 화면에서 상황을 다시 고르면 미션도 그 상황으로 (아직 다 하지 않았을 때만)."""
    st = _state(s, user.id)
    if st and user.mode in MODES and st.mode != user.mode and not all(_done_all(s, user, st)):
        st.mode = user.mode
        s.add(st)
        s.commit()


def _state(s: Session, user_id: int) -> GuideState | None:
    return s.exec(select(GuideState).where(GuideState.user_id == user_id)).first()


def _marks(st: GuideState) -> list[str]:
    try:
        return [str(x) for x in json.loads(st.marks or "[]")]
    except ValueError:
        return []


def _jobs(s: Session, user: User) -> list[Job]:
    """이 계정의 일하는 곳 (안내는 가입하자마자 시작하고 그때는 일하는 곳이 없으므로, 모두 가입 뒤에 등록한 곳이다)."""
    return s.exec(select(Job).where(Job.user_id == user.id)).all()


def mission_done(s: Session, user: User, st: GuideState, key: str) -> bool:
    """미션 하나를 했는지 기록으로 판정한다 (안내를 시작한 뒤에 한 일만)."""
    since = st.started_at
    if key in ("seek", "contract"):
        return s.exec(select(CheckRun).where(CheckRun.user_id == user.id, CheckRun.kind == key,
                                             CheckRun.created_at >= since)).first() is not None
    if key == "job":
        return bool(_jobs(s, user))
    if key == "quitjob":
        return any(j.status == "quit" for j in _jobs(s, user))
    if key == "punch":
        rows = s.exec(select(WorkRecord).where(WorkRecord.user_id == user.id, WorkRecord.clock_in >= since)).all()
        return any(r.clock_out is not None for r in rows)
    if key == "payday":  # 받은 급여를 저장했고 (저장하면 비교까지 이어서 한다)
        job_ids = [j.id for j in _jobs(s, user)]
        return bool(job_ids) and s.exec(select(Payslip).where(Payslip.job_id.in_(job_ids),
                                                              Payslip.created_at >= since)).first() is not None
    if key == "paid":
        return any(j.status == "quit" and j.paid_after_quit is not None for j in _jobs(s, user))
    if key == "report":
        return s.exec(select(Report).where(Report.user_id == user.id, Report.created_at >= since)).first() is not None
    return False


def _done_all(s: Session, user: User, st: GuideState) -> list[bool]:
    return [mission_done(s, user, st, m["key"]) for m in MODES[st.mode]["missions"]]


def _files() -> dict:
    return {inp: [{"name": n, "label": label, "url": f"/api/guide/files/{n}", "fill": FILLS.get(n)} for n, label in files]
            for inp, files in FILES.items()}


def state_of(s: Session, user: User) -> dict:
    """화면이 보는 체험 안내 상태. 안내가 없거나 끈 계정은 on=False (설문 주소는 메뉴에 늘 쓰므로 함께 준다)."""
    st = _state(s, user.id)
    if not st or st.mode not in MODES or "off" in _marks(st):
        return {"on": False, "survey_url": config.SURVEY_URL}
    mode = MODES[st.mode]
    missions = [{**m, "done": d} for m, d in zip(mode["missions"], _done_all(s, user, st))]
    marks = _marks(st)
    all_done = all(m["done"] for m in missions)
    return {"on": True, "mode": st.mode, "title": mode["title"], "missions": missions,
            "done_count": sum(m["done"] for m in missions), "all_done": all_done,
            "intro_seen": "intro" in marks, "closed": "closed" in marks,
            # 예시 자료는 체험하는 동안만 (다 하고 완료 창을 닫으면 업로드는 바로 내 파일 고르기)
            "files": _files() if not (all_done and "closed" in marks) else {},
            "job_fill": {"url": f"/api/guide/files/{JOB_FILL[0]}", "label": JOB_FILL[1], "fill": FILLS[JOB_FILL[0]]},
            "survey_url": config.SURVEY_URL}


# ---------- API ----------
@router.get("/state")
def guide_state(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return state_of(s, u)


class MarkIn(BaseModel):
    key: str


@router.post("/mark")
def guide_mark(data: MarkIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """화면에서만 알 수 있는 일을 남긴다: intro(첫 안내 봄), closed(완료 창 닫음), off(체험 안내 끄기)."""
    if data.key not in MARKS:
        raise HTTPException(422, "남길 수 없는 항목이에요")
    st = _state(s, u.id)
    if st and data.key not in _marks(st):
        st.marks = json.dumps(_marks(st) + [data.key])
        s.add(st)
        s.commit()
    return state_of(s, u)


@router.get("/files/{name:path}")
def guide_file(name: str, u: User = Depends(current_user)):
    """예시 자료 (정해 둔 가상 자료만)."""
    if name not in ALLOWED_FILES:
        raise HTTPException(404, "자료를 찾지 못했어요")
    path = (MATERIAL / name).resolve()
    if MATERIAL.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "자료를 찾지 못했어요")
    return FileResponse(path, media_type="image/png", filename=path.name)


# ---------- 예전 베타 계정 지우기 (python -m app.guide --remove-old-beta) ----------
OLD_BETA = [f"beta{n}@example.com" for n in range(1, 10)] + [f"owner{n}@example.com" for n in (1, 2, 3)]


def _delete_user(s: Session, uid: int) -> int:
    """한 사용자의 기록을 모든 테이블에서 지운다 (사용자 번호가 있는 표, 그 사람 사업장 번호만 있는 표). 지운 행 수."""
    from sqlalchemy import text
    from sqlmodel import SQLModel
    job_ids = [j.id for j in s.exec(select(Job).where(Job.user_id == uid)).all()]
    n = 0
    for table in reversed(SQLModel.metadata.sorted_tables):  # 참조하는 표부터
        cols = table.columns.keys()
        if "user_id" not in cols and "job_id" in cols and job_ids:
            n += s.execute(text(f'DELETE FROM "{table.name}" WHERE job_id IN ({",".join(map(str, job_ids))})')).rowcount
    for table in reversed(SQLModel.metadata.sorted_tables):
        if "user_id" in table.columns.keys():
            n += s.execute(text(f'DELETE FROM "{table.name}" WHERE user_id = :u'), {"u": uid}).rowcount
    if _has_table(s, "betastate"):  # 예전 베타 체험판의 진행 표 (지금 코드에는 없음)
        n += s.execute(text('DELETE FROM "betastate" WHERE user_id = :u'), {"u": uid}).rowcount
    n += s.execute(text('DELETE FROM "user" WHERE id = :u'), {"u": uid}).rowcount
    return n


def _has_table(s: Session, name: str) -> bool:
    from sqlalchemy import text
    return s.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}).first() is not None


def _delete_files(s: Session, uid: int) -> None:
    """올린 원본(data/uploads/<사용자 번호>/)과 상담 사전 자료 파일. data 폴더 밖은 건드리지 않는다."""
    import shutil
    for r in s.exec(select(Report).where(Report.user_id == uid)).all():
        path = Path(r.path).resolve()
        if config.REPORT_DIR.resolve() in path.parents and path.is_file():
            path.unlink()
    folder = (config.UPLOAD_DIR / str(uid)).resolve()
    if config.UPLOAD_DIR.resolve() in folder.parents and folder.is_dir():
        shutil.rmtree(folder)


def remove_old_beta() -> int:
    """예전 베타 체험판 계정(beta1~9, owner1~3)을 기록과 올린 파일까지 지운다. 다른 계정은 건드리지 않는다."""
    from app.db import engine, init_db
    init_db()
    with Session(engine) as s:
        users = s.exec(select(User).where(User.email.in_(OLD_BETA))).all()
        for u in users:
            _delete_files(s, u.id)
            print(f"{u.email}: 기록 {_delete_user(s, u.id)}건과 올린 파일을 지웠어요")
        s.commit()
    print(f"예전 베타 계정 {len(users)}개를 지웠어요 (저장 위치: {config.DB_PATH})")
    return len(users)


if __name__ == "__main__":
    if "--remove-old-beta" in sys.argv[1:]:
        remove_old_beta()
    else:
        print("사용: python -m app.guide --remove-old-beta  (예전 베타 계정 beta1~9, owner1~3을 지운다)")
