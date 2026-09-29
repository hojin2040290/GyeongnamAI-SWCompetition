"""데이터베이스 표. 시각은 모두 한국 시간(KST) 기준으로 저장한다."""
from datetime import date
from typing import Optional

from pydantic import NaiveDatetime as datetime  # 한국 시각을 시간대 정보 없이 저장
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    birth_date: date
    mode: str = "work"  # seek, work, quit : 처음 고른 상황
    created_at: datetime


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    name: str
    status: str = "working"  # working, quit
    quit_date: Optional[date] = None
    paid_after_quit: Optional[bool] = None
    industry: str = ""
    work_desc: str = ""
    wage: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    no_end: bool = False
    contract_written: Optional[bool] = None
    copy_received: Optional[bool] = None
    probation: str = "unknown"  # yes, no, unknown
    probation_months: Optional[int] = None
    schedule_json: str = "{}"  # {"월": {"start":"18:00","end":"22:00","brk":"없음"}, ...}
    size: str = "unknown"  # 5+, lt5, unknown
    pay_cycle: str = ""
    payday: Optional[int] = None
    pay_method: str = ""
    deduction: str = ""
    consent: str = ""  # 냈어요, 안 냈어요, 모름
    address: str = ""
    owner: str = ""
    reported: bool = False
    created_at: datetime


class WorkRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int = Field(index=True, foreign_key="job.id")
    clock_in: datetime
    clock_out: Optional[datetime] = None
    in_lat: Optional[float] = None
    in_lng: Optional[float] = None
    out_lat: Optional[float] = None
    out_lng: Optional[float] = None


class Evidence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: Optional[int] = Field(default=None, index=True)
    kind: str  # contract, payslip, message, schedule, deposit, post, notice, other
    filename: str
    stored_path: str
    sha256: str
    size: int
    uploaded_at: datetime
    note: str = ""


class ContractFields(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    fields_json: str
    confirmed_at: datetime


class Payslip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    month: str  # YYYY-MM
    amount: int
    evidence_id: Optional[int] = None
    created_at: datetime


class CheckRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: Optional[int] = Field(default=None, index=True)
    kind: str  # contract, seek, payday, quit
    results_json: str
    created_at: datetime


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: Optional[int] = None
    title: str
    body: str
    read: bool = False
    created_at: datetime


class Report(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int
    path: str
    created_at: datetime


class GuardPost(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    url: str
    title: str = ""
    source: str = "user"  # user, search
    status: str = "pending"  # pending(판별 대기), suspect, ok
    evidence_id: Optional[int] = None
    found_at: datetime


class AgentLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: Optional[int] = None
    run_id: str
    event: str
    step: str
    detail: str
    created_at: datetime


class LawArticle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    law_name: str = Field(index=True)
    article_no: str
    title: str = ""
    text: str
    fetched_at: datetime
