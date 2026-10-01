"""시험 데이터 만들기 (가상 정보만 사용): 한 사용자의 알바 5개와 서류 사진을 data/test 아래에 만든다.

실행: python -m app.demo_db           → data/test 를 새로 만든다
      python -m app.demo_db --force   → data/test 가 이미 있으면 지우고 다시 만든다
시험 데이터로 서버 실행: .env에 TEST_DATA=true (평소 데이터 data/app.db, data/uploads는 그대로)
  TEST_DATA=true로 켰는데 data/test에 시험 데이터가 없으면 서버가 켜지면서 이 명령을 실행한다 (app/main.py)
로그인: test@example.com / test1234 (2009-05-20생, 만 17세)

알바마다 서류 사진(계약서, 급여명세서, 입금내역, 사장님 메시지)은 그 알바의 기록과 같은 값으로 그린 것이다.
  1번 알바는 테스트자료 폴더의 원본 사진과 근무기록 CSV, 2~5번은 테스트자료/알바5개 의 사진.
  사진 다시 그리기 (기록이나 계산이 바뀌었을 때, 개발자용):
    python -m app.demo_db --html /tmp/docs && node tests/ui/make_case_images.js /tmp/docs 테스트자료/알바5개
  사진 속 금액은 코드(app/calc/pay.py)가 계산한 값이고 manifest.json에 적어 둔다. 계산이 바뀌어 맞지 않으면 만들기를 멈춘다.
계정, 사업장, 사진은 앱의 API로 넣어(지금 코드와 같은 모양, 원본 그대로 저장) 근무 기록만 정해 둔 시각으로 넣는다.
"""
import argparse
import csv
import html
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "테스트자료"
CASE_DIR = MATERIAL / "알바5개"
EMAIL, PASSWORD, NAME, BIRTH = "test@example.com", "test1234", "홍길동", "2009-05-20"


def _day_shifts(days: list[str], start: str, end: str) -> list[tuple[str, str, str]]:
    return [(f"{d} {start}", f"{d} {end}", "") for d in days]


def _csv_shifts() -> list[tuple]:
    """1번 알바: 테스트자료/05_근무기록.csv (서버 시각 +09:00, GPS)."""
    rows = csv.DictReader((MATERIAL / "05_근무기록.csv").open(encoding="utf-8-sig"))
    return [(r["출근_서버시각"][:16].replace("T", " "), r["퇴근_서버시각"][:16].replace("T", " "), "",
             (float(r["gps_출근_위도"]), float(r["gps_출근_경도"]))) for r in rows]


# 알바 5개. job은 화면의 '일하는 곳' 입력값 그대로, docs는 (파일, 올릴 곳, 메모)
CASES = [
    {"key": "1", "title": "테스트자료 원본: 수습 감액(계약 6개월), 휴게시간 미기재, 청소년 야간, 5인 미만, 그만둠",
     "job": {"name": "행복편의점 도계점", "industry": "편의점", "work_desc": "편의점 계산, 상품 진열", "wage": 9288,
             "start_date": "2026-09-07", "end_date": "2027-03-06", "contract_written": True, "probation": "yes",
             "probation_months": 3, "size": "lt5", "pay_cycle": "월급", "payday": 10, "pay_method": "계좌 이체",
             "deduction": "없음", "consent": "모름", "address": "경상남도 창원시 의창구 도계동 312-7", "owner": "최민호",
             "schedule": {d: {"start": "17:00", "end": "22:30", "brk": "30분"} for d in ("월", "수", "금", "토")}},
     "shifts": "csv", "month": "2026-09", "paid": 557280, "quit": "2026-09-27",
     "docs": [("02_근로계약서.png", "contract", ""), ("06_급여명세서.png", "payslip", "9월분 명세서"),
              ("03_근무표.png", "schedule", "근무표 (휴게시간 19:30~20:00)"),
              ("04_사업주_메시지_캡처.png", "message", "수습 시급 통보, 22시 반 마감 지시, 그만둠"),
              ("07_입금내역.png", "deposit", "10/10 입금 557,280원")],
     "expect": ["최저임금: 위반 의심 (수습 감액은 1년 이상 계약일 때만, 이 계약은 6개월)", "휴게시간 기재: 위반 의심 (계약서 칸 비어 있음)",
                "청소년 야간근로: 위반 의심 (매일 22:00~22:31)", "가산수당: 해당 없음 (5인 미만)",
                "주휴수당: 명세서 0원 (주 20시간 개근)", "퇴직 정산: 기한 10/11, 받았는지 기록 전이라 확인 필요"]},
    {"key": "2", "title": "5인 이상, 야간·하루 한도 초과, 결근한 주, 기본급만 받음 (적게 받음)",
     "job": {"name": "가상분식 시험점", "industry": "음식점, 카페", "work_desc": "주문 받기, 서빙", "wage": 10320,
             "start_date": "2026-08-03", "end_date": "2027-02-02", "contract_written": True, "copy_received": True,
             "probation": "no", "size": "5+", "pay_cycle": "월급", "payday": 10, "pay_method": "계좌 이체",
             "deduction": "없음", "consent": "냈어요", "address": "경상남도 창원시 가상구 시험로 10", "owner": "박가상",
             "schedule": {"월": {"start": "17:00", "end": "22:00", "brk": "30분"},
                          "수": {"start": "17:00", "end": "22:00", "brk": "30분"},
                          "금": {"start": "17:00", "end": "22:00", "brk": "30분"},
                          "토": {"start": "10:00", "end": "16:00", "brk": "1시간"}}},
     "shifts": [("2026-08-03 17:00", "2026-08-03 22:00", "첫째 주: 계약한 요일 모두 출근 (주휴수당)"),
                ("2026-08-05 17:00", "2026-08-05 23:00", "23시 퇴근 (야간 1시간)"),
                ("2026-08-07 17:00", "2026-08-07 22:00", ""), ("2026-08-08 10:00", "2026-08-08 16:00", ""),
                ("2026-08-10 17:00", "2026-08-10 22:00", "둘째 주: 모두 출근 (주휴수당)"),
                ("2026-08-12 17:00", "2026-08-12 22:00", ""), ("2026-08-14 17:00", "2026-08-14 22:00", ""),
                ("2026-08-15 10:00", "2026-08-15 19:00", "19시 퇴근 (쉬는 1시간 빼고 8시간, 청소년 하루 7시간 초과)"),
                ("2026-08-17 17:00", "2026-08-17 22:00", "셋째 주: 금, 토 결근 (주휴수당 없음)"),
                ("2026-08-19 17:00", "2026-08-19 22:00", ""),
                ("2026-08-24 17:00", "2026-08-24 22:00", "넷째 주: 모두 출근 (주휴수당)"),
                ("2026-08-26 17:00", "2026-08-26 22:00", ""), ("2026-08-28 17:00", "2026-08-28 22:00", ""),
                ("2026-08-29 10:00", "2026-08-29 16:00", ""),
                ("2026-08-31 17:00", "2026-08-31 22:00", "다섯째 주: 8월 안에서는 월요일만")],
     "void": ("2026-08-11 17:00", "2026-08-11 17:01", "실수로 누른 출근 (계산에서 빠짐)"),
     "month": "2026-08", "paid": "base", "deposit": "2026.09.10 10:05",
     "contract": {"근로일": "매주 월, 수, 금, 토", "근로시간": "월 수 금 17시 00분 ~ 22시 00분, 토 10시 00분 ~ 16시 00분",
                  "휴게시간": "월 수 금 19시 00분 ~ 19시 30분, 토 13시 00분 ~ 14시 00분", "임금": "시급 10,320원"},
     "docs": [("2_근로계약서.png", "contract", ""), ("2_급여명세서.png", "payslip", "8월분 명세서 (기본급만)"),
              ("2_입금내역.png", "deposit", "9/10 입금")],
     "expect": ["8월 급여: 적게 받음 (주휴수당 3주분과 가산수당을 못 받음) → 위반 의심", "청소년 야간근로: 8/5 22~23시 (인가 확인 필요)",
                "청소년 하루 7시간 초과: 8/15", "계약서, 최저임금, 휴게시간: 정상"]},
    {"key": "3", "title": "모두 정상: 최저임금 이상, 주 15시간 미만, 휴게 필요 없는 근무, 전액 받음",
     "job": {"name": "가상카페 시험점", "industry": "음식점, 카페", "work_desc": "음료 제조, 계산", "wage": 11000,
             "start_date": "2026-08-04", "end_date": "2026-12-31", "contract_written": True, "copy_received": True,
             "probation": "no", "size": "5+", "pay_cycle": "월급", "payday": 10, "pay_method": "계좌 이체",
             "deduction": "없음", "consent": "냈어요", "address": "경상남도 창원시 가상구 시험로 20", "owner": "이가상",
             "schedule": {d: {"start": "15:00", "end": "18:30", "brk": "없음"} for d in ("화", "목")}},
     "shifts": _day_shifts(["2026-08-04", "2026-08-06", "2026-08-11", "2026-08-13", "2026-08-18", "2026-08-20",
                            "2026-08-25", "2026-08-27"], "15:00", "18:30"),
     "month": "2026-08", "paid": "total", "deposit": "2026.09.10 09:30",
     "contract": {"근로일": "매주 화, 목", "근로시간": "15시 00분 ~ 18시 30분", "휴게시간": "없음 (하루 3시간 30분 근무)",
                  "임금": "시급 11,000원"},
     "docs": [("3_근로계약서.png", "contract", ""), ("3_급여명세서.png", "payslip", "8월분 명세서"),
              ("3_입금내역.png", "deposit", "9/10 입금")],
     "expect": ["계약서 항목 모두 정상 (주 7시간이라 주휴수당 대상 아님, 4시간 미만이라 휴게 의무 없음)", "8월 급여: 계산한 금액과 같음 → 정상"]},
    {"key": "4", "title": "정보 부족: 계약서 안 씀, 동의서 안 냄, 인원과 휴게시간 모름, 받은 금액 없음",
     "job": {"name": "가상베이커리 시험점", "industry": "판매, 마트", "work_desc": "빵 포장, 진열", "wage": 10320,
             "start_date": "2026-09-06", "contract_written": False, "copy_received": False, "probation": "unknown",
             "size": "unknown", "pay_cycle": "월급", "payday": 10, "consent": "안 냈어요", "owner": "정가상",
             "schedule": {"일": {"start": "09:00", "end": "15:00", "brk": "모름"}}},
     "shifts": _day_shifts(["2026-09-06", "2026-09-13", "2026-09-20", "2026-09-27"], "09:00", "15:00"),
     "month": "2026-09", "paid": None,
     "messages": [("boss", "내일부터 일요일 아침 9시에 나와", "9/5 18:20"), ("me", "네 계약서는 언제 쓰나요?", "9/5 18:22"),
                  ("boss", "계약서는 나중에 쓰자 바쁘다", "9/5 18:30"), ("me", "알겠습니다", "9/5 18:31")],
     "docs": [("4_사업주_메시지_캡처.png", "message", "계약서는 나중에 쓰자는 메시지")],
     "expect": ["근로계약서 작성: 위반 의심 (쓰지 않음)", "친권자 동의서: 위반 의심 또는 확인 필요 (안 냄)",
                "휴게시간: 확인 필요 (6시간 근무인데 쉬는 시간 모름)", "가산수당: 확인 필요 (사업장 인원 모름)",
                "9월 급여: 받은 금액이 없어 비교하지 않음 (확인 필요, 체불 판단 안 함)"]},
    {"key": "5", "title": "그만두고 임금 못 받음, 4시간 근무에 휴게 없음, 신고 후 보복 위협",
     "job": {"name": "가상치킨 시험점", "industry": "음식점, 카페", "work_desc": "포장, 배달 준비", "wage": 10320,
             "start_date": "2026-08-01", "end_date": "2026-12-31", "contract_written": True, "copy_received": True,
             "probation": "no", "size": "5+", "pay_cycle": "월급", "payday": 10, "pay_method": "계좌 이체",
             "deduction": "없음", "consent": "냈어요", "address": "경상남도 창원시 가상구 시험로 30", "owner": "최가상",
             "schedule": {d: {"start": "18:00", "end": "22:00", "brk": "없음"} for d in ("금", "토")}},
     "shifts": _day_shifts(["2026-08-01", "2026-08-07", "2026-08-08", "2026-08-14", "2026-08-15", "2026-08-21",
                            "2026-08-22", "2026-08-28", "2026-08-29"], "18:00", "22:00"),
     "month": "2026-08", "paid": None, "quit": "2026-08-29", "paid_after_quit": False, "reported": True,
     "contract": {"근로일": "매주 금, 토", "근로시간": "18시 00분 ~ 22시 00분", "휴게시간": "없음", "임금": "시급 10,320원"},
     "messages": [("me", "사장님 오늘까지만 하고 그만두겠습니다", "8/29 21:50"), ("boss", "알겠어 수고했다", "8/29 21:55"),
                  ("me", "사장님 8월 월급은 언제 주시나요?", "9/14 12:10"), ("boss", "가게가 어려워서 다음 달에 줄게", "9/14 15:40"),
                  ("me", "기한이 지나서 노동청에 신고했습니다", "9/20 10:02"),
                  ("boss", "신고 취소 안 하면 동네에 다 소문낸다", "9/20 10:15")],
     "docs": [("5_근로계약서.png", "contract", ""), ("5_사업주_메시지_캡처.png", "message", "임금 미지급, 신고 뒤 위협")],
     "expect": ["퇴직 정산: 위반 의심 (그만둔 날 8/29, 기한 9/12 지남, 못 받았다고 기록)", "휴게시간 부여: 위반 의심 (4시간 근무에 휴게 없음)",
                "8월 급여: 받은 금액 없음 → 비교하지 않음", "신고 후 보호: 신고함, 보복 위협 메시지 (게시물 검색은 화면에서)"]},
]


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def shifts_of(case: dict) -> list[tuple]:
    return _csv_shifts() if case["shifts"] == "csv" else case["shifts"]


def compute(case: dict):
    """코드가 계산한 그 달 급여 (사진 속 금액과 DB의 받은 금액이 같은 값을 쓴다)."""
    from app.calc import pay as paycalc
    recs = [SimpleNamespace(clock_in=_dt(s[0]), clock_out=_dt(s[1])) for s in shifts_of(case)]
    j = case["job"]
    return paycalc.calc_month(recs, j["schedule"], j["wage"], j["size"], date.fromisoformat(BIRTH), case["month"])


def paid_of(case: dict) -> int | None:
    p = case["paid"]
    if p in ("base", "total"):
        return getattr(compute(case), p)
    return p


# ---------- 서류 그리기 (HTML → PNG는 tests/ui/make_case_images.js) ----------
CSS = """body{margin:0;background:#fff;font-family:'Noto Sans KR','Apple SD Gothic Neo','WenQuanYi Zen Hei',sans-serif;color:#111}
.doc{width:1040px;padding:40px 60px}.doc h1{text-align:center;font-size:40px;margin:0 0 50px}
table{border-collapse:collapse;width:100%;font-size:26px}td{border:2px solid #111;padding:18px 18px;height:34px}
td.k{width:180px;text-align:center;font-weight:700}.sig{font-size:26px;margin-top:40px;line-height:2.4}
.slip{width:880px;padding:40px 60px}.slip h1{text-align:center;font-size:38px}.slip p{font-size:26px;margin:10px 0}
.slip td{font-size:26px}.slip td.r{text-align:right}.slip td.k{text-align:left;width:auto}
.bank{width:680px;padding:30px 40px;font-size:26px}.bank h1{font-size:30px;margin:10px 0 30px}
.row{border-top:2px solid #ddd;padding:16px 0}.row .t{color:#666;font-size:20px}.row .l{display:flex;justify-content:space-between;margin-top:6px}
.in{color:#1a5fd6;font-weight:700}.out{font-weight:700}
.chat{width:750px;background:#b2c7d9;min-height:900px;font-size:26px}.chat h1{background:#a9bccd;text-align:center;font-size:28px;margin:0;padding:24px}
.msg{display:flex;align-items:flex-end;gap:10px;margin:28px 40px}.msg.me{flex-direction:row-reverse}
.bubble{background:#fff;border-radius:18px;padding:14px 20px;max-width:430px}.me .bubble{background:#fee500}.time{font-size:17px;color:#444}"""


def _page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def _korean_date(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{d.year}년 {d.month}월 {d.day}일"


def contract_html(case: dict) -> str:
    j, c, e = case["job"], case["contract"], html.escape
    rows = [("사업주", f"{j['name']} 대표 {j['owner']}"), ("근로자", f"{NAME} (2009년 5월 20일생)"),
            ("계약기간", f"{_korean_date(j['start_date'])} ~ {_korean_date(j['end_date'])}"), ("수습기간", "없음"),
            ("근무장소", j["address"]), ("업무내용", j["work_desc"]), ("근로일", c["근로일"]), ("근로시간", c["근로시간"]),
            ("휴게시간", c["휴게시간"]), ("주휴일", "매주 일요일"), ("임금", c["임금"]),
            ("임금지급일", f"매월 {j['payday']}일, 근로자 명의 계좌 입금"), ("연차휴가", "관계 법령에 따름")]
    trs = "".join(f"<tr><td class='k'>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in rows)
    return _page(f"<div class='doc'><h1>근로계약서 (연소근로자용)</h1><table>{trs}</table>"
                 f"<div class='sig'>{_korean_date(j['start_date'])}<br>사업주: {e(j['owner'])} (서명)"
                 f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;근로자: {NAME} (서명)</div></div>")


def payslip_html(case: dict) -> str:
    j, r, paid = case["job"], compute(case), paid_of(case)
    y, m = map(int, case["month"].split("-"))
    days = sorted(s[0][:10] for s in shifts_of(case))
    hours = f"{r.work_min // 60}시간" + (f" {r.work_min % 60}분" if r.work_min % 60 else "")
    full = case["paid"] == "total"
    rows = [("기본급", f"{hours} x {j['wage']:,}원", r.base), ("주휴수당", "", r.weekly_holiday if full else 0),
            ("가산수당", "", r.premium if full else 0), ("공제 합계", "", 0)]
    trs = "".join(f"<tr><td class='k'><b>{k}</b></td><td>{d}</td><td class='r'>{v:,}원</td></tr>" for k, d, v in rows)
    trs += f"<tr><td class='k'><b>실지급액</b></td><td></td><td class='r'><b>{paid:,}원</b></td></tr>"
    pay_m = m % 12 + 1
    return _page(f"<div class='slip'><h1>{y}년 {m}월분 임금명세서</h1><p>성명: {NAME}</p><p>사업장: {html.escape(j['name'])}</p>"
                 f"<p>지급일: {y + (m == 12)}년 {pay_m}월 {j['payday']}일</p>"
                 f"<p>산정기간: {y}년 {m}월 {int(days[0][8:])}일 ~ {m}월 {int(days[-1][8:])}일</p><br><table>{trs}</table></div>")


def deposit_html(case: dict) -> str:
    j, paid = case["job"], paid_of(case)
    rows = [(case["deposit"], j["name"].replace(" ", ""), f"+{paid:,}원", "in"),
            ("2026.09.03 18:40", "가상문구", "-4,500원", "out"), ("2026.08.28 12:05", "가상분식집", "-6,000원", "out")]
    items = "".join(f"<div class='row'><div class='t'>{t}</div><div class='l'><span>{html.escape(n)}</span>"
                    f"<span class='{c}'>{a}</span></div></div>" for t, n, a, c in rows)
    return _page(f"<div class='bank'><h1>입출금 거래내역</h1>{items}</div>")


def messages_html(case: dict) -> str:
    j = case["job"]
    items = "".join(f"<div class='msg {'me' if who == 'me' else ''}'><div class='bubble'>{html.escape(t)}</div>"
                    f"<div class='time'>{tm}</div></div>" for who, t, tm in case["messages"])
    return _page(f"<div class='chat'><h1>{html.escape(j['owner'])} 사장님</h1>{items}</div>")


DRAW = {"근로계약서": contract_html, "급여명세서": payslip_html, "입금내역": deposit_html, "사업주_메시지_캡처": messages_html}


def write_html(out: Path) -> None:
    """2~5번 알바의 서류를 HTML로 쓰고, 사진 속 금액을 manifest.json에 적는다."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for case in CASES[1:]:
        for name, _, _ in case["docs"]:
            draw = DRAW[name.split("_", 1)[1].removesuffix(".png")]
            (out / name.replace(".png", ".html")).write_text(draw(case), encoding="utf-8")
        manifest[case["key"]] = {"month": case["month"], "work_min": compute(case).work_min, "paid": paid_of(case)}
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    (CASE_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HTML: {out}\n사진으로 찍기: node tests/ui/make_case_images.js {out} {CASE_DIR}")


def check_images() -> None:
    """사진이 모두 있고, 사진 속 금액(manifest)이 지금 코드의 계산과 같은지."""
    manifest = json.loads((CASE_DIR / "manifest.json").read_text(encoding="utf-8"))
    for case in CASES:
        base = MATERIAL if case["key"] == "1" else CASE_DIR
        missing = [n for n, _, _ in case["docs"] if not (base / n).exists()]
        if missing:
            sys.exit(f"{case['key']}번 알바의 사진이 없어요: {missing}")
        m = manifest.get(case["key"])
        if m and (m["work_min"] != compute(case).work_min or m["paid"] != paid_of(case)):
            sys.exit(f"{case['key']}번 알바: 사진 속 금액이 지금 계산과 달라요. 사진을 다시 그려 주세요 (이 파일 맨 위 설명).")


# ---------- DB에 넣기 ----------
LAW_TABLES = ("lawarticle", "lawsource", "lawdoc")  # 법 기준표 (python -m app.law.fetch로 평소 DB에 만든 것)


def copy_law_table(target_db: Path, src: Path | None = None) -> int:
    """평소 DB(data/app.db)의 법 기준표를 시험 DB로 복사한다. 평소 DB는 읽기만 한다.
    시험 DB에 법 기준표가 없으면 get_article이 모두 '미구축'이라 에이전트가 조문을 확인하지 못한다."""
    import sqlite3
    src = src or ROOT / "data" / "app.db"
    if not src.exists():
        return 0
    copied = 0
    with sqlite3.connect(target_db) as dst:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            for t in LAW_TABLES:
                try:
                    src_cols = [r[1] for r in s.execute(f"pragma table_info({t})")]
                except sqlite3.Error:
                    continue
                cols = [c for c in (r[1] for r in dst.execute(f"pragma table_info({t})")) if c in src_cols]
                if not cols:
                    continue
                rows = s.execute(f"select {','.join(cols)} from {t}").fetchall()
                dst.executemany(f"insert into {t} ({','.join(cols)}) values ({','.join('?' * len(cols))})", rows)
                copied += len(rows)
        finally:
            s.close()
    return copied
def _ok(r, what: str):
    if r.status_code != 200:
        raise SystemExit(f"{what}에 실패했어요: {r.status_code} {r.text[:300]}")
    return r.json()


def _upload(c, url: str, path: Path, data: dict) -> dict:
    return _ok(c.post(url, data=data, files={"file": (path.name, path.read_bytes(), "image/png")}), f"{path.name} 올리기")


def build_case(c, uid: int, case: dict) -> int:
    from sqlmodel import Session

    from app.calc.timeutil import now_kst
    from app.db import engine
    from app.models import WorkRecord

    job_id = _ok(c.post("/api/jobs", json=case["job"]), f"{case['job']['name']} 만들기")["id"]
    with Session(engine) as s:
        for sh in shifts_of(case):
            gps = sh[3] if len(sh) > 3 else None
            s.add(WorkRecord(user_id=uid, job_id=job_id, clock_in=_dt(sh[0]), clock_out=_dt(sh[1]),
                             in_lat=gps and gps[0], in_lng=gps and gps[1], out_lat=gps and gps[0], out_lng=gps and gps[1]))
        if case.get("void"):
            a, b, why = case["void"]
            s.add(WorkRecord(user_id=uid, job_id=job_id, clock_in=_dt(a), clock_out=_dt(b), void_at=now_kst(), void_reason=why))
        s.commit()
    base = MATERIAL if case["key"] == "1" else CASE_DIR
    paid = paid_of(case)
    for name, kind, note in case["docs"]:
        if kind == "contract":
            _upload(c, f"/api/jobs/{job_id}/contract", base / name, {"read": "false"})  # 사진 읽기는 화면에서 AI로
        elif kind == "payslip":
            _upload(c, f"/api/jobs/{job_id}/payslip", base / name, {"month": case["month"], "amount": str(paid), "check": "false"})
        else:
            _upload(c, f"/api/jobs/{job_id}/evidence", base / name, {"kind": kind, "note": note})
    if case.get("quit"):
        _ok(c.post(f"/api/jobs/{job_id}/quit", json={"quit_date": case["quit"], "check": False}), "그만둔 날 저장")
    if case.get("paid_after_quit") is not None:
        _ok(c.post(f"/api/jobs/{job_id}/paid", json={"paid": case["paid_after_quit"], "check": False}), "받음 여부 저장")
    if case.get("reported"):
        _ok(c.post(f"/api/jobs/{job_id}/guard", json={"reported": True, "check": False}), "신고했어요 저장")
    return job_id


def show(case: dict) -> None:
    j, r, paid = case["job"], compute(case), paid_of(case)
    print(f"\n[{case['key']}] {j['name']} — {case['title']}")
    sched = ", ".join(f"{d} {v['start']}~{v['end']}(쉬는 시간 {v['brk']})" for d, v in j["schedule"].items())
    print(f"  시급 {j['wage']:,}원, 인원 {dict(lt5='5명 미만', unknown='모름').get(j['size'], '5명 이상')}, 근무 {j['start_date']}~"
          + (f", 그만둔 날 {case['quit']}" if case.get("quit") else "") + f", 계약 근무 {sched}")
    days = [s[0][5:10] for s in shifts_of(case)]
    print(f"  근무 기록 {len(days)}건: {', '.join(days)}" + (f" (+ 실수 표시 {case['void'][0][5:10]})" if case.get("void") else ""))
    print(f"  {case['month']} 계산: 근무 {r.work_min // 60}시간 {r.work_min % 60}분, 기본급 {r.base:,}원, "
          f"주휴수당 {r.weekly_holiday:,}원, 가산수당 {r.premium:,}원, 합계 {r.total:,}원")
    print(f"  받은 금액: {'기록 없음' if paid is None else f'{paid:,}원 (차이 {r.total - paid:,}원)'}")
    print(f"  올린 사진: {', '.join(n for n, _, _ in case['docs'])}")
    for x in case["expect"]:
        print(f"  기대: {x}")


def _target(arg: str) -> Path:
    target = (Path(arg) if Path(arg).is_absolute() else ROOT / arg).resolve()
    data = (ROOT / "data").resolve()
    if target == data or data not in target.parents:
        sys.exit("시험 데이터는 data 폴더 안의 따로 된 폴더(기본 data/test)에만 만들어요.")
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description="시험 데이터(알바 5개와 서류 사진) 만들기")
    ap.add_argument("--dir", default="data/test", help="만들 폴더 (기본 data/test, .env의 TEST_DATA=true가 쓰는 곳)")
    ap.add_argument("--force", action="store_true", help="폴더가 이미 있으면 지우고 다시 만들기")
    ap.add_argument("--html", help="(개발자용) 2~5번 알바의 서류를 HTML로 이 폴더에 쓰기")
    args = ap.parse_args()
    if args.html:
        write_html(Path(args.html))
        return
    target = _target(args.dir)
    # 저장 위치는 app을 하나라도 불러오기 전에 정해야 이 폴더를 쓴다 (.env의 값보다 먼저)
    os.environ.update(DB_PATH=str(target / "app.db"), UPLOAD_DIR=str(target / "uploads"),
                      REPORT_DIR=str(target / "reports"), LLM_FAKE="false", LLM_ENABLED="false")
    from app import config
    if config.DB_PATH != (target / "app.db").resolve() or target not in config.UPLOAD_DIR.parents:
        sys.exit(f"저장 위치가 시험 폴더가 아니에요 ({config.DB_PATH}). 평소 데이터를 지키려고 멈춰요.")
    check_images()
    if (target / "app.db").exists() or any(p.is_file() for p in target.rglob("*")):  # 빈 폴더는 config가 막 만든 것
        if not args.force:
            sys.exit(f"{target} 가 이미 있어요. 지우고 다시 만들려면 --force를 붙여 주세요.")
        shutil.rmtree(target)
        for d in (config.UPLOAD_DIR, config.REPORT_DIR):
            d.mkdir(parents=True, exist_ok=True)
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app
    init_db()
    laws = copy_law_table(config.DB_PATH)
    print(f"법 기준표: 평소 DB에서 {laws}건 복사" if laws else
          "법 기준표: 평소 DB(data/app.db)에 없어 복사하지 못했어요. 시험 데이터로 켠 채 법 기준표를 만들 수 있어요 (TEST_DATA=true python -m app.law.fetch)")
    c = TestClient(app)  # 서버를 띄우지 않고 앱의 API를 부른다 (예약 작업은 돌지 않음)
    _ok(c.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD, "birth_date": BIRTH}), "가입")
    uid = _ok(c.get("/api/me"), "내 정보")["id"]
    _ok(c.put("/api/me/prefs", json={"gps_consent": True}), "위치 기록 동의")
    print(f"계정: {EMAIL} / {PASSWORD} ({NAME}, {BIRTH}생, 만 17세)")
    for case in CASES:
        build_case(c, uid, case)
        show(case)
    print(f"\n만든 폴더: {target}")
    if args.dir == "data/test":
        print("시험 데이터로 실행: .env에 TEST_DATA=true 를 넣고 서버를 켜세요 (로그에 '시험 데이터로 실행 중')")


if __name__ == "__main__":
    main()
