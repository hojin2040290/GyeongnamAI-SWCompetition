"""에이전트가 쓰는 도구.

사용자 번호(user_id)와 사업장 번호(job_id)는 코드가 미리 고정한다.
AI(나중에 연결)는 이 값을 입력하지 않으므로 다른 사용자의 기록에 접근할 수 없다.
"""
import json
from datetime import date, timedelta

from sqlmodel import Session, select

from app.calc import pay as paycalc
from app.calc import schedule as sch
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.law.lookup import attach_articles, attach_refs
from app import ocr, storage
from app.llm import client
from app.models import CheckRun, ContractFields, Evidence, GuardPost, Job, Notification, Payslip, User, WorkRecord

NOTIFY_DEDUP_HOURS = 24  # 같은 알림을 다시 보내지 않는 시간


def facts_from_job(user: User, job: Job, fields: dict | None, on: date | None = None) -> engine.Facts:
    return engine.Facts(
        birth=user.birth_date, on=on or today_kst(), wage=job.wage, probation=job.probation,
        probation_months=job.probation_months, start_date=job.start_date, end_date=job.end_date,
        no_end=job.no_end, schedule=sch.parse(job.schedule_json), size=job.size, industry=job.industry,
        work_desc=job.work_desc, contract_written=job.contract_written, copy_received=job.copy_received,
        consent=job.consent, contract_fields=fields or {}, biz_no=job.biz_no or "",
    )


AI_JUDGE_SYSTEM = (
    "당신은 청소년 아르바이트 근로권익 점검을 돕는 보조자입니다. 법적 판단을 확정하지 않고 참고 의견만 냅니다. "
    "각 항목의 사실(근거)과 조문만 보고 해당 조항 위반이 의심되면 bad, 문제가 없으면 ok, "
    "판단에 필요한 정보가 없으면 warn으로 답하세요. 숫자를 새로 계산하지 말고 주어진 값만 쓰세요. "
    '설명 없이 JSON 배열 [{"i": 항목 번호, "status": "ok|warn|bad", "reason": "근거로 쓴 조항과 사실 한 문장"}]만 답하세요.')


def _judge_prompt(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items):
        art = it.get("article") or {}
        lines.append(json.dumps({"i": i, "조항": it["law"], "내용": it["text"], "사실": it.get("basis", []),
                                 "부족한 정보": it.get("needed", []), "조문": (art.get("text") or "")[:800]},
                                ensure_ascii=False))
    return "\n".join(lines)


AI_WAITING = "AI 응답 대기 중"

WARNING_SYSTEM = (
    "당신은 신고한 청소년 아르바이트생을 돕는 보조자입니다. 사업주에게 보낼 보복 금지 안내 문구를 씁니다. "
    "정중하고 차분하게, 위협이나 과장 없이 3~5문장으로 쓰세요. 주어진 조문만 근거로 들고, 조문에 없는 내용이나 "
    "주어지지 않은 사실(날짜, 금액, 이름)을 지어내지 마세요. "
    '설명 없이 JSON {"message": "안내 문구", "laws": ["근거로 든 조항"]}만 답하세요.')

POST_SYSTEM = (
    "당신은 신고한 청소년 아르바이트생을 돕는 보조자입니다. 공개 게시물이 신고한 근로자를 겨냥한 보복성 게시물"
    "(신상 공개, 비방, 취업 방해, 불리한 처우 예고 등)인지 판별합니다. 주어진 제목, 주소, 내용만 보고 판단하고 추측하지 마세요. "
    "보복성이 의심되면 suspect, 관련 없거나 문제없으면 ok, 내용이 부족해 판단할 수 없으면 unclear로 답하세요. "
    '설명 없이 JSON 배열 [{"i": 번호, "status": "suspect|ok|unclear", "reason": "근거로 쓴 게시물 내용과 조항 한 문장"}]만 답하세요.')

SUMMARY_SYSTEM = (
    "당신은 청소년 아르바이트생이 노동 상담 기관에 가져갈 상담 사전 자료의 사건 요약을 씁니다. "
    "주어진 기록만 쓰고, 숫자는 주어진 값을 그대로 옮기며 새로 계산하지 마세요. 기록에 없는 사실은 쓰지 말고 "
    "'확인 필요'라고 적으세요. 법적 결론을 단정하지 말고 '의심', '확인 필요'처럼 쓰세요. "
    '설명 없이 JSON {"summary": "사건 요약 4~6문장", "points": ["상담 때 물어볼 점"], '
    '"basis": ["요약에 근거로 쓴 조항"]}만 답하세요.')

POST_STATUS = {"suspect": "suspect", "ok": "ok", "unclear": "unclear"}


def keywords_of(job: Job) -> list[str]:
    return [k.strip() for k in (job.guard_keywords or "").split(",") if k.strip()]


def make_tools(session: Session, user_id: int, job_id: int | None):
    def get_user() -> User:
        return session.get(User, user_id)

    def get_job() -> Job:
        job = session.get(Job, job_id)
        if not job or job.user_id != user_id:
            raise PermissionError("다른 사용자의 사업장이에요")
        return job

    def get_contract_fields() -> dict:
        row = session.exec(select(ContractFields).where(ContractFields.job_id == job_id)
                           .order_by(ContractFields.id.desc())).first()
        return json.loads(row.fields_json) if row else {}

    def get_records() -> list[WorkRecord]:
        """계산과 점검에 쓰는 기록. 실수로 표시한 기록은 뺀다."""
        return list(session.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.user_id == user_id,
                                                          WorkRecord.void_at == None)  # noqa: E711
                                 .order_by(WorkRecord.clock_in)))

    def find_open_record() -> dict | None:
        """퇴근을 누르지 않은 채 오래된 출근 기록."""
        r = session.exec(select(WorkRecord).where(WorkRecord.job_id == job_id, WorkRecord.user_id == user_id,
                                                  WorkRecord.clock_out == None, WorkRecord.void_at == None)  # noqa: E711
                         .order_by(WorkRecord.id.desc())).first()
        if not r:
            return None
        hours = (now_kst() - r.clock_in).total_seconds() / 3600
        return {"id": r.id, "clock_in": r.clock_in.isoformat(), "hours": round(hours, 1)}

    def get_record(record_id: int) -> WorkRecord:
        r = session.get(WorkRecord, record_id)
        if not r or r.user_id != user_id or r.job_id != job_id:
            raise PermissionError("다른 사용자의 근무 기록이에요")
        return r

    # ----- 판단 -----
    def judge_job() -> list[dict]:
        """입력한 기본 정보와 계약서 내용을 법 기준과 대조."""
        user, job = get_user(), get_job()
        items = engine.judge(facts_from_job(user, job, get_contract_fields()), "contract")
        return engine.to_json(items)

    def judge_records() -> list[dict]:
        """실제 출퇴근 기록 전체를 법 기준과 대조."""
        user, job = get_user(), get_job()
        return engine.to_json(engine.judge_records(user.birth_date, get_records(), sch.parse(job.schedule_json)))

    def judge_shift(day: str) -> list[dict]:
        """퇴근한 날과 그 주의 기록만 대조."""
        user, job = get_user(), get_job()
        items = engine.judge_records(user.birth_date, get_records(), sch.parse(job.schedule_json),
                                     focus=date.fromisoformat(day))
        return engine.to_json(items)

    def judge_seek(data: dict) -> dict:
        """지원 전: 공고 조건을 법 기준과 대조하고 물어볼 질문을 만든다."""
        facts = engine.Facts(birth=get_user().birth_date, on=today_kst(), wage=data.get("wage"),
                             probation=data.get("probation") or "unknown", schedule=data.get("schedule") or {},
                             industry=data.get("industry", ""), work_desc=data.get("work_desc", ""))
        items = engine.judge(facts, "seek")
        return {"items": engine.to_json(items), "questions": engine.questions(facts, items)}

    def attach_law(items: list[dict]) -> list[dict]:
        """판단 결과마다 법 기준표의 조문 원문을 붙인다. 없으면 '법 기준표 미구축'."""
        return attach_refs(session, attach_articles(session, items))

    def ai_judge(items: list[dict]) -> list[dict]:
        """조항 해당 여부 판단 (AI). 연결 전이면 '확인 중'으로 두고, 연결 후에는 AI 판단을 검증 장치로 다시 확인한다."""
        items = engine.await_ai(items)
        if not client.available() or not items:
            return items
        try:
            answers = client.ask_json(AI_JUDGE_SYSTEM, _judge_prompt(items))
        except client.LLMError as exc:
            for it in items:
                it["ai_error"] = str(exc)
            return items
        for a in answers if isinstance(answers, list) else []:
            i = a.get("i") if isinstance(a, dict) else None
            if isinstance(i, int) and 0 <= i < len(items) and a.get("status") in (engine.OK, engine.WARN, engine.BAD):
                items[i]["status"], items[i]["ai_reason"] = a["status"], str(a.get("reason", ""))[:300]
                items[i]["ai_pending"] = False
        return engine.cross_check(items)

    def read_contract_image(evidence_id: int) -> dict:
        """계약서 사진 읽기 (비전 모델). 읽은 값은 사용자가 확인한 뒤 저장한다."""
        return _read_image(evidence_id, ocr.read_contract)

    def read_payslip_image(evidence_id: int) -> dict:
        """급여명세서 사진 읽기 (비전 모델)."""
        return _read_image(evidence_id, ocr.read_payslip)

    def _read_image(evidence_id: int, reader) -> dict:
        ev = session.get(Evidence, evidence_id)
        if not ev or ev.user_id != user_id:
            raise PermissionError("다른 사용자의 자료예요")
        mime = ocr.image_mime(ev.filename, None)
        if not mime:
            return {"ai": False, "reason": "사진 파일(png, jpg)만 읽을 수 있어요. 내용을 직접 입력해 주세요."}
        if not client.vision_available():
            return {"ai": False, "reason": "사진을 읽을 AI 모델이 아직 연결되지 않았어요. 내용을 직접 입력해 주세요."}
        try:
            return {"ai": True, **reader(storage.read(ev.stored_path), mime)}
        except client.LLMError as exc:
            return {"ai": False, "reason": f"{exc}. 내용을 직접 입력해 주세요."}

    # ----- 급여, 퇴직 -----
    def calc_pay(month: str) -> dict:
        user, job = get_user(), get_job()
        if not job.wage:
            return {"error": "시급이 등록되지 않아 계산할 수 없어요"}
        r = paycalc.calc_month(get_records(), sch.parse(job.schedule_json), job.wage, job.size, user.birth_date, month)
        return r.as_dict()

    def get_payslip(month: str):
        row = session.exec(select(Payslip).where(Payslip.job_id == job_id, Payslip.month == month)
                           .order_by(Payslip.id.desc())).first()
        return row.amount if row else None

    def compare_pay(expected: dict, paid) -> dict:
        """계산한 금액과 받은 금액의 차이 (숫자만). 체불인지 판단은 AI가 한다."""
        if "error" in expected:
            return {"status": engine.WARN, "text": expected["error"]}
        if paid is None:
            return {"status": engine.WARN, "text": "명세서나 받은 금액이 아직 없어요. 올리면 비교해 드려요."}
        if not expected.get("work_min"):
            return {"status": engine.WARN, "diff": -paid,
                    "text": f"이 달에 계산할 근무 기록이 없어 받은 금액 {paid:,}원과 비교하지 못했어요. "
                            "출퇴근 기록이 없거나 모두 실수로 표시됐는지 확인해 주세요."}
        diff = expected["total"] - paid
        tol = max(100, round(expected["total"] * 0.01))
        if diff > tol:
            fact = f"계산한 금액보다 {diff:,}원 적게 받았어요."
        elif diff < -tol:
            fact = f"계산한 금액보다 {-diff:,}원 더 받았어요. 계산에 빠진 근무나 수당이 있는지 확인해 보세요."
        else:
            fact = f"계산한 금액과 받은 금액의 차이가 {abs(diff):,}원이에요."
        return {"status": engine.PENDING, "rule_status": engine.BAD if diff > tol else engine.OK, "diff": diff,
                "short": diff > tol, "text": fact + " 체불인지는 AI 판단 전이에요."}

    def settlement() -> dict | None:
        job = get_job()
        if job.status != "quit" or not job.quit_date:
            return None
        st = paycalc.settlement_status(job.quit_date, today_kst(), job.paid_after_quit)
        st["rule_status"], st["status"] = st["status"], engine.PENDING  # 기한 계산은 코드, 위반 판단은 AI
        return st

    # ----- 기록, 알림 -----
    def save_check(kind: str, results) -> int:
        run = CheckRun(user_id=user_id, job_id=job_id, kind=kind, results_json=json.dumps(results, ensure_ascii=False),
                       created_at=now_kst())
        session.add(run)
        session.commit()
        return run.id

    def notify(title: str, body: str) -> str:
        """알림 보내기. 같은 알림이 하루 안에 이미 있으면 다시 보내지 않는다 (점검을 여러 번 눌러도 한 번만)."""
        since = now_kst() - timedelta(hours=NOTIFY_DEDUP_HOURS)
        dup = session.exec(select(Notification).where(
            Notification.user_id == user_id, Notification.job_id == job_id, Notification.title == title,
            Notification.body == body, Notification.created_at >= since)).first()
        if dup:
            return "같은 알림이 이미 있어 보내지 않음"
        session.add(Notification(user_id=user_id, job_id=job_id, title=title, body=body, created_at=now_kst()))
        session.commit()
        return "보냄"

    # ----- 상담 -----
    def counsel_for_age() -> list[dict]:
        from app.calc.age import age_on
        age = age_on(get_user().birth_date, today_kst())
        return [c for c in P()["counsel"] if c["min_age"] <= age <= c["max_age"]]

    def case_facts() -> dict:
        """사건 요약에 넘길 기록 (코드가 계산하고 저장한 값만)."""
        user, job = get_user(), get_job()
        from app.calc.age import age_on
        check = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == "contract")
                             .order_by(CheckRun.id.desc())).first()
        pays = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == "payday")
                            .order_by(CheckRun.id.desc())).all()
        items = [{"조항": it["law"], "결과": it["status"], "내용": it["text"], "사실": it.get("basis", []),
                  "AI 근거": it.get("ai_reason", "")} for it in (json.loads(check.results_json) if check else [])]
        pay = [{"월": d["month"], "계산한 금액": d["expected"].get("total"), "받은 금액": d["paid"],
                "비교": d["compare"]["text"]} for d in (json.loads(p.results_json) for p in pays)]
        return {"만 나이": age_on(user.birth_date, today_kst()), "사업장": job.name, "업종": job.industry,
                "하는 일": job.work_desc, "근무 시작": str(job.start_date or "미입력"),
                "그만둔 날": str(job.quit_date or ""), "약속한 시급": job.wage, "사업장 규모": job.size,
                "점검 결과": items, "급여 비교": pay, "퇴직 후 지급": settlement(), "신고함": job.reported}

    def summarize_case() -> dict:
        """상담 사전 자료의 사건 요약 (AI). AI 응답이 없으면 'AI 응답 대기 중'으로 둔다."""
        if not client.available():
            return {"ai": False, "reason": f"{AI_WAITING}: AI가 연결되면 사건 요약을 작성해요"}
        try:
            ans = client.ask_json(SUMMARY_SYSTEM, json.dumps(case_facts(), ensure_ascii=False, default=str))
        except client.LLMError as exc:
            return {"ai": False, "reason": f"{AI_WAITING}: {exc}"}
        if not isinstance(ans, dict) or not str(ans.get("summary", "")).strip():
            return {"ai": False, "reason": f"{AI_WAITING}: AI 답에 요약이 없어요"}
        return {"ai": True, "summary": str(ans["summary"])[:2000],
                "points": [str(x)[:300] for x in ans.get("points") or []][:8],
                "basis": [str(x)[:100] for x in ans.get("basis") or []][:8]}

    def build_report(summary: dict | None = None) -> dict:
        """상담 사전 자료 문서 만들기. 사건 요약은 AI가 쓴 것을 넣는다."""
        from app import report  # report가 이 모듈을 쓰므로 여기서 불러온다
        rep = report.build(session, user_id, job_id, summary)
        return {"id": rep.id, "url": f"/api/reports/{rep.id}"}

    # ----- 신고 후 보호 -----
    def set_reported(on: bool) -> bool:
        job = get_job()
        job.reported = on
        session.add(job)
        session.commit()
        return on

    def warning_message() -> str:
        from app import guard
        return guard.current_message(get_job())

    def write_warning_message() -> dict:
        """보복 금지 안내 문구 작성 (AI). 근거 조문은 법 기준표에서 가져온다. AI 응답이 없으면 기본 문구를 쓴다."""
        from app.law.lookup import article_info
        job = get_job()
        if not client.available():
            return {"ai": False, "reason": f"{AI_WAITING}: AI가 연결되면 상황에 맞는 문구를 작성해요. 지금은 기본 문구예요."}
        laws = [{"조항": label, "조문": (article_info(session, label).get("text") or "")[:800]}
                for label in P()["retaliation"]["laws"]]
        facts = {"사업주": job.owner or "사장", "사업장": job.name, "업종": job.industry, "근로자 만 나이": _age(),
                 "상황": "근로자가 노동관계법 위반으로 신고함", "근거 조문": laws}
        try:
            ans = client.ask_json(WARNING_SYSTEM, json.dumps(facts, ensure_ascii=False))
        except client.LLMError as exc:
            return {"ai": False, "reason": f"{AI_WAITING}: {exc}. 지금은 기본 문구예요."}
        msg = str(ans.get("message", "")).strip() if isinstance(ans, dict) else ""
        if not msg:
            return {"ai": False, "reason": f"{AI_WAITING}: AI 답에 문구가 없어요. 지금은 기본 문구예요."}
        job.guard_ai_message = msg[:2000]
        session.add(job)
        session.commit()
        return {"ai": True, "message": job.guard_ai_message, "laws": ans.get("laws") or []}

    def _age() -> int:
        from app.calc.age import age_on
        return age_on(get_user().birth_date, today_kst())

    def search_posts() -> dict:
        from app import guard
        job = get_job()
        return guard.search_public_posts(session, job, keywords_of(job))

    def preserve_post(url: str, title: str = "") -> dict:
        from app import guard
        return guard.preserve(session, user_id, get_job(), url, title)

    def classify_posts() -> dict:
        """보복성 게시물 판별 (AI). AI 응답이 없으면 'AI 응답 대기 중'으로 두고 증거만 보존한다."""
        pending = list(session.exec(select(GuardPost).where(GuardPost.job_id == job_id, GuardPost.status == "pending")
                                    .order_by(GuardPost.id)))[:20]
        if not pending:
            return {"judged": 0, "pending": 0}
        if not client.available():
            return {"judged": 0, "pending": len(pending), "reason": f"{AI_WAITING}: 판별하지 않고 증거만 보존했어요"}
        job = get_job()
        head = json.dumps({"사업장": job.name, "사업주": job.owner, "검색어(근로자 이름, 별명)": keywords_of(job),
                           "관련 조항": P()["retaliation"]["laws"]}, ensure_ascii=False)
        lines = [json.dumps({"i": i, "제목": p.title, "주소": p.url, "내용": p.snippet[:1000]}, ensure_ascii=False)
                 for i, p in enumerate(pending)]
        try:
            answers = client.ask_json(POST_SYSTEM, head + "\n" + "\n".join(lines))
        except client.LLMError as exc:
            return {"judged": 0, "pending": len(pending), "reason": f"{AI_WAITING}: {exc}"}
        judged = 0
        for a in answers if isinstance(answers, list) else []:
            i = a.get("i") if isinstance(a, dict) else None
            if isinstance(i, int) and 0 <= i < len(pending) and a.get("status") in POST_STATUS:
                pending[i].status, pending[i].ai_reason = POST_STATUS[a["status"]], str(a.get("reason", ""))[:300]
                session.add(pending[i])
                judged += 1
        session.commit()
        suspect = sum(p.status == "suspect" for p in pending)
        return {"judged": judged, "pending": len(pending) - judged, "suspect": suspect}

    return {
        "get_user": get_user, "get_job": get_job, "get_contract_fields": get_contract_fields,
        "get_records": get_records, "get_record": get_record, "find_open_record": find_open_record, "judge_job": judge_job, "judge_records": judge_records,
        "judge_shift": judge_shift, "judge_seek": judge_seek, "attach_law": attach_law, "calc_pay": calc_pay,
        "get_payslip": get_payslip, "compare_pay": compare_pay, "settlement": settlement, "save_check": save_check,
        "notify": notify, "counsel_for_age": counsel_for_age, "build_report": build_report,
        "set_reported": set_reported, "warning_message": warning_message, "search_posts": search_posts,
        "preserve_post": preserve_post, "classify_posts": classify_posts, "ai_judge": ai_judge,
        "write_warning_message": write_warning_message, "summarize_case": summarize_case,
        "read_contract_image": read_contract_image, "read_payslip_image": read_payslip_image,
    }
