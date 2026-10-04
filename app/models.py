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
    guard_message: str = ""  # 사용자가 고친 보복 금지 안내 문구 (비어 있으면 AI 문구, 그것도 없으면 기본 문구)
    guard_ai_message: str = ""  # AI가 작성한 보복 금지 안내 문구 (AI 응답이 없으면 빈 값)
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
    topic: str = ""  # 알림 종류 (같은 종류의 새 알림이 오면 예전 알림을 지운다, app/notices.py)
    run_id: str = ""  # 보낸 에이전트 실행 (한 실행에서 보낸 알림끼리는 지우지 않는다)


class Report(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int
    path: str
    ai_summary: bool = False  # AI가 쓴 요약이 들어갔는지 (아니면 AI 응답 대기 중)
    created_at: datetime


class GuardPost(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    url: str
    title: str = ""
    source: str = "user"  # user, search
    status: str = "pending"  # pending(AI 응답 대기 중), suspect(보복 의심), ok(문제 없음)
    snippet: str = ""  # 판별에 쓰는 게시물 내용 일부 (검색 결과 요약이나 보존한 화면의 글자)
    ai_reason: str = ""  # AI가 판별한 근거
    evidence_id: Optional[int] = None
    found_at: datetime


class CaseNote(SQLModel, table=True):
    """에이전트 메모와 조언. 다음 실행 때 메모를 읽고, 조언은 홈에 보여 준다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int = Field(index=True)
    kind: str  # memory(에이전트 메모), advice(사용자에게 하는 조언)
    text: str
    next_tab: str = ""  # 조언의 바로 가기: check, pay, docs, guard (없으면 빈 값)
    basis_key: str = ""  # 매일 종합 조언이 본 기록의 요약값 (기록이 그대로면 다시 조언하지 않음)
    event: str = ""
    run_id: str = ""
    created_at: datetime


class AgentQuestion(SQLModel, table=True):
    """에이전트가 판단에 필요한 정보를 사용자에게 묻는 질문. 답하면 질문한 점검을 다시 시작한다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int = Field(index=True)
    event: str  # 질문한 일 (답하면 이 일을 다시 시작)
    run_id: str = ""
    question: str
    options_json: str = "[]"
    why: str = ""  # 왜 묻는지
    law: str = ""  # 관련 조항
    context_json: str = "{}"  # 다시 시작할 때 필요한 값 (예: 급여 점검의 달)
    status: str = "open"  # open(답 기다림), answered(답함), closed(닫음)
    answer: str = ""
    created_at: datetime
    answered_at: Optional[datetime] = None


class AgentTask(SQLModel, table=True):
    """에이전트가 스스로 예약한 후속 확인. 때가 되면 스케줄러가 그 점검을 다시 시작한다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: int = Field(index=True)
    kind: str  # contract_check, payday, quit_check, guard_review, report
    month: str = ""  # 급여 점검할 달
    note: str  # 왜 다시 확인하는지 (다시 시작할 때 에이전트에게 넘김)
    due_at: datetime
    status: str = "pending"  # pending(예약), done(실행함), cancelled(취소)
    event: str = ""  # 예약한 일
    run_id: str = ""
    created_at: datetime
    done_at: Optional[datetime] = None


class GuideState(SQLModel, table=True):
    """처음 쓰는 사람 체험 안내 (새로 가입한 계정만): 가입할 때 고른 상황과 미션 진행 (app/guide.py)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    mode: str  # seek(구하는 중), work(일하는 중), quit(그만둠)
    started_at: datetime  # 이 시각 뒤에 한 일만 미션으로 센다
    marks: str = "[]"  # 화면에서만 알 수 있는 일 (intro 첫 안내 봄, closed 완료 창 닫음, off 안내 끔)


class LoginAttempt(SQLModel, table=True):
    """로그인 실패 기록 (비밀번호 대입을 막기 위해 이메일, 접속 주소별로 센다). 하루 지난 기록은 지운다."""
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    ip: str = Field(index=True)
    at: datetime = Field(index=True)


class AgentLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    job_id: Optional[int] = None
    run_id: str
    event: str
    step: str
    detail: str
    created_at: datetime
    tags: str = ""  # 이 단계에서 AI가 고른 도구 이름들 (JSON 목록). 화면은 판단 글과 따로 태그로 보여 준다


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
