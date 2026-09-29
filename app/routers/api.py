"""웹 화면이 부르는 API."""
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import guard
from app.agent import core
from app.agent.tools import keywords_of, make_tools
from app.auth import check_password, current_user, hash_password
from app.calc.age import age_on
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.db import get_session
from app.law.lookup import attach_articles, table_status
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
            "age": age_on(u.birth_date, today_kst()), "gps_consent": u.gps_consent, "last_job_id": u.last_job_id}


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


class MeIn(BaseModel):
    birth_date: date
    mode: Optional[str] = None


@router.put("/me")
def update_me(data: MeIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """생년월일(나이 기준)과 처음 고른 상황은 나중에도 고칠 수 있다."""
    if data.birth_date > today_kst():
        raise HTTPException(400, "생년월일이 오늘보다 늦어요")
    u.birth_date = data.birth_date
    if data.mode in ("seek", "work", "quit"):
        u.mode = data.mode
    s.add(u)
    s.commit()
    return user_out(u)


class PrefsIn(BaseModel):
    gps_consent: Optional[bool] = None
    last_job_id: Optional[int] = None


@router.put("/me/prefs")
def update_prefs(data: PrefsIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """화면 설정을 바로 저장: 위치 기록 동의, 마지막으로 보던 일하는 곳."""
    if data.gps_consent is not None:
        u.gps_consent = data.gps_consent
    if data.last_job_id is not None:
        own_job(s, u, data.last_job_id)
        u.last_job_id = data.last_job_id
    s.add(u)
    s.commit()
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
    s.refresh(job)  # 커밋 뒤 비워진 값을 다시 읽어야 model_dump가 채워진다
    return job_out(job)


@router.put("/jobs/{job_id}")
def update_job(job_id: int, data: JobIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    if s.exec(select(Job).where(Job.user_id == u.id, Job.name == data.name.strip(), Job.id != job_id)).first():
        raise HTTPException(400, f"{data.name} 이름이 이미 있어요. 지점명까지 적어 주세요")
    apply_job(job, data)
    s.add(job)
    s.commit()
    s.refresh(job)  # 커밋 뒤 비워진 값을 다시 읽어야 model_dump가 채워진다
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
    out = {"action": action, "server_time": t.isoformat(), "record": rec_out(rec)}
    if action == "out":
        out["shift"] = core.run_shift_check(s, u.id, job_id, rec.id)  # 퇴근한 순간 에이전트가 그날 기록을 점검
    return out


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
    items = attach_articles(s, json.loads(row.results_json)) if row else None
    return {"items": items, "created_at": row.created_at.isoformat() if row else None}


@router.get("/law/status")
def law_status(u: User = Depends(current_user), s: Session = Depends(get_session)):
    return table_status(s)


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
    return core.run_seek_check(s, u.id, data.model_dump())


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


@router.get("/jobs/{job_id}/payslips")
def list_payslips(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rows = s.exec(select(Payslip).where(Payslip.job_id == job_id).order_by(Payslip.month.desc(), Payslip.id.desc())).all()
    latest: dict[str, Payslip] = {}
    for p in rows:
        latest.setdefault(p.month, p)  # 같은 달을 다시 올리면 마지막 금액을 쓴다
    return [{"id": p.id, "month": p.month, "amount": p.amount, "evidence_id": p.evidence_id,
             "created_at": p.created_at.isoformat()} for p in latest.values()]


@router.delete("/jobs/{job_id}/payslips/{month}")
def delete_payslip(job_id: int, month: str, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """잘못 올린 받은 금액 지우기. 함께 올린 명세서 원본은 증거 자료로 남긴다."""
    own_job(s, u, job_id)
    for p in s.exec(select(Payslip).where(Payslip.job_id == job_id, Payslip.month == month)).all():
        s.delete(p)
    s.commit()
    return {"ok": True}


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
    rows = s.exec(select(AgentLog).where(AgentLog.user_id == u.id,
                                         (AgentLog.job_id == job_id) | (AgentLog.job_id == None))  # noqa: E711
                  .order_by(AgentLog.id.desc()).limit(60)).all()
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
    return core.run_report(s, u.id, job_id)


@router.get("/jobs/{job_id}/reports")
def list_reports(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    rows = s.exec(select(Report).where(Report.job_id == job_id, Report.user_id == u.id).order_by(Report.id.desc())).all()
    return [{"id": r.id, "url": f"/api/reports/{r.id}", "created_at": r.created_at.isoformat()} for r in rows]


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
    return {"reported": job.reported, "message": guard.current_message(job) if job.reported else "",
            "custom_message": bool(job.guard_message.strip()),
            "keywords": keywords_of(job), "queries": guard.search_queries(job, keywords_of(job)),
            "posts": [post_out(p) for p in posts]}


@router.post("/jobs/{job_id}/guard")
def set_reported(job_id: int, data: ReportedIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    run = core.run_guard_toggle(s, u.id, job_id, data.reported)
    return {**guard_state(job_id, u, s), "trace": run["trace"]}


class MessageIn(BaseModel):
    message: str


@router.put("/jobs/{job_id}/guard/message")
def set_message(job_id: int, data: MessageIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """고친 안내 문구 저장. 빈 문구를 보내면 기본 문구로 돌아간다."""
    job = own_job(s, u, job_id)
    job.guard_message = data.message.strip()[:2000]
    s.add(job)
    s.commit()
    return guard_state(job_id, u, s)


class KeywordsIn(BaseModel):
    keywords: list[str]


@router.put("/jobs/{job_id}/guard/keywords")
def set_keywords(job_id: int, data: KeywordsIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """게시물 검색어 (본인 이름, 별명 등). 사업장 이름과 함께 검색한다."""
    job = own_job(s, u, job_id)
    kws = [k.strip().replace(",", " ") for k in data.keywords if k.strip()][:10]
    job.guard_keywords = ",".join(dict.fromkeys(kws))
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
    return core.run_guard_preserve(s, u.id, job_id, data.url, data.title)


@router.post("/jobs/{job_id}/guard/search")
def guard_search(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    return core.run_guard_search(s, u.id, job_id)


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
