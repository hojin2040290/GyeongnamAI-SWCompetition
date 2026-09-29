"""나이 계산. 법마다 기준이 달라 함수를 나눈다."""
from datetime import date


def age_on(birth: date, on: date) -> int:
    """만 나이."""
    a = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        a -= 1
    return a


def is_youth_protection(birth: date, on: date, age_limit: int, year_rule: bool) -> bool:
    """청소년 보호법의 청소년 여부. year_rule이면 만 나이가 되는 해의 1월 1일을 맞은 사람은 제외."""
    if year_rule:
        return on.year - birth.year < age_limit
    return age_on(birth, on) < age_limit
