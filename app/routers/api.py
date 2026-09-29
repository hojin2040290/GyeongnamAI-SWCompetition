"""웹 화면이 부르는 API."""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import guard, report
from app.agent import core
from app.agent.tools import make_tools
from app.auth import check_password, current_user, hash_password
from app.calc.age import age_on
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.db import get_session
from app.judge import engine
from app.models import (AgentLog, CheckRun, ContractFields, Evidence, GuardPost, Job, Notification, Payslip, Report,
                        User, WorkRecord)
from app.storage import save_original

router = APIRouter(prefix="/api")


# ---------- 로그인 ----------
class RegisterIn(BaseModel):
    email: str
    password: str
    birth_date: date
    mode: str = "work"


class LoginIn(BaseModel):
    email: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "birth_date": u.birth_date.isoformat(), "mode": u.mode,
            "age": age_on(u.birth_date, today_kst())}


@router.post("/auth/register")
def register(data: RegisterIn, request: Request, s: Session = Depends(get_session)):
    if len(data.password) < 4:
        raise HTTPException(400, "비밀번호는 4자 이상으로 정해 주세요")
    if s.exec(select(User).where(User.email == data.email)).first():
        raise HTTPException(400, "이미 가입한 이메일이에요. 로그인해 주세요")
    u = User(email=data.email, password_hash=hash_password(data.password), birth_date=data.birth_date,
             mode=data.mode, created_at=now_kst())
    s.add(u)
    s.commit()
    request.session["uid"] = u.id
    return user_out(u)


@router.post("/auth/login")
def login(data: LoginIn, request: Request, s: Session = Depends(get_session)):
    u = s.exec(select(User).where(User.email == data.email)).first()
    if not u or not check_password(data.password, u.password_hash):
        raise HTTPException(400, "이메일이나 비밀번호가 맞지 않아요")
    request.session["uid"] = u.id
    return user_out(u)


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(u: User = Depends(current_user)):
    return user_out(u)


# ---------- 사업장 ----------
class JobIn(BaseModel):
    name: str
    status: str = "working"
    quit_date: Optional[date] = None
    industry: str = ""
    work_desc: str = ""
    wage: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    no_end: bool = False
    contract_written: Optional[bool] = None
    copy_received: Optional[bool] = None
    probation: str = "unknown"
    probation_months: Optional[int] = None
    schedule: dict = {}
    size: str = "unknown"
    pay_cycle: str = ""
    payday: Optional[int] = None
    pay_method: str = ""
    deduction: str = ""
    consent: str = ""
    address: str = ""
    owner: str = ""


def own_job(s: Session, u: User, job_id: int) -> Job:
    job = s.get(Job, job_id)
    if not job or job.user_id != u.id:
        raise HTTPException(404, "사업장을 찾을 수 없어요")
    return job


def job_out(j: Job) -> dict:
    d = j.model_dump()
    d["schedule"] = json.loads(j.schedule_json or "{}")
    d.pop("schedule_json", None)
    for k in ("quit_date", "start_date", "end_date", "created_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


def apply_job(job: Job, data: JobIn) -> None:
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "사업장 이름을 입력해 주세요")
    for k, v in data.model_dump().items():
        if k == "schedule":
            job.schedule_json = json.dumps(v, ensure_ascii=False)
        else:
            setattr(job, k, v)
    job.name = name
    if job.status != "quit":
        job.quit_date = None


@router.get("/jobs")
def list_jobs(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return [job_out(j) for j in s.exec(select(Job).where(Job.user_id == u.id).order_by(Job.id))]


@router.post("/jobs")
def create_job(data: JobIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    if s.exec(select(Job).where(Job.user_id == u.id, Job.name == data.name.strip())).first():
        raise HTTPException(400, f"{data.name} 이름이 이미 있어요. 지점명까지 적어 주세요")
    job = Job(user_id=u.id, name=data.name, created_at=now_kst())
    apply_job(job, data)
    s.add(job)
    s.commit()
    return job_out(job)


@router.put("/jobs/{job_id}")
def update_job(job_id: int, data: JobIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    apply_job(job, data)
    s.add(job)
    s.commit()
    return job_out(job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    s.delete(job)
    s.commit()
    return {"ok": True}


class QuitIn(BaseModel):
    quit_date: date


@router.post("/jobs/{job_id}/quit")
def set_quit(job_id: int, data: QuitIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    job.status, job.quit_date, job.paid_after_quit = "quit", data.quit_date, None
    s.add(job)
    s.commit()
    return settlement(job_id, u, s)


class PaidIn(BaseModel):
    paid: bool


@router.post("/jobs/{job_id}/paid")
def set_paid(job_id: int, data: PaidIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    job.paid_after_quit = data.paid
    s.add(job)
    s.commit()
    return settlement(job_id, u, s)


@router.get("/jobs/{job_id}/settlement")
def settlement(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    return {"settlement": core.run_quit_check(s, u.id, job_id)}


# ---------- 출퇴근 ----------
class PunchIn(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None


def rec_out(r: WorkRecord) -> dict:
    return {"id": r.id, "clock_in": r.clock_in.isoformat(), "clock_out": r.clock_out.isoformat() if r.clock_out else None,
            "gps": r.in_lat is not None}


@router.post("/jobs/{job_id}/punch")
def punch(job_id: int, data: PunchIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """출근 또는 퇴근. 시각은 서버가 요청을 받은 순간으로 기록한다."""
    job = own_job(s, u, job_id)
    if job.status == "quit":
        raise HTTPException(400, "그만둔 사업장이에요")
    t = now_kst()
    open_rec = s.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.clock_out == None)  # noqa: E711
                      .order_by(WorkRecord.id.desc())).first()
    if open_rec:
        open_rec.clock_out, open_rec.out_lat, open_rec.out_lng = t, data.lat, data.lng
        s.add(open_rec)
        action = "out"
        rec = open_rec
    else:
        rec = WorkRecord(user_id=u.id, job_id=job_id, clock_in=t, in_lat=data.lat, in_lng=data.lng)
        s.add(rec)
        action = "in"
    s.commit()
    return {"action": action, "server_time": t.isoformat(), "record": rec_out(rec)}


@router.get("/jobs/{job_id}/records")
def records(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rows = s.exec(select(WorkRecord).where(WorkRecord.job_id == job_id).order_by(WorkRecord.clock_in.desc())).all()
    return {"working": any(r.clock_out is None for r in rows), "records": [rec_out(r) for r in rows]}


# ---------- 증거 자료 ----------
def ev_out(e: Evidence) -> dict:
    return {"id": e.id, "kind": e.kind, "filename": e.filename, "uploaded_at": e.uploaded_at.isoformat(),
            "sha256": e.sha256, "size": e.size, "note": e.note}


async def store_upload(s: Session, u: User, job_id, kind: str, file: UploadFile, note: str = "") -> Evidence:
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일이에요")
    path, digest, size = save_original(u.id, file.filename, data)
    ev = Evidence(user_id=u.id, job_id=job_id, kind=kind, filename=file.filename or "file", stored_path=path,
                  sha256=digest, size=size, uploaded_at=now_kst(), note=note)
    s.add(ev)
    s.commit()
    return ev


@router.post("/jobs/{job_id}/evidence")
async def upload_evidence(job_id: int, kind: str = Form("other"), note: str = Form(""), file: UploadFile = File(...),
                          u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    return ev_out(await store_upload(s, u, job_id, kind, file, note))


@router.post("/evidence")
async def upload_evidence_nojob(kind: str = Form("notice"), note: str = Form(""), file: UploadFile = File(...),
                                u: User = Depends(current_user), s: Session = Depends(get_session)):
    """사업장 등록 전 자료 (예: 지원 전 채용공고)."""
    return ev_out(await store_upload(s, u, None, kind, file, note))


@router.get("/jobs/{job_id}/evidence")
def list_evidence(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rows = s.exec(select(Evidence).where(Evidence.job_id == job_id).order_by(Evidence.uploaded_at.desc())).all()
    return [ev_out(e) for e in rows]


@router.get("/evidence/{ev_id}/file")
def evidence_file(ev_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    ev = s.get(Evidence, ev_id)
    if not ev or ev.user_id != u.id:
        raise HTTPException(404, "자료를 찾을 수 없어요")
    return FileResponse(ev.stored_path, filename=ev.filename)


# ---------- 계약서 점검 ----------
@router.post("/jobs/{job_id}/contract")
async def upload_contract(job_id: int, file: UploadFile = File(...), u: User = Depends(current_user),
                          s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    ev = await store_upload(s, u, job_id, "contract", file)
    # AI 연결 전: 사진을 읽지 않으므로 빈 칸을 돌려주고 사용자가 직접 입력한다.
    return {"evidence": ev_out(ev), "fields": {k: "" for k in P()["written_terms"]["items"]}, "ai": False}


class FieldsIn(BaseModel):
    fields: dict


@router.get("/jobs/{job_id}/contract/fields")
def get_fields(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    return {"items": P()["written_terms"]["items"], "fields": make_tools(s, u.id, job_id)["get_contract_fields"]()}


@router.put("/jobs/{job_id}/contract/fields")
def put_fields(job_id: int, data: FieldsIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    s.add(ContractFields(job_id=job_id, fields_json=json.dumps(data.fields, ensure_ascii=False), confirmed_at=now_kst()))
    s.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/check")
def run_check(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    return core.run_contract_check(s, u.id, job_id)


@router.get("/jobs/{job_id}/check")
def last_check(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    row = s.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == "contract")
                 .order_by(CheckRun.id.desc())).first()
    return {"items": json.loads(row.results_json) if row else None, "created_at": row.created_at.isoformat() if row else None}


# ---------- 지원 전 확인 ----------
class SeekIn(BaseModel):
    name: str = ""
    industry: str = ""
    work_desc: str = ""
    wage: Optional[int] = None
    probation: str = "unknown"
    schedule: dict = {}


@router.post("/seek/check")
def seek_check(data: SeekIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    facts = engine.Facts(birth=u.birth_date, on=today_kst(), wage=data.wage, probation=data.probation,
                         schedule=data.schedule, industry=data.industry, work_desc=data.work_desc)
    items = engine.judge(facts, "seek")
    out = {"items": engine.to_json(items), "questions": engine.questions(facts, items)}
    s.add(CheckRun(user_id=u.id, kind="seek", results_json=json.dumps({"input": data.model_dump(), **out}, ensure_ascii=False),
                   created_at=now_kst()))
    s.commit()
    return out


# ---------- 급여 ----------
@router.post("/jobs/{job_id}/payslip")
async def add_payslip(job_id: int, month: str = Form(...), amount: int = Form(...), file: Optional[UploadFile] = File(None),
                      u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    ev_id = None
    if file is not None and file.filename:
        ev_id = (await store_upload(s, u, job_id, "payslip", file, f"{month} 급여")).id
    s.add(Payslip(job_id=job_id, month=month, amount=amount, evidence_id=ev_id, created_at=now_kst()))
    s.commit()
    return core.run_payday(s, u.id, job_id, month)


@router.get("/jobs/{job_id}/pay")
def pay(job_id: int, month: str, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    t = make_tools(s, u.id, job_id)
    exp = t["calc_pay"](month)
    paid = t["get_payslip"](month)
    return {"expected": exp, "paid": paid, "compare": t["compare_pay"](exp, paid)}


@router.post("/jobs/{job_id}/agent/payday")
def agent_payday(job_id: int, month: str, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """시연용: 월급날이 된 것처럼 에이전트를 지금 시작한다."""
    own_job(s, u, job_id)
    return core.run_payday(s, u.id, job_id, month)


@router.get("/jobs/{job_id}/agent/log")
def agent_log(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rows = s.exec(select(AgentLog).where(AgentLog.job_id == job_id, AgentLog.user_id == u.id)
                  .order_by(AgentLog.id.desc()).limit(40)).all()
    return [{"run_id": r.run_id, "event": r.event, "step": r.step, "detail": r.detail, "at": r.created_at.isoformat()}
            for r in rows]


# ---------- 상담 ----------
@router.get("/counsel")
def counsel(u: User = Depends(current_user)):
    age = age_on(u.birth_date, today_kst())
    return [c for c in P()["counsel"] if c["min_age"] <= age <= c["max_age"]]


@router.post("/jobs/{job_id}/report")
def make_report(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rep = report.build(s, u.id, job_id)
    return {"id": rep.id, "url": f"/api/reports/{rep.id}"}


@router.get("/reports/{rep_id}")
def get_report(rep_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    rep = s.get(Report, rep_id)
    if not rep or rep.user_id != u.id:
        raise HTTPException(404, "자료를 찾을 수 없어요")
    return FileResponse(rep.path, media_type="text/html")


# ---------- 보복 대응 ----------
class ReportedIn(BaseModel):
    reported: bool


def post_out(p: GuardPost) -> dict:
    return {"id": p.id, "url": p.url, "title": p.title, "source": p.source, "status": p.status,
            "evidence_id": p.evidence_id, "found_at": p.found_at.isoformat()}


@router.get("/jobs/{job_id}/guard")
def guard_state(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    posts = s.exec(select(GuardPost).where(GuardPost.job_id == job_id).order_by(GuardPost.id.desc())).all()
    return {"reported": job.reported, "message": guard.warning_message(job) if job.reported else "",
            "posts": [post_out(p) for p in posts]}


@router.post("/jobs/{job_id}/guard")
def set_reported(job_id: int, data: ReportedIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    job.reported = data.reported
    s.add(job)
    s.commit()
    return guard_state(job_id, u, s)


class PostIn(BaseModel):
    url: str
    title: str = ""


@router.post("/jobs/{job_id}/guard/posts")
def add_post(job_id: int, data: PostIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    if not data.url.startswith(("http://", "https://")):
        raise HTTPException(400, "게시물 주소는 http 또는 https로 시작해야 해요")
    p = GuardPost(job_id=job_id, url=data.url, title=data.title, source="user", found_at=now_kst())
    s.add(p)
    s.commit()
    ev = None
    try:
        ev = guard.capture(s, u.id, p)
    except Exception as exc:  # 캡처 실패해도 주소와 시각은 남긴다
        p.title = p.title or f"캡처 실패: {type(exc).__name__}"
    if ev:
        p.evidence_id = ev.id
    s.add(p)
    s.commit()
    return post_out(p)


@router.post("/jobs/{job_id}/guard/search")
def guard_search(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    return guard.search_public_posts(s, job, "")


# ---------- 알림 ----------
@router.get("/notifications")
def notifications(u: User = Depends(current_user), s: Session = Depends(get_session)):
    rows = s.exec(select(Notification).where(Notification.user_id == u.id).order_by(Notification.id.desc()).limit(20)).all()
    return [{"id": n.id, "job_id": n.job_id, "title": n.title, "body": n.body, "read": n.read,
             "at": n.created_at.isoformat()} for n in rows]


@router.post("/notifications/read")
def read_all(u: User = Depends(current_user), s: Session = Depends(get_session)):
    for n in s.exec(select(Notification).where(Notification.user_id == u.id, Notification.read == False)):  # noqa: E712
        n.read = True
        s.add(n)
    s.commit()
    return {"ok": True}
