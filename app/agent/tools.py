"""에이전트가 쓰는 도구.

사용자 번호(user_id)와 사업장 번호(job_id)는 코드가 미리 고정한다.
AI는 이 값을 입력하지 않으므로 다른 사용자의 기록에 접근할 수 없다.
- make_tools(): 코드가 쓰는 함수 (AI 응답이 없을 때의 고정 순서도 이것을 쓴다)
- agent_tools(): AI가 골라 쓰는 도구와 입력 모양 (숫자는 계산 도구만 만든다)
"""
import json
from datetime import date, timedelta

from sqlmodel import Session, select

from app.agent.loop import Tool
from app.calc import pay as paycalc
from app.calc import records as reccalc
from app.calc import schedule as sch
from app.calc.age import age_on
from app.calc.params import P
from app.calc.timeutil import now_kst, today_kst
from app.judge import engine
from app.law.lookup import article_info, attach_articles, attach_refs, known_law, refs_for
from app import ocr, storage
from app.llm import client
from app.models import CheckRun, ContractFields, Evidence, GuardPost, Job, Notification, Payslip, User, WorkRecord

NOTIFY_DEDUP_HOURS = 24  # 같은 알림을 다시 보내지 않는 시간
POST_STATUS = ("suspect", "ok", "unclear")  # 보복 의심, 문제 없음, 확인 필요


def facts_from_job(user: User, job: Job, fields: dict | None, on: date | None = None) -> engine.Facts:
    return engine.Facts(
        birth=user.birth_date, on=on or today_kst(), wage=job.wage, probation=job.probation,
        probation_months=job.probation_months, start_date=job.start_date, end_date=job.end_date,
        no_end=job.no_end, schedule=sch.parse(job.schedule_json), size=job.size, industry=job.industry,
        work_desc=job.work_desc, contract_written=job.contract_written, copy_received=job.copy_received,
        consent=job.consent, contract_fields=fields or {}, biz_no=job.biz_no or "",
    )


def keywords_of(job: Job) -> list[str]:
    return [k.strip() for k in (job.guard_keywords or "").split(",") if k.strip()]


def for_ai(items: list[dict]) -> list[dict]:
    """검토 항목을 AI에게 넘길 모양으로. 규칙 결과(rule_status)는 검증 장치용이라 넘기지 않는다."""
    return [{"i": i, "조항": it["law"], "검토 내용": it["text"], "사실": it.get("basis", []),
             "부족한 정보": it.get("needed", []), "자료": "근무 기록" if it.get("source") == "records" else "입력 정보"}
            for i, it in enumerate(items)]


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

    # ----- 법 기준 대조 (검토할 항목과 사실. 결과 판단은 AI) -----
    def judge_job() -> list[dict]:
        """입력한 기본 정보와 계약서 내용을 법 기준과 대조."""
        user, job = get_user(), get_job()
        return engine.to_json(engine.judge(facts_from_job(user, job, get_contract_fields()), "contract"))

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

    def wait_ai(items: list[dict], error: str = "") -> list[dict]:
        """AI 판단 전 상태: 규칙 결과는 검증 장치용으로 숨기고 '확인 중(AI 응답 대기 중)'으로 둔다."""
        items = engine.await_ai(items)
        if error:
            for it in items:
                if it["status"] == engine.PENDING:
                    it["ai_error"] = error
        return items

    def apply_judgments(items: list[dict], judgments: list) -> list[dict]:
        """AI 판단을 항목에 붙이고 검증 장치로 다시 확인한다.
        근거 조항이 법 기준표에 없거나 근거 사실이 없는 판단은 받지 않는다 (그 항목은 대기로 남음)."""
        for j in judgments if isinstance(judgments, list) else []:
            i = j.get("i") if isinstance(j, dict) else None
            if not (isinstance(i, int) and 0 <= i < len(items)) or j.get("status") not in (engine.OK, engine.WARN, engine.BAD):
                continue
            it, law, fact = items[i], str(j.get("law", "")).strip(), str(j.get("fact", "")).strip()
            label_ok = law == it["law"] or known_law(session, law)
            if not label_ok or not fact:
                it["ai_error"] = (f"AI가 댄 근거 조항 '{law}'이 법 기준표에 없어 판단을 받지 않았어요" if not label_ok
                                  else "AI가 근거 사실을 내지 않아 판단을 받지 않았어요")
                continue
            it["status"], it["ai_reason"] = j["status"], str(j.get("reason", ""))[:300]
            it["ai_law"], it["ai_fact"] = law, fact[:300]
            it.pop("ai_error", None)
        for it in items:
            if it["status"] == engine.PENDING and "ai_error" not in it:
                it["ai_error"] = "AI가 이 항목을 판단하지 않았어요"
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

    # ----- 급여, 퇴직 (계산은 코드) -----
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
            return {"status": engine.WARN, "text": expected["error"], "needed": ["시급"]}
        if paid is None:
            return {"status": engine.WARN, "text": "명세서나 받은 금액이 아직 없어요. 올리면 비교해 드려요.", "needed": ["받은 금액"]}
        if not expected.get("work_min"):
            return {"status": engine.WARN, "diff": -paid, "needed": ["근무 기록"],
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
                "short": diff > tol, "text": fact}

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
        title, body = str(title).strip()[:100], str(body).strip()[:500]
        if not title or not body:
            raise ValueError("알림 제목과 내용이 필요해요")
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
        age = age_on(get_user().birth_date, today_kst())
        return [c for c in P()["counsel"] if c["min_age"] <= age <= c["max_age"]]

    def build_report(summary: dict | None = None) -> dict:
        """상담 사전 자료 문서 만들기. summary는 AI가 쓴 사건 요약 (없으면 'AI 응답 대기 중')."""
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

    def search_posts() -> dict:
        from app import guard
        job = get_job()
        return guard.search_public_posts(session, job, keywords_of(job))

    def preserve_post(url: str, title: str = "") -> dict:
        from app import guard
        return guard.preserve(session, user_id, get_job(), url, title)

    def pending_posts() -> int:
        return len(session.exec(select(GuardPost).where(GuardPost.job_id == job_id, GuardPost.status == "pending")).all())

    return {
        "get_user": get_user, "get_job": get_job, "get_contract_fields": get_contract_fields,
        "get_records": get_records, "get_record": get_record, "find_open_record": find_open_record,
        "judge_job": judge_job, "judge_records": judge_records, "judge_shift": judge_shift, "judge_seek": judge_seek,
        "attach_law": attach_law, "wait_ai": wait_ai, "apply_judgments": apply_judgments,
        "calc_pay": calc_pay, "get_payslip": get_payslip, "compare_pay": compare_pay, "settlement": settlement,
        "save_check": save_check, "notify": notify, "counsel_for_age": counsel_for_age, "build_report": build_report,
        "set_reported": set_reported, "warning_message": warning_message, "search_posts": search_posts,
        "preserve_post": preserve_post, "pending_posts": pending_posts,
        "read_contract_image": read_contract_image, "read_payslip_image": read_payslip_image,
    }


# ---------- AI가 골라 쓰는 도구 ----------
S = {"type": "string"}
MONTH = {"type": "string", "description": "YYYY-MM 형식의 달"}
LAW_LABEL = {"type": "string", "description": "조항 이름 (예: 근로기준법 제70조)"}
LAW_LIST = {"type": "array", "items": {"type": "string"}, "description": "근거로 든 조항 이름들"}


def agent_tools(session: Session, user_id: int, job_id: int | None, state: dict) -> dict[str, Tool]:
    """AI에게 보여 주는 도구. state에는 이번 실행에서 코드가 계산한 결과(급여 비교, 저장한 문구 등)를 남긴다."""
    t = make_tools(session, user_id, job_id)

    def get_profile() -> dict:
        user = t["get_user"]()
        out = {"오늘": today_kst().isoformat(), "만 나이(오늘)": age_on(user.birth_date, today_kst())}
        if job_id is None:
            return out
        job = t["get_job"]()
        return {**out, "사업장": job.name, "업종": job.industry, "하는 일": job.work_desc, "사업주": job.owner or "모름",
                "시급": job.wage, "사업장 인원": job.size, "수습": job.probation, "수습 개월": job.probation_months,
                "근무 시작": job.start_date, "계약 끝": job.end_date, "기간 정함 없음": job.no_end,
                "계약상 근무": sch.parse(job.schedule_json), "계약서 작성": job.contract_written,
                "사본 받음": job.copy_received, "보호자 서류": job.consent or "모름", "월급날": job.payday,
                "상태": "그만둠" if job.status == "quit" else "일하는 중", "그만둔 날": job.quit_date,
                "신고함": job.reported}

    def get_contract() -> dict:
        return t["get_contract_fields"]() or {"안내": "확인한 계약서 내용이 없어요"}

    def list_evidence() -> list[dict]:
        rows = session.exec(select(Evidence).where(Evidence.user_id == user_id, Evidence.job_id == job_id)
                            .order_by(Evidence.uploaded_at)).all()
        return [{"id": e.id, "종류": e.kind, "파일": e.filename, "올린 시각": e.uploaded_at, "SHA-256": e.sha256}
                for e in rows]

    def get_age_on(day: str) -> dict:
        return {"날짜": day, "만 나이": age_on(t["get_user"]().birth_date, date.fromisoformat(day))}

    def calc_work_days(month: str = "") -> list[dict]:
        """근무일별 근로시간, 야간 근로 분, 그날의 만 나이 (코드 계산)."""
        user, job = t["get_user"](), t["get_job"]()
        minor = P()["minor"]
        facts = reccalc.day_facts(t["get_records"](), sch.parse(job.schedule_json), user.birth_date,
                                  minor["night_start"], minor["night_end"])
        return [{"날짜": f.day, "출근": f"{f.clock_in:%H:%M}", "퇴근": f"{f.clock_out:%H:%M}", "만 나이": f.age,
                 "머문 분": f.span_min, "계약상 쉬는 분": f.break_min, "근로 분": f.work_min, "야간 분": f.night_min}
                for f in facts if not month or f.day.isoformat().startswith(month)][-40:]

    def calc_pay(month: str) -> dict:
        return t["calc_pay"](month)

    def get_payslip(month: str) -> dict:
        return {"달": month, "받은 금액": t["get_payslip"](month)}

    def compare_pay(month: str) -> dict:
        """계산한 금액과 받은 금액 비교 (숫자만). 결과는 이번 실행의 급여 비교로 남는다."""
        expected, paid = t["calc_pay"](month), t["get_payslip"](month)
        cmp = t["compare_pay"](expected, paid)
        state["pay"] = {"month": month, "expected": expected, "paid": paid, "compare": cmp}
        return {"달": month, "계산한 금액": expected.get("total"), "받은 금액": paid, "차이": cmp.get("diff"),
                "사실": cmp["text"], "부족한 정보": cmp.get("needed", [])}

    def settlement() -> dict:
        st = t["settlement"]()
        state["settlement"] = st
        if not st:
            return {"안내": "그만둔 사업장이 아니에요"}
        return {"그만둔 날": st["quit_date"], "지급 기한": st["due"], "남은 날": st["left"],
                "받았다고 기록함": st["rule_status"] == engine.OK}

    def get_article(label: str) -> dict:
        return article_info(session, label)

    def find_refs(label: str) -> list[dict]:
        return refs_for(session, label)

    def get_saved_checks() -> dict:
        """저장된 계약서 점검 결과와 급여 비교."""
        check = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.user_id == user_id,
                                                    CheckRun.kind == "contract").order_by(CheckRun.id.desc())).first()
        pays = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.user_id == user_id,
                                                   CheckRun.kind == "payday").order_by(CheckRun.id.desc())).all()
        items = [{"조항": it["law"], "결과": it["status"], "내용": it["text"], "사실": it.get("basis", []),
                  "AI 근거": it.get("ai_reason", "")} for it in (json.loads(check.results_json) if check else [])]
        pay = [{"달": d["month"], "계산한 금액": d["expected"].get("total"), "받은 금액": d["paid"],
                "비교": d["compare"]["text"], "결과": d["compare"]["status"]}
               for d in (json.loads(p.results_json) for p in pays)]
        return {"계약서 점검": items or "아직 없음", "급여 비교": pay or "아직 없음"}

    def notify(title: str, body: str) -> str:
        return t["notify"](title, body)

    def save_warning_message(message: str, laws: list) -> str:
        """보복 금지 안내 문구 저장. 근거 조항은 법 기준표에 있는 것만 받는다."""
        message = str(message).strip()
        bad = [x for x in laws or [] if not known_law(session, str(x))]
        if not message:
            raise ValueError("안내 문구가 비어 있어요")
        if not laws or bad:
            raise ValueError(f"근거 조항이 법 기준표에 없어요: {', '.join(map(str, bad)) or '근거 조항 없음'}")
        job = t["get_job"]()
        job.guard_ai_message = message[:2000]
        session.add(job)
        session.commit()
        state["message"] = True
        return "저장함"

    def search_posts() -> dict:
        return t["search_posts"]()

    def list_posts(pending_only: bool = True) -> list[dict]:
        t["get_job"]()
        q = select(GuardPost).where(GuardPost.job_id == job_id)
        if pending_only:
            q = q.where(GuardPost.status == "pending")
        return [{"post_id": p.id, "제목": p.title, "주소": p.url, "내용": p.snippet[:1000], "상태": p.status,
                 "찾은 방법": "검색" if p.source == "search" else "사용자가 보존"}
                for p in session.exec(q.order_by(GuardPost.id)).all()][:20]

    def set_post_status(post_id: int, status: str, reason: str) -> str:
        """게시물 판별 저장: suspect(보복 의심), ok(문제 없음), unclear(확인 필요)."""
        t["get_job"]()
        post = session.get(GuardPost, post_id)
        if not post or post.job_id != job_id:
            raise PermissionError("이 사업장의 게시물이 아니에요")
        if status not in POST_STATUS or not str(reason).strip():
            raise ValueError("status는 suspect, ok, unclear 중 하나이고 판별 근거가 필요해요")
        post.status, post.ai_reason = status, str(reason).strip()[:300]
        session.add(post)
        session.commit()
        state.setdefault("posts", []).append({"post_id": post_id, "status": status})
        return "저장함"

    def build_report(summary: str, points: list, basis: list) -> dict:
        """상담 사전 자료 만들기. 사건 요약과 상담 때 물어볼 점을 넣는다."""
        bad = [x for x in basis or [] if not known_law(session, str(x))]
        if not str(summary).strip():
            raise ValueError("사건 요약이 비어 있어요")
        if bad:
            raise ValueError(f"근거 조항이 법 기준표에 없어요: {', '.join(map(str, bad))}")
        rep = t["build_report"]({"ai": True, "summary": str(summary)[:2000],
                                 "points": [str(x)[:300] for x in points or []][:8],
                                 "basis": [str(x)[:100] for x in basis or []][:8]})
        state["report"] = rep
        return rep

    def counsel_for_age() -> list[dict]:
        return t["counsel_for_age"]()

    tools = [
        Tool("get_profile", "오늘 날짜, 사용자의 오늘 만 나이, 사업장 기본 정보(시급, 계약상 근무, 계약서 작성 등)를 본다.",
             get_profile),
        Tool("get_contract", "사용자가 확인한 근로계약서 항목 내용을 본다.", get_contract),
        Tool("list_evidence", "보관한 증거 자료 목록(종류, 올린 시각, SHA-256)을 본다.", list_evidence),
        Tool("get_age_on", "특정 날짜의 만 나이를 계산한다.", get_age_on,
             {"day": {"type": "string", "description": "YYYY-MM-DD"}}, ["day"]),
        Tool("calc_work_days", "실제 출퇴근 기록으로 근무일별 근로시간, 야간 근로 시간, 그날의 만 나이를 계산한다.",
             calc_work_days, {"month": {"type": "string", "description": "YYYY-MM (비우면 전체)"}}),
        Tool("calc_pay", "그 달 받아야 할 임금을 계산한다 (기본급, 주휴수당, 가산수당).", calc_pay, {"month": MONTH}, ["month"]),
        Tool("get_payslip", "그 달 받은 금액(명세서나 입금 기록)을 본다.", get_payslip, {"month": MONTH}, ["month"]),
        Tool("compare_pay", "그 달 계산한 임금과 받은 금액의 차이를 계산한다.", compare_pay, {"month": MONTH}, ["month"]),
        Tool("settlement", "그만둔 뒤 임금 지급 기한과 남은 날을 계산한다.", settlement),
        Tool("get_article", "법 기준표에서 조문 원문을 찾는다.", get_article, {"label": LAW_LABEL}, ["label"]),
        Tool("find_refs", "조항 주제에 맞는 판례, 해석례, 결정문을 찾는다.", find_refs, {"label": LAW_LABEL}, ["label"]),
        Tool("get_saved_checks", "저장된 계약서 점검 결과와 급여 비교 결과를 본다.", get_saved_checks),
        Tool("counsel_for_age", "사용자 나이에 맞는 상담 기관을 찾는다.", counsel_for_age),
        Tool("notify", "사용자에게 웹 알림을 보낸다.", notify, {"title": S, "body": S}, ["title", "body"]),
        Tool("save_warning_message", "사업주에게 보낼 보복 금지 안내 문구를 저장한다.", save_warning_message,
             {"message": S, "laws": LAW_LIST}, ["message", "laws"]),
        Tool("search_posts", "사업장 이름과 검색어로 공개 게시물을 검색해 새 게시물을 보존한다.", search_posts),
        Tool("list_posts", "보존한 게시물(제목, 주소, 내용 일부)을 본다.", list_posts,
             {"pending_only": {"type": "boolean", "description": "판별 전 게시물만 볼지"}}),
        Tool("set_post_status", "게시물 판별 결과를 저장한다. suspect(보복 의심), ok(문제 없음), unclear(확인 필요).",
             set_post_status, {"post_id": {"type": "integer"}, "status": {"type": "string", "enum": list(POST_STATUS)},
                               "reason": S}, ["post_id", "status", "reason"]),
        Tool("build_report", "상담 사전 자료 문서를 만든다. 사건 요약, 상담 때 물어볼 점, 근거 조항을 넣는다.", build_report,
             {"summary": S, "points": {"type": "array", "items": S}, "basis": LAW_LIST},
             ["summary", "points", "basis"]),
    ]
    return {x.name: x for x in tools}
