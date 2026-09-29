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
    gps_consent: bool = False  # 출퇴근 때 위치를 함께 기록하는 데 동의했는지
    last_job_id: Optional[int] = None  # 마지막으로 보던 일하는 곳 (다시 열면 이곳부터)
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
    biz_no: str = ""  # 사업자등록번호 (000-00-00000, 모르면 빈 값)
    reported: bool = False
    guard_keywords: str = ""  # 게시물 검색어 (쉼표로 구분, 예: 본인 이름, 별명)
    guard_message: str = ""  # 사용자가 고친 보복 금지 안내 문구 (비어 있으면 기본 문구)
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
    # 실수로 누른 기록: 시각은 고치거나 지우지 않고 표시만 한다 (급여 계산과 점검에서 제외)
    void_at: Optional[datetime] = None
    void_reason: str = ""


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


class LawSource(SQLModel, table=True):
    """법 기준표를 만든 법령의 판 정보. 매일 현행 판이 바뀌었는지 비교한다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    law_name: str = Field(index=True, unique=True)
    law_id: str
    mst: str  # 법령일련번호 (판마다 다름)
    enforce_date: str = ""
    promul_no: str = ""
    fetched_at: datetime


class LawValue(SQLModel, table=True):
    """고시 원문에서 가져온 기준값 (예: 연도별 최저임금). law_params.json보다 우선한다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True)  # min_wage
    year: int
    value: int
    source: str  # 예: 2026년 적용 최저임금 고시 (고용노동부 고시 제2025-00호)
    fetched_at: datetime


class LawDoc(SQLModel, table=True):
    """판례, 법제처 해석례, 고용노동부 해석, 노동위원회 결정문 (점검 주제별로 미리 받아 둔 참고 자료)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # prec, expc, moelCgmExpc, nlrc
    doc_id: str
    topic: str = Field(index=True)
    title: str
    number: str = ""
    date: str = ""
    summary: str = ""
    fields_json: str = "{}"
    fetched_at: datetime
