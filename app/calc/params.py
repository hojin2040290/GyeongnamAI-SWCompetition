"""법 기준값 읽기. 기본은 data/law_params.json, 법제처에서 불러온 고시 값이 있으면 그 값을 우선한다."""
import json
from functools import lru_cache

from app.config import LAW_PARAMS_PATH

# 법제처 고시에서 불러온 값 {("min_wage", 2026): (10320, "출처")}. 서버 시작과 법 기준표 갱신 때 채운다.
_overrides: dict[tuple[str, int], tuple[int, str]] = {}


@lru_cache
def P() -> dict:
    return json.loads(LAW_PARAMS_PATH.read_text(encoding="utf-8"))


def set_overrides(values: dict[tuple[str, int], tuple[int, str]]) -> None:
    _overrides.clear()
    _overrides.update(values)


def min_wage(year: int) -> tuple[int | None, str]:
    """(시간급 최저임금, 출처). 고시에서 불러온 값이 없으면 law_params.json 값."""
    if ("min_wage", year) in _overrides:
        return _overrides[("min_wage", year)]
    v = P()["min_wage"]["by_year"].get(str(year))
    return v, (P()["min_wage"]["source"] if v else "")
