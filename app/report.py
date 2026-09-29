"""상담 사전 자료 만들기. 지금은 모인 기록을 정리한 문서이고, AI 연결 후 사건 요약을 AI가 덧붙인다."""
import html
import json
from pathlib import Path

from sqlmodel import Session, select

from app.agent.tools import make_tools
from app.calc import schedule as sch
from app.calc.age import age_on
from app.calc.timeutil import now_kst, today_kst
from app.config import REPORT_DIR
from app.law.lookup import article_info
from app.models import CheckRun, Evidence, Report, WorkRecord

LABEL = {"ok": "정상", "warn": "확인 필요", "bad": "위반 의심"}


def build(session: Session, user_id: int, job_id: int) -> Report:
    t = make_tools(session, user_id, job_id)
    user, job = t["get_user"](), t["get_job"]()
    e = html.escape
    check = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == "contract")
                         .order_by(CheckRun.id.desc())).first()
    pays = session.exec(select(CheckRun).where(CheckRun.job_id == job_id, CheckRun.kind == "payday")
                        .order_by(CheckRun.id.desc())).all()
    recs = session.exec(select(WorkRecord).where(WorkRecord.job_id == job_id).order_by(WorkRecord.clock_in)).all()
    evs = session.exec(select(Evidence).where(Evidence.job_id == job_id).order_by(Evidence.uploaded_at)).all()
    sched = sch.parse(job.schedule_json)
    settle = t["settlement"]()
    counsel = t["counsel_for_age"]()

    rows = []
    articles: dict[str, dict] = {}
    rows.append("<h2>1. 기본 정보</h2><table>")
    rows.append(f"<tr><th>만 나이</th><td>{age_on(user.birth_date, today_kst())}세</td></tr>")
    rows.append(f"<tr><th>사업장</th><td>{e(job.name)}</td></tr><tr><th>주소</th><td>{e(job.address or '미입력')}</td></tr>")
    rows.append(f"<tr><th>사업자등록번호</th><td>{e(job.biz_no or '미입력')}</td></tr>")
    rows.append(f"<tr><th>사업주</th><td>{e(job.owner or '미입력')}</td></tr><tr><th>업종, 하는 일</th><td>{e(job.industry)} / {e(job.work_desc)}</td></tr>")
    rows.append(f"<tr><th>근무 기간</th><td>{job.start_date or '미입력'} ~ {job.quit_date or job.end_date or '현재'}</td></tr>")
    rows.append(f"<tr><th>약속한 시급</th><td>{f'{job.wage:,}원' if job.wage else '미입력'}</td></tr>")
    sched_txt = ", ".join(f"{d} {s['start']}~{s['end']}(쉬는 시간 {s.get('brk','모름')})" for d, s in sched.items()) or "미입력"
    rows.append(f"<tr><th>계약상 근무</th><td>{e(sched_txt)}</td></tr>")
    pay_parts = [p for p in (job.pay_cycle, f"{job.payday}일" if job.payday else "", job.pay_method,
                             f"공제 {job.deduction or '모름'}") if p]
    rows.append(f"<tr><th>임금 지급</th><td>{e(', '.join(pay_parts))}</td></tr></table>")

    rows.append("<h2>2. 점검 결과</h2>")
    if check:
        rows.append("<table><tr><th>조항</th><th>결과</th><th>내용</th><th>근거</th><th>법 기준표</th></tr>")
        for it in json.loads(check.results_json):
            art = article_info(session, it["law"])  # 저장 뒤 법 기준표가 새로 구축됐을 수 있어 다시 찾는다
            articles.setdefault(it["law"], art)
            src = "근무 기록" if it.get("source") == "records" else "입력 정보"
            rows.append(f"<tr><td class='n'>{e(it['law'])}</td><td class='n'>{LABEL[it['status']]}</td><td>{e(it['text'])}</td>"
                        f"<td>{e(', '.join(it['basis']))}<br><span class='s'>{src} 기준</span></td>"
                        f"<td class='n'>{'조문 원문 첨부' if art['built'] else '법 기준표 미구축'}</td></tr>")
        rows.append(f"</table><p class='s'>점검 시각 {check.created_at}</p>")
    else:
        rows.append("<p>아직 계약 점검을 하지 않았어요.</p>")

    rows.append("<h2>3. 급여 비교</h2>")
    if pays:
        rows.append("<table><tr><th>월</th><th>계산한 금액</th><th>받은 금액</th><th>결과</th></tr>")
        for p in pays:
            d = json.loads(p.results_json)
            exp = d["expected"].get("total")
            exp_txt = f"{exp:,}원" if exp else "-"
            paid_txt = f"{d['paid']:,}원" if d["paid"] is not None else "미입력"
            rows.append(f"<tr><td>{d['month']}</td><td>{exp_txt}</td><td>{paid_txt}</td><td>{e(d['compare']['text'])}</td></tr>")
        rows.append("</table>")
    else:
        rows.append("<p>급여 점검 기록이 없어요.</p>")
    if settle:
        rows.append(f"<p>퇴직일 {settle['quit_date']}, 임금 지급 기한 {settle['due']} ({LABEL[settle['status']]})</p>")

    rows.append("<h2>4. 근무 기록</h2><table><tr><th>출근</th><th>퇴근</th><th>출근 위치</th><th>비고</th></tr>")
    for r in recs:
        loc = f"{r.in_lat:.5f}, {r.in_lng:.5f}" if r.in_lat is not None else "기록 안 함"
        note, cls = "", ""
        if r.void_at:
            note, cls = f"실수로 표시함 ({r.void_at}, 이유: {e(r.void_reason)}). 계산에서 제외", " class='v'"
        rows.append(f"<tr{cls}><td>{r.clock_in}</td><td>{r.clock_out or '미기록'}</td><td>{loc}</td><td>{note}</td></tr>")
    rows.append("</table><p class='s'>출퇴근 시각은 버튼을 누른 순간 서버가 받은 시각이에요. 실수로 누른 기록은 지우지 않고 "
                "표시한 시각과 이유를 함께 남겼어요.</p>")

    rows.append("<h2>5. 증거 자료</h2><table><tr><th>종류</th><th>파일</th><th>올린 시각</th><th>파일 고유값(SHA-256)</th></tr>")
    for ev in evs:
        rows.append(f"<tr><td>{e(ev.kind)}</td><td>{e(ev.filename)}</td><td>{ev.uploaded_at}</td><td class='h'>{ev.sha256}</td></tr>")
    rows.append("</table>")

    rows.append("<h2>6. 상담 기관</h2><ul>")
    for c in counsel:
        rows.append(f"<li>{e(c['name'])} {c['phone']} ({e(c['note'])})</li>")
    rows.append("</ul>")

    rows.append("<h2>7. 관련 조문 원문</h2>")
    built = {k: v for k, v in articles.items() if v["built"]}
    for label, art in built.items():
        rows.append(f"<h3>{e(label)} {e(art.get('title', ''))}</h3><pre>{e(art['text'])}</pre>"
                    f"<p class='s'>법제처 국가법령정보 API에서 불러온 시각 {art['fetched_at']}</p>")
    missing = [k for k, v in articles.items() if not v["built"]]
    if missing:
        rows.append(f"<p>법 기준표 미구축: {e(', '.join(missing))}. 법제처 API로 조문을 불러오면 원문이 함께 들어가요.</p>")
    if not articles:
        rows.append("<p>점검 결과가 없어 첨부할 조문이 없어요.</p>")
    rows.append("<p class='s'>이 자료는 법적 판단이 아닌 참고 자료예요.</p>")

    doc = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>상담 사전 자료</title>
<style>body{{font-family:sans-serif;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.6}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}th,td{{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top;font-size:14px}}
th{{background:#f2f4f8}}pre{{white-space:pre-wrap;background:#f7f8fb;padding:10px;font-size:13px}}.s{{color:#666;font-size:13px}}.h{{font-size:11px;word-break:break-all}}.n{{white-space:nowrap}}tr.v td{{color:#888}}tr.v td:nth-child(-n+2){{text-decoration:line-through}}</style></head>
<body><h1>상담 사전 자료</h1><p class="s">작성 시각 {now_kst()}</p>{''.join(rows)}</body></html>"""
    path = Path(REPORT_DIR) / f"report_{user_id}_{job_id}_{now_kst().strftime('%Y%m%d%H%M%S')}.html"
    path.write_text(doc, encoding="utf-8")
    rep = Report(user_id=user_id, job_id=job_id, path=str(path), created_at=now_kst())
    session.add(rep)
    session.commit()
    return rep
