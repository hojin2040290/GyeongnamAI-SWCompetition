"""에이전트 판단 반복 (vLLM tool calling).

AI에게는 사건의 목표와 쓸 수 있는 도구 목록만 준다.
1. AI가 도구를 고른다  2. 코드가 도구를 실행한다  3. 결과를 AI에게 돌려준다  4. AI가 다음 행동을 정하거나 finish로 끝낸다.
반복은 MAX_STEPS회로 제한하고, 매 단계의 AI 판단과 도구 호출을 동작 기록(AgentLog)에 남긴다.
AI가 없거나 응답하지 않으면 None을 돌려주고, 부른 쪽은 사실만 정리해 'AI 응답 대기 중'으로 둔다.
"""
import json
from dataclasses import dataclass, field
from typing import Callable

from app.llm import client

MAX_STEPS = 10  # AI 판단 반복 최대 횟수
REFLECT_MAX = 2  # 검증 장치가 판단을 돌려보내 다시 판단하게 하는 최대 횟수
RESULT_MAX = 6000  # AI에게 돌려주는 도구 결과 글자 수
AI_WAITING = "AI 응답 대기 중"

AGENT_SYSTEM = (
    "당신은 청소년 아르바이트 근로권익을 점검하고 보호하는 AI 에이전트 '알바지킴이'입니다. "
    "주어진 목표를 이루기 위해 도구를 골라 쓰고, 도구 결과를 보고 다음 행동을 정합니다.\n"
    "규칙:\n"
    "- 시간, 금액, 나이 같은 숫자는 직접 계산하지 말고 계산 도구의 결과만 쓰세요.\n"
    "- 법 조항은 get_article로 법 기준표에서 확인한 것만 근거로 드세요. 기억으로 조문을 쓰지 마세요.\n"
    "- 판단에 필요한 정보가 기록에 없으면 추측하지 말고 확인 필요(warn)로 두세요.\n"
    "- 판단마다 근거로 쓴 조항(law)과 사실(fact)을 함께 내세요.\n"
    "- 법적 판단을 확정하지 말고 참고 의견으로 쓰세요. 사용자에게 보내는 글은 청소년이 이해하기 쉬운 존댓말로 쓰세요.\n"
    "- 목표를 이루면 finish 도구로 끝내세요.")


@dataclass
class Tool:
    """AI가 고를 수 있는 도구. 사용자 번호와 사업장 번호는 입력에 없고 코드가 고정한다."""
    name: str
    desc: str
    fn: Callable
    params: dict = field(default_factory=dict)  # {"month": {"type": "string", "description": "..."}}
    required: list = field(default_factory=list)

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.desc,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


@dataclass
class Goal:
    """사건 하나의 목표. finish는 AI가 끝낼 때 내는 결과의 모양, check는 끝내기 전에 코드가 확인할 것."""
    text: str
    tools: list[str]
    finish: dict = field(default_factory=dict)
    finish_required: list = field(default_factory=list)
    check: Callable[[dict], str | None] | None = None  # 문제가 있으면 AI에게 돌려줄 말
    review: Callable[[dict], list[dict]] | None = None  # 검증 장치: 판단마다 문제를 찾아 AI에게 돌려준다

    def finish_spec(self) -> dict:
        return Tool("finish", "목표를 이뤘을 때 결과를 내고 끝낸다.", lambda **_: None,
                    self.finish, self.finish_required).spec()


def _args(call: dict) -> dict:
    raw = call.get("function", {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
    except json.JSONDecodeError:
        return {"_bad": raw[:200]}
    return v if isinstance(v, dict) else {"_bad": raw[:200]}


def _dump(v) -> str:
    text = json.dumps(v, ensure_ascii=False, default=str)
    return text if len(text) <= RESULT_MAX else text[:RESULT_MAX] + "…(생략)"


def use_tool(run, tools: dict[str, Tool], allowed: list[str], name: str, args: dict):
    """AI가 고른 도구를 코드가 실행한다. 목표에 없는 도구, 잘못된 입력, 권한 오류는 AI에게 오류로 돌려준다."""
    if name not in allowed or name not in tools:
        result = {"error": f"이 목표에서 쓸 수 없는 도구예요: {name}"}
    elif "_bad" in args:
        result = {"error": "도구 입력을 JSON으로 읽지 못했어요"}
    else:
        try:
            result = tools[name].fn(**args)
        except PermissionError as exc:
            result = {"error": str(exc)}
        except (TypeError, ValueError, KeyError) as exc:
            result = {"error": f"도구 입력이 맞지 않아요 ({type(exc).__name__}: {str(exc)[:120]})"}
    run.log(f"도구 {name}", f"입력 {_dump(args)[:150]} → {_dump(result)[:250]}")
    return result


def run_agent(run, goal: Goal, tools: dict[str, Tool], context: dict) -> dict | None:
    """AI가 목표를 이룰 때까지 도구를 고르고 실행하는 반복. 끝내면 finish 입력을, 못 하면 None을 돌려준다."""
    if not client.available():
        run.log("AI", f"{AI_WAITING}: AI 모델이 연결되지 않았어요")
        return None
    specs = [tools[n].spec() for n in goal.tools if n in tools] + [goal.finish_spec()]
    messages = [{"role": "system", "content": AGENT_SYSTEM},
                {"role": "user", "content": f"목표: {goal.text}\n상황: {_dump(context)}"}]
    for turn in range(1, MAX_STEPS + 1):
        try:
            msg = client.chat(messages, tools=specs)
        except client.LLMError as exc:
            run.ai_error = str(exc)
            run.log("AI 응답 없음", f"{AI_WAITING}: {exc}")
            return None
        calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        run.log(f"AI 판단 {turn}", content.strip() or "도구 선택: " + ", ".join(c["function"]["name"] for c in calls))
        if not calls:
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": "도구를 쓰거나, 목표를 이뤘으면 finish 도구로 끝내 주세요."}]
            continue
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls:
            name, args = call["function"]["name"], _args(call)
            if name == "finish":
                problem = goal.check(args) if goal.check else None
                feedback = goal.review(args) if goal.review and problem is None else []
                if problem is None and feedback and run.state.get("reflect", 0) < REFLECT_MAX:
                    run.state["reflect"] = run.state.get("reflect", 0) + 1
                    result = {"검증 장치": feedback,
                              "요청": "문제가 된 판단의 근거를 다시 확인하고 finish로 다시 판단해 주세요. "
                                      "다시 봐도 같다면 이유를 reason에 적어 같은 판단을 내도 돼요."}
                    run.log(f"검증 장치 {run.state['reflect']}", "다시 판단 요청: " + "; ".join(
                        f"{f.get('i', '')} {f['문제']}" for f in feedback)[:280])
                elif problem is None:
                    run.log("AI 끝냄", _dump(args)[:300])
                    return args
                else:
                    result = {"error": problem}
                    run.log("끝내기 전 확인", problem)
            else:
                result = use_tool(run, tools, goal.tools, name, args)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": _dump(result)})
    run.ai_error = f"반복 {MAX_STEPS}회 안에 끝내지 못했어요"
    run.log("멈춤", f"{AI_WAITING}: {run.ai_error}")
    return None
