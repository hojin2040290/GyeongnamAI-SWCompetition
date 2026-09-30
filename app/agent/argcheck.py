"""AI가 낸 도구 입력을 도구 설명(JSON 스키마)에 맞춰 검사한다.

실제 모델은 글 칸에 목록을, 숫자 칸에 글자를 넣기도 한다. 그대로 실행하면 서버 오류가 나므로
분명히 바꿀 수 있는 것("3" → 3, 숫자 → 글)만 바꾸고, 나머지는 AI에게 오류로 돌려준다.
"""


class ArgError(ValueError):
    """도구 입력이 설명과 맞지 않을 때 (AI에게 그대로 돌려준다)."""


def _string(v, where: str) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    raise ArgError(f"{where}에는 글(문자열)을 넣어 주세요")


def _integer(v, where: str) -> int:
    if isinstance(v, bool):
        raise ArgError(f"{where}에는 정수를 넣어 주세요")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    raise ArgError(f"{where}에는 정수를 넣어 주세요")


def _number(v, where: str) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(str(v).strip())
    except ValueError:
        raise ArgError(f"{where}에는 숫자를 넣어 주세요") from None


def _boolean(v, where: str) -> bool:
    if isinstance(v, bool):
        return v
    if str(v).strip().lower() in ("true", "false"):
        return str(v).strip().lower() == "true"
    raise ArgError(f"{where}에는 true나 false를 넣어 주세요")


def fit(schema: dict, value, where: str):
    """값 하나를 스키마에 맞춘다. 맞출 수 없으면 ArgError."""
    kind = schema.get("type")
    if kind == "string":
        value = _string(value, where)
    elif kind == "integer":
        value = _integer(value, where)
    elif kind == "number":
        value = _number(value, where)
    elif kind == "boolean":
        value = _boolean(value, where)
    elif kind == "array":
        if not isinstance(value, list):
            raise ArgError(f"{where}에는 목록(배열)을 넣어 주세요")
        items = schema.get("items") or {}
        value = [fit(items, v, f"{where}[{n}]") for n, v in enumerate(value)] if items else value
    elif kind == "object":
        if not isinstance(value, dict):
            raise ArgError(f"{where}에는 객체를 넣어 주세요")
        if schema.get("properties"):
            value = fit_args(schema["properties"], schema.get("required") or [], value, where)
    if "enum" in schema and value not in schema["enum"]:
        raise ArgError(f"{where}은(는) {', '.join(map(str, schema['enum']))} 중 하나여야 해요")
    return value


def fit_args(props: dict, required: list, args: dict, where: str = "") -> dict:
    """도구 입력 전체를 맞춘다. 설명에 없는 입력과 빈 값(null)은 뺀다."""
    out = {}
    for key, value in args.items():
        if key not in props or value is None:
            continue
        out[key] = fit(props[key], value, f"{where}.{key}" if where else key)
    missing = [k for k in required if k not in out]
    if missing:
        raise ArgError(f"{where + ' ' if where else ''}꼭 필요한 입력이 없어요: {', '.join(missing)}")
    return out
