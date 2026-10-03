"""프롬프트 인젝션 대비 안전장치.

AI가 받는 글(사용자가 적은 칸, 외부 게시물, 파일 이름 등)은 지시가 아닌 데이터로만 다룬다.
- neutralize(): 채팅 틀을 흉내 내는 특수 토큰(<|im_start|>, <tool_call> 등)과 보이지 않는 제어 문자를 지운다.
- output_problem(): AI가 사용자에게 보여 줄 글에 링크나 상담 기관이 아닌 전화번호가 있으면 막는다
  (주입된 지시로 가짜 링크나 번호를 안내하는 것을 막기 위해). 한자(중국어)가 섞여도 돌려보낸다 (Qwen 계열이 가끔 섞는다).
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
    return None


def scrub(text: str) -> str:
    """이미 받은 AI 글(판단 이유 등)에서 링크와 상담 기관이 아닌 전화번호를 지운다."""
    ok = allowed_phones()
    text = _HAN.sub("", _URL.sub("(링크 삭제됨)", text or ""))  # 한자는 지운다 (다시 쓰게 할 수 없는 곳)
    return _PHONE.sub(lambda m: m.group(0) if re.sub(r"\D", "", m.group(0)) in ok else "(번호 삭제됨)", text)
