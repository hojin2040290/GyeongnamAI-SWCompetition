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
from app.calc import bizno
from app.calc.age import age_on
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.config import OPEN_RECORD_ALERT_HOURS, PUNCH_CONFIRM_SEC
from app.db import get_session
from app.law.lookup import attach_articles, attach_refs, table_status
from app.llm import client as llm_client
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
    biz_no: str = ""


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
    try:
        job.biz_no = bizno.normalize(data.biz_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
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
    return {"settlement": core.run_quit_check(s, u.id, job_id)}


class PaidIn(BaseModel):
    paid: bool


@router.post("/jobs/{job_id}/paid")
def set_paid(job_id: int, data: PaidIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    job.paid_after_quit = data.paid
    s.add(job)
    s.commit()
    return {"settlement": core.run_quit_check(s, u.id, job_id)}


def _saved_judgment(s: Session, job_id: int, kind: str, now: dict, same) -> dict:
    """코드가 방금 계산한 값에, 같은 사실로 저장된 AI 판단이 있으면 붙인다 (화면을 다시 열 때마다 AI를 부르지 않음)."""
    rows = s.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == kind)
                  .order_by(CheckRun.id.desc())).all()
    for row in rows:
        saved = json.loads(row.results_json)
        if same(saved):
            return saved
    return now


@router.get("/jobs/{job_id}/settlement")
def settlement(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """지급 기한은 코드가 다시 계산하고, 위반 판단은 저장된 에이전트 판단을 쓴다 (없으면 AI 응답 대기 중)."""
    own_job(s, u, job_id)
    st = make_tools(s, u.id, job_id)["settlement"]()
    if not st:
        return {"settlement": None}
    saved = _saved_judgment(s, job_id, "quit", st, lambda x: x.get("due") == st["due"]
                            and x.get("rule_status") == st["rule_status"] and x.get("left") == st["left"])
    return {"settlement": saved}


# ---------- 출퇴근 ----------
class PunchIn(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    confirm: bool = False  # 확인 질문에 '예'라고 답하고 다시 보낸 요청


def rec_out(r: WorkRecord) -> dict:
    end = r.clock_out or now_kst()
    return {"id": r.id, "clock_in": r.clock_in.isoformat(), "clock_out": r.clock_out.isoformat() if r.clock_out else None,
            "gps": r.in_lat is not None, "hours": round((end - r.clock_in).total_seconds() / 3600, 1),
            "void": r.void_at is not None, "void_at": r.void_at.isoformat() if r.void_at else None,
            "void_reason": r.void_reason}


def open_record(s: Session, job_id: int) -> WorkRecord | None:
    return s.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.clock_out == None,  # noqa: E711
                                           WorkRecord.void_at == None).order_by(WorkRecord.id.desc())).first()  # noqa: E711


def punch_warning(open_rec: WorkRecord, t) -> str:
    """퇴근을 누르기 전에 한 번 더 물어볼 상황이면 그 문구를 돌려준다."""
    sec = (t - open_rec.clock_in).total_seconds()
    if sec < PUNCH_CONFIRM_SEC:
        return "방금 출근했어요. 정말 퇴근할까요?"
    if sec >= OPEN_RECORD_ALERT_HOURS * 3600:
        return (f"출근한 지 {int(sec // 3600)}시간이 지났어요. 지금 퇴근으로 기록할까요? "
                "퇴근을 잊었던 거라면 취소하고 그 출근 기록을 실수로 표시해 주세요.")
    return ""


@router.post("/jobs/{job_id}/punch")
def punch(job_id: int, data: PunchIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """출근 또는 퇴근. 시각은 서버가 요청을 받은 순간으로 기록한다."""
    job = own_job(s, u, job_id)
    if job.status == "quit":
        raise HTTPException(400, "그만둔 사업장이에요")
    t = now_kst()
    open_rec = open_record(s, job_id)
    if open_rec and not data.confirm and (msg := punch_warning(open_rec, t)):
        raise HTTPException(409, msg)  # 화면이 확인을 받은 뒤 confirm=true로 다시 보낸다
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
    return {"working": any(r.clock_out is None and r.void_at is None for r in rows),
            "open_alert_hours": OPEN_RECORD_ALERT_HOURS, "records": [rec_out(r) for r in rows]}


class VoidIn(BaseModel):
    reason: str = ""


def own_record(s: Session, u: User, job_id: int, rec_id: int) -> WorkRecord:
    own_job(s, u, job_id)
    r = s.get(WorkRecord, rec_id)
    if not r or r.job_id != job_id or r.user_id != u.id:
        raise HTTPException(404, "근무 기록을 찾을 수 없어요")
    return r


@router.post("/jobs/{job_id}/records/{rec_id}/void")
def void_record(job_id: int, rec_id: int, data: VoidIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """실수로 누른 기록 표시. 시각은 그대로 두고, 표시한 시각과 이유를 함께 남긴다."""
    r = own_record(s, u, job_id, rec_id)
    r.void_at, r.void_reason = now_kst(), (data.reason.strip() or "실수로 누름")[:200]
    s.add(r)
    s.commit()
    return rec_out(r)


@router.delete("/jobs/{job_id}/records/{rec_id}/void")
def unvoid_record(job_id: int, rec_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """실수 표시 취소. 퇴근이 없는 기록인데 이미 다른 출근이 진행 중이면 취소할 수 없다."""
    r = own_record(s, u, job_id, rec_id)
    if r.clock_out is None and (cur := open_record(s, job_id)) and cur.id != r.id:
        raise HTTPException(400, "지금 출근 중인 기록이 있어 표시를 취소할 수 없어요. 먼저 퇴근해 주세요")
    r.void_at, r.void_reason = None, ""
    s.add(r)
    s.commit()
    return rec_out(r)


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
    # 비전 모델이 읽은 값은 화면에 채우기만 하고, 사용자가 확인한 뒤 저장한다 (연결 전이면 직접 입력)
    res = core.run_read_image(s, u.id, job_id, ev.id, "contract")
    fields = res.get("fields") or {k: "" for k in P()["written_terms"]["items"]}
    return {"evidence": ev_out(ev), "fields": fields, "ai": res.get("ai", False), "found": res.get("found", 0),
            "total": len(fields), "reason": res.get("reason", ""), "trace": res["trace"]}


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
    items = attach_refs(s, attach_articles(s, json.loads(row.results_json))) if row else None
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
    biz_no: str = ""


@router.post("/seek/check")
def seek_check(data: SeekIn, u: User = Depends(current_user), s: Session = Depends(get_session)):
    try:
        data.biz_no = bizno.normalize(data.biz_no)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return core.run_seek_check(s, u.id, data.model_dump())


# ---------- 급여 ----------
@router.post("/jobs/{job_id}/payslip")
async def add_payslip(job_id: int, month: str = Form(...), amount: int = Form(...), file: Optional[UploadFile] = File(None),
                      evidence_id: Optional[int] = Form(None),
                      u: User = Depends(current_user), s: Session = Depends(get_session)):
    own_job(s, u, job_id)
    ev_id = None
    if file is not None and file.filename:
        ev_id = (await store_upload(s, u, job_id, "payslip", file, f"{month} 급여")).id
    elif evidence_id:  # 먼저 올려 AI가 읽은 명세서
        ev = s.get(Evidence, evidence_id)
        if not ev or ev.user_id != u.id or ev.job_id != job_id:
            raise HTTPException(404, "명세서 자료를 찾을 수 없어요")
        ev_id = ev.id
    s.add(Payslip(job_id=job_id, month=month, amount=amount, evidence_id=ev_id, created_at=now_kst()))
    s.commit()
    return core.run_payday(s, u.id, job_id, month)


@router.post("/jobs/{job_id}/payslip/read")
async def read_payslip(job_id: int, file: UploadFile = File(...), u: User = Depends(current_user),
                       s: Session = Depends(get_session)):
    """명세서 사진을 원본으로 저장하고 AI가 읽는다. 읽은 금액은 화면에 채우기만 하고 저장은 사용자가 한다."""
    own_job(s, u, job_id)
    ev = await store_upload(s, u, job_id, "payslip", file, "급여명세서")
    res = core.run_read_image(s, u.id, job_id, ev.id, "payslip")
    return {**res, "evidence_id": ev.id}


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
    now = {"month": month, "expected": exp, "paid": paid, "compare": t["compare_pay"](exp, paid)}
    return _saved_judgment(s, job_id, "payday", now, lambda x: x.get("month") == month and x.get("paid") == paid
                           and x.get("expected") == json.loads(json.dumps(exp, default=str)))


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
            "ai_reason": p.ai_reason, "evidence_id": p.evidence_id, "found_at": p.found_at.isoformat()}


@router.get("/jobs/{job_id}/guard")
def guard_state(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    job = own_job(s, u, job_id)
    posts = s.exec(select(GuardPost).where(GuardPost.job_id == job_id).order_by(GuardPost.id.desc())).all()
    return {"reported": job.reported, "message": guard.current_message(job) if job.reported else "",
            "custom_message": bool(job.guard_message.strip()), "message_source": guard.message_source(job),
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
    """고친 안내 문구 저장. 빈 문구를 보내면 AI가 쓴 문구(없으면 기본 문구)로 돌아간다."""
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


# ---------- 사건 진행 상황과 에이전트 조언 ----------
@router.get("/jobs/{job_id}/case")
def case_state(job_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    from app.agent import case
    job = own_job(s, u, job_id)
    adv = case.latest_advice(s, job_id)
    return {"progress": case.progress(s, job), "memory": case.memories(s, job_id),
            "advice": {"text": adv.text, "next_tab": adv.next_tab, "event": adv.event,
                       "created_at": adv.created_at.isoformat()} if adv else None}


# ---------- 에이전트 진행 상황 (화면에 단계별로 보여 주기) ----------
@router.get("/agent/last")
def agent_last(u: User = Depends(current_user), s: Session = Depends(get_session)):
    """지금까지 남은 이 사용자의 마지막 동작 기록 번호. 이 뒤의 기록이 새로 시작한 에이전트의 단계다."""
    row = s.exec(select(AgentLog).where(AgentLog.user_id == u.id).order_by(AgentLog.id.desc())).first()
    return {"id": row.id if row else 0}


@router.get("/agent/live")
def agent_live(after: int = 0, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """after 번호 뒤에 새로 남은 동작 기록 (에이전트가 일하는 동안 화면이 불러 간다)."""
    rows = s.exec(select(AgentLog).where(AgentLog.user_id == u.id, AgentLog.id > after)
                  .order_by(AgentLog.id).limit(50)).all()
    return [{"id": r.id, "event": r.event, "step": r.step, "detail": r.detail[:300]} for r in rows]


# ---------- AI 연결 상태 ----------
@router.get("/ai/status")
def ai_status(u: User = Depends(current_user)):
    return llm_client.status()


# ---------- 알림 ----------
def _notes(s: Session, u: User, job_id: Optional[int]):
    """이 사업장 알림과 사업장과 상관없는 알림 (job_id가 없으면 전체)."""
    q = select(Notification).where(Notification.user_id == u.id)
    if job_id is not None:
        q = q.where((Notification.job_id == job_id) | (Notification.job_id == None))  # noqa: E711
    return q


@router.get("/notifications")
def notifications(job_id: Optional[int] = None, u: User = Depends(current_user), s: Session = Depends(get_session)):
    rows = s.exec(_notes(s, u, job_id).order_by(Notification.id.desc()).limit(20)).all()
    return [{"id": n.id, "job_id": n.job_id, "title": n.title, "body": n.body, "read": n.read,
             "at": n.created_at.isoformat()} for n in rows]


@router.post("/notifications/read")
def read_all(job_id: Optional[int] = None, u: User = Depends(current_user), s: Session = Depends(get_session)):
    for n in s.exec(_notes(s, u, job_id).where(Notification.read == False)):  # noqa: E712
        n.read = True
        s.add(n)
    s.commit()
    return {"ok": True}


@router.delete("/notifications/{note_id}")
def delete_note(note_id: int, u: User = Depends(current_user), s: Session = Depends(get_session)):
    n = s.get(Notification, note_id)
    if not n or n.user_id != u.id:
        raise HTTPException(404, "알림을 찾을 수 없어요")
    s.delete(n)
    s.commit()
    return {"ok": True}


@router.delete("/notifications")
def clear_notes(job_id: Optional[int] = None, u: User = Depends(current_user), s: Session = Depends(get_session)):
    """알림 모두 지우기 (알림만 지우고 점검 결과와 동작 기록은 남는다)."""
    rows = s.exec(_notes(s, u, job_id)).all()
    for n in rows:
        s.delete(n)
    s.commit()
    return {"deleted": len(rows)}
