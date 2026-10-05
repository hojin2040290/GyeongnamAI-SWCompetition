"""프롬프트 인젝션 대비 안전장치.

AI가 받는 글(사용자가 적은 칸, 외부 게시물, 파일 이름 등)은 지시가 아닌 데이터로만 다룬다.
- neutralize(): 채팅 틀을 흉내 내는 특수 토큰(<|im_start|>, <tool_call> 등)과 보이지 않는 제어 문자를 지운다.
- output_problem(): AI가 사용자에게 보여 줄 글에 링크나 상담 기관이 아닌 전화번호가 있으면 막는다
  (주입된 지시로 가짜 링크나 번호를 안내하는 것을 막기 위해). 한자(중국어)가 섞여도 돌려보낸다 (Qwen 계열이 가끔 섞는다).
  서버 안에서만 쓰는 이름(도구 이름, warn 같은 결과 값, quit_check 같은 점검 이름)도 사용자는 모르는 말이라 한국어로 다시 쓰게 한다.
"""
import re

from app.calc.params import P

# 모델의 채팅 틀이나 도구 호출 표시를 흉내 내는 문자열
_SPECIAL = re.compile(
    r"<\|[^<>|\n]{0,40}\|>|</?\s*(tool_call|tool_response|tools|think|system|assistant|user|function)\s*>"
    r"|\[/?INST\]|<</?SYS>>|<\s*/?\s*s\s*>", re.I)
# 제어 문자와 글자 방향을 바꾸는 보이지 않는 문자 (줄바꿈과 탭은 남긴다)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_URL = re.compile(r"(https?://|www\.)\S+|\b[\w-]+\.(com|net|kr|org|io|me|xyz|ly|link)(/\S*)?\b", re.I)
# 한자 (CJK 통합 한자와 확장 A, 호환 한자). 실제 모델이 한국어 글에 '元之间' 같은 중국어를 섞은 적이 있다
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
# 서버 안에서만 쓰는 이름과 뜻 (LLM이 고쳐 쓸 때 참고로 넘긴다. 실제 사례: '확인 필요(warn)로 판단. 10월 12일 quit_check 예약')
KO_NAMES = {
    "ok": "정상", "warn": "확인 필요", "bad": "위반 의심", "pending": "AI 판단 대기", "suspect": "보복 의심", "unclear": "확인 필요",
    "contract_check": "계약서 점검", "payday": "급여 점검", "quit_check": "퇴직 정산 확인", "guard_review": "신고 후 게시물 확인",
    "report": "상담 사전 자료", "check": "계약서 화면", "pay": "급여 화면", "docs": "자료 화면", "guard": "보호 화면",
    "ask_user": "질문", "build_report": "상담 사전 자료 만들기", "calc_pay": "임금 계산", "calc_work_days": "근무일 계산",
    "check_rules": "법 기준 대조", "compare_pay": "급여 비교", "counsel_for_age": "상담 기관 찾기", "find_refs": "판례 찾기",
    "finish": "마무리", "get_age_on": "만 나이 계산", "get_all_facts": "기록 모아 보기", "get_answers": "답변 확인",
    "get_article": "법 조문 확인", "get_contract": "계약서 내용 확인", "get_overview": "점검 현황 확인", "get_payslip": "받은 금액 확인",
    "get_profile": "내 정보 확인", "get_saved_checks": "저장된 점검 결과 확인", "give_advice": "조언", "list_evidence": "증거 자료 확인",
    "list_posts": "보존한 게시물 확인", "make_plan": "계획 세우기", "notify": "알림", "remember": "메모",
    "run_pay_check": "급여 점검", "run_post_search": "게시물 검색", "run_quit_check": "퇴직 정산 점검",
    "save_warning_message": "사업주 안내 문구 저장", "schedule_followup": "확인 예약", "search_posts": "게시물 검색",
    "set_post_status": "게시물 판별", "settlement": "퇴직 정산 계산", "judgments": "판단", "answer_ids": "답변 번호",
    "next_tab": "바로 가기 화면", "read_contract_image": "계약서 사진 읽기", "read_payslip_image": "명세서 사진 읽기",
    "read_posting_image": "채용공고 사진 읽기",
}
_WORD = r"(?<![A-Za-z0-9_])({})(?![A-Za-z0-9_])"
# 알려진 이름, 그리고 알 수 없어도 snake_case(영어_밑줄)는 내부 이름으로 본다
_INTERNAL = re.compile(_WORD.format("|".join(sorted(map(re.escape, KO_NAMES), key=len, reverse=True)) + r"|[a-z]+(?:_[a-z0-9]+)+"))
FAKE_PREFIX = "테스트 답변입니다"  # 가짜 AI 글은 무엇을 넘겼는지 보이려고 도구 이름을 일부러 적는다
_PHONE = re.compile(r"(?<![\d-])(0\d{1,2}[-. ]?\d{3,4}[-. ]?\d{4}|1\d{3}[-. ]?\d{4})(?![\d-])")


def neutralize(text: str) -> str:
    """AI에게 넘기기 전에 특수 토큰과 보이지 않는 문자를 지운다."""
    return _SPECIAL.sub(" ", _CTRL.sub("", text or ""))


def clip(text, limit: int) -> str:
    return neutralize(str(text or "")).strip()[:limit]


def allowed_phones() -> set[str]:
    return {re.sub(r"\D", "", c["phone"]) for c in P()["counsel"]}


def output_problem(*texts) -> str | None:
    """사용자에게 보여 줄 AI 글 검사. 문제가 있으면 AI에게 돌려줄 말."""
    joined = " ".join(str(t or "") for t in texts)
    if _URL.search(joined):
        return "사용자에게 보여 줄 글에는 링크나 인터넷 주소를 넣을 수 없어요. 빼고 다시 써 주세요"
    ok = allowed_phones()
    bad = [m.group(0) for m in _PHONE.finditer(joined) if re.sub(r"\D", "", m.group(0)) not in ok]
    if bad:
        return f"전화번호는 상담 기관 번호({', '.join(c['phone'] for c in P()['counsel'])})만 쓸 수 있어요. 빼고 다시 써 주세요"
    han = _HAN.findall(joined)
    if han:
        return f"사용자에게 보여 줄 글은 한국어(한글)로만 써 주세요. 한자나 중국어({', '.join(dict.fromkeys(han))[:40]})가 섞였어요. 한글로 고쳐 다시 써 주세요"
    found = internal_names(*texts)
    if found:
        hint = ", ".join(f"{w} → {KO_NAMES[w]}" if w in KO_NAMES else w for w in found[:6])
        return (f"사용자에게 보여 줄 글에 서버 안에서만 쓰는 이름({hint})이 있어요. 사용자는 모르는 말이니 "
                "도구 이름이나 영어 값을 빼고 한국어로 풀어 다시 써 주세요 (결과는 정상, 확인 필요, 위반 의심으로)")
    return None


def internal_names(*texts) -> list[str]:
    """사용자에게 보일 글 속 내부 이름. 가짜 AI 글('테스트 답변입니다 (...)')은 빼고 본다."""
    found: list[str] = []
    for t in texts:
        t = str(t or "")
        if t.startswith(FAKE_PREFIX):
            continue
        found += [m.group(1) for m in _INTERNAL.finditer(t)]
    return list(dict.fromkeys(found))


def scrub(text: str) -> str:
    """이미 받은 AI 글(판단 이유 등)에서 링크와 상담 기관이 아닌 전화번호를 지운다."""
    ok = allowed_phones()
    text = _HAN.sub("", _URL.sub("(링크 삭제됨)", text or ""))  # 한자는 지운다 (내부 이름은 rewrite.for_user가 LLM으로 고친다)
    return _PHONE.sub(lambda m: m.group(0) if re.sub(r"\D", "", m.group(0)) in ok else "(번호 삭제됨)", text)
