"""입력 칸마다 쓸 수 있는 글자와 안내. 화면(입력하는 동안 거르기, 안내 문구)과 서버(저장할 때 검사)가 같은 규칙을 쓴다.

chars는 정규식 문자 묶음([...]) 안에 들어가는 글자들이다. 한글은 입력하는 도중의 자모(ㄱ, ㅏ)도 포함한다.
"""
import re

KO = "가-힣ㄱ-ㅎㅏ-ㅣ"

# 근로계약서에 적힌 내용 (항목 이름은 law_params.json의 written_terms.items)
CONTRACT = {
    "임금": {"chars": KO + "0-9 ,.%()~", "allowed": "한글, 숫자, 띄어쓰기와 , . % ( ) ~",
           "placeholder": "시급·일급·월급과 금액 (예: 시급 10,030원)"},
    "근로시간": {"chars": KO + "0-9 ,:~()\\-", "allowed": "한글, 숫자, 띄어쓰기와 , : ~ ( ) -",
             "placeholder": "일하는 요일과 시각 (예: 매주 월, 수, 금 17:00~22:30)"},
    "휴게시간": {"chars": KO + "0-9 ,:~()\\-", "allowed": "한글, 숫자, 띄어쓰기와 , : ~ ( ) -",
             "placeholder": "쉬는 시간 (예: 30분, 19:30~20:00)"},
    "휴일": {"chars": KO + " ,", "allowed": "한글, 띄어쓰기와 ,",
           "placeholder": "쉬는 날 (예: 매주 일요일)"},
    "연차휴가": {"chars": KO + "0-9 ,.()", "allowed": "한글, 숫자, 띄어쓰기와 , . ( )",
             "placeholder": "연차휴가를 어떻게 준다고 적혀 있는지 (예: 근로기준법에 따름)"},
    "근무장소": {"chars": KO + "0-9 ,.()\\-", "allowed": "한글, 숫자, 띄어쓰기와 , . ( ) -",
             "placeholder": "일하는 곳 주소 (예: 경남 창원시 의창구 중앙대로 12)"},
    "업무내용": {"chars": KO + "0-9 ,.()·/", "allowed": "한글, 숫자, 띄어쓰기와 , . ( ) · /",
             "placeholder": "맡은 일 (예: 계산, 상품 진열, 매장 청소)"},
}

# 사업장 정보의 글 칸 (가게 이름에는 영문이 들어갈 수 있다)
JOB_CHARS = KO + "A-Za-z0-9 .,\\-()·/&#"
JOB_ALLOWED = "한글, 영문, 숫자, 띄어쓰기와 .,-()·/&#"
JOB = {"name": "사업장 이름", "owner": "사업주", "address": "주소", "work_desc": "하는 일"}


def _ok(chars: str, value: str) -> bool:
    return re.fullmatch(f"[{chars}]*", value or "") is not None


def contract_problem(item: str, value: str) -> str | None:
    rule = CONTRACT.get(item)
    if rule and not _ok(rule["chars"], value):
        return f"{item}에는 {rule['allowed']}만 쓸 수 있어요"
    return None


def job_problem(field: str, value: str) -> str | None:
    if field in JOB and not _ok(JOB_CHARS, value):
        return f"{JOB[field]}에는 {JOB_ALLOWED}만 쓸 수 있어요"
    return None


def clean_contract(item: str, value: str) -> str:
    """쓸 수 없는 글자를 뺀다 (사진에서 읽은 내용을 칸에 채우기 전에)."""
    rule = CONTRACT.get(item)
    if not rule:
        return value
    return re.sub(r"\s+", " ", re.sub(f"[^{rule['chars']}]", "", value or "")).strip()


def for_screen() -> dict:
    """화면이 받아 쓰는 규칙."""
    return {"contract": CONTRACT, "job": {"chars": JOB_CHARS, "allowed": JOB_ALLOWED, "fields": JOB}}
