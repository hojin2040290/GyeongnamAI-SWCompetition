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


def test_long_text_is_sent_back_not_cut():
    """AI가 쓴 글이 칸보다 길면 몰래 자르지 않고, 몇 자로 줄일지 알려 돌려보낸다 (화면에 끊긴 글이 나오지 않게)."""
    from app.agent.argcheck import ArgError, fit_args
    from app.agent.tools import text, texts
    with pytest.raises(ArgError, match="450자예요.*400자 안으로"):
        fit_args({"advice": text(400)}, ["advice"], {"advice": "가" * 450})
    assert fit_args({"advice": text(400)}, ["advice"], {"advice": "가" * 400})["advice"] == "가" * 400
    with pytest.raises(ArgError, match="5개까지"):
        fit_args({"q": texts(200, 5)}, ["q"], {"q": ["질문"] * 6})
    with pytest.raises(ArgError, match=r"q\[1\].*200자"):
        fit_args({"q": texts(200, 5)}, ["q"], {"q": ["짧음", "나" * 201]})


def test_ai_written_fields_all_have_limits():
    """화면에 보이는 AI 글 칸은 모두 글자 수 제한이 도구 설명에 있다 (AI가 미리 알고 맞춰 쓰게)."""
    from app.agent import core
    from app.agent.tools import agent_tools
    from app.db import engine, init_db
    from sqlmodel import Session
    init_db()
    with Session(engine) as s:
        tools = agent_tools(s, 1, None, {})
    props = lambda n: tools[n].params  # noqa: E731
    for name, key in [("give_advice", "advice"), ("remember", "note"), ("ask_user", "question"), ("ask_user", "why"),
                      ("schedule_followup", "note"), ("set_post_status", "reason"), ("build_report", "summary")]:
        assert "maxLength" in props(name)[key], (name, key)
    assert props("ask_user")["options"]["items"]["maxLength"] and props("build_report")["points"]["items"]["maxLength"]
    assert core.JUDGE_ONE["reason"]["maxLength"] and core.JUDGE_ONE["fact"]["maxLength"]
