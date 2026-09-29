"""법 기준값(data/law_params.json) 읽기."""
import json
from functools import lru_cache

from app.config import LAW_PARAMS_PATH


@lru_cache
def P() -> dict:
    return json.loads(LAW_PARAMS_PATH.read_text(encoding="utf-8"))
