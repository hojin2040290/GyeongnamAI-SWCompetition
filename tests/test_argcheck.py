"""AI가 낸 도구 입력 검사: 분명히 바꿀 수 있는 것만 바꾸고, 나머지는 오류로 돌려준다."""
import pytest

from app.agent.argcheck import ArgError, fit_args

PROPS = {"month": {"type": "string"}, "n": {"type": "integer"}, "on": {"type": "boolean"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "status": {"type": "string", "enum": ["ok", "warn", "bad"]},
         "items": {"type": "array", "items": {"type": "object", "properties": {"i": {"type": "integer"}}, "required": ["i"]}}}


def test_converts_clear_values():
    out = fit_args(PROPS, [], {"month": 202608, "n": "3", "on": "true", "tags": ["a", 1], "items": [{"i": "2"}]})
    assert out == {"month": "202608", "n": 3, "on": True, "tags": ["a", "1"], "items": [{"i": 2}]}


def test_drops_unknown_and_null():
    assert fit_args(PROPS, [], {"zzz": 1, "month": None}) == {}


@pytest.mark.parametrize("args", [{"month": ["2026-08"]}, {"month": {"a": 1}}, {"n": "abc"}, {"n": True}, {"n": 1.5},
                                  {"tags": "a"}, {"status": "모름"}, {"items": [{"x": 1}]}, {"items": [1]}])
def test_rejects_wrong_types(args):
    with pytest.raises(ArgError):
        fit_args(PROPS, [], args)


def test_required():
    with pytest.raises(ArgError, match="month"):
        fit_args(PROPS, ["month"], {"n": 1})
