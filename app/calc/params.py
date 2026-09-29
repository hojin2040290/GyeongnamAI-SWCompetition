"""법 기준값(data/law_params.json) 읽기.

금액 같은 기준값은 이 파일에서만 읽는다. 법제처에서 불러온 고시는 값을 바꾸지 않고 근거(출처)로만 붙인다.
"""
import json
from functools import lru_cache

from app.config import LAW_PARAMS_PATH

# 법제처에서 불러온 최저임금 고시 {2026: "2026년 적용 최저임금 고시 (고용노동부 고시 제2025-00호)"}
_notices: dict[int, str] = {}


@lru_cache
def P() -> dict:
    return json.loads(LAW_PARAMS_PATH.read_text(encoding="utf-8"))


def set_notices(notices: dict[int, str]) -> None:
    _notices.clear()
    _notices.update(notices)


def min_wage(year: int) -> tuple[int | None, str]:
    """(시간급 최저임금, 근거). 금액은 law_params.json, 근거는 법제처에서 불러온 고시가 있으면 그 고시."""
    v = P()["min_wage"]["by_year"].get(str(year))
    if not v:
        return None, ""
    return v, (f"근거 고시: {_notices[year]}" if year in _notices else P()["min_wage"]["source"])
