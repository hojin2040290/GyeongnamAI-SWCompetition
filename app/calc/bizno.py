"""사업자등록번호 정리와 확인. 마지막 자리는 앞 9자리로 계산하는 검증 번호다."""
import re

WEIGHTS = [1, 3, 7, 1, 3, 7, 1, 3, 5]


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def check_digit(first9: str) -> int:
    """앞 9자리로 마지막 검증 번호를 계산한다."""
    d = [int(c) for c in first9]
    total = sum(a * w for a, w in zip(d, WEIGHTS)) + (d[8] * 5) // 10
    return (10 - total % 10) % 10


def is_valid(value: str) -> bool:
    n = digits(value)
    return len(n) == 10 and check_digit(n[:9]) == int(n[9])


def normalize(value: str) -> str:
    """빈 값은 그대로, 10자리는 000-00-00000 형태로. 자리 수가 틀리면 ValueError.
    검증 번호가 틀려도 저장은 막지 않는다 (점검 결과에서 확인 필요로 안내)."""
    n = digits(value)
    if not n:
        return ""
    if len(n) != 10:
        raise ValueError("사업자등록번호는 숫자 10자리예요")
    return f"{n[:3]}-{n[3:5]}-{n[5:]}"
