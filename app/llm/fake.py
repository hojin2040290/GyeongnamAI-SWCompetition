"""가짜 AI (시험용). .env에 LLM_FAKE=true를 넣으면 실제 모델 대신 이 코드가 답한다.

vLLM과 같은 모양(OpenAI 호환 응답)으로 답하므로 에이전트 반복, 도구 실행, 검증 장치, 기록, 화면은 실제 코드가 그대로 돈다.
- 계획(make_plan) → 목표에 나온 도구를 차례로 부름 → 메모와 조언 → finish로 끝냄
- AI가 쓰는 글은 모두 '테스트 답변입니다 (AI에게 넘긴 내용: …)' 모양이라, 화면에서 AI 몫이 어디에 들어가는지 알아볼 수 있다
- 법 판단은 하지 않는다: 판단은 모두 확인 필요(warn), 근거 조항과 사실은 도구 결과에 있는 것을 그대로 옮긴다
- 숫자를 만들어 내지 않는다: 사진 읽기에서 금액은 비워 둔다
실제 서비스에서는 쓰지 않는다 (LLM_FAKE=false).
"""
import json
import re
import time

PREFIX = "테스트 답변입니다"
# 목표에 이름이 나와도 가짜 AI가 부르지 않는 도구 (사용자에게 묻기, 예약, 다른 점검 실행 등은 실제 AI가 판단할 몫)
SKIP = {"make_plan", "finish", "ask_user", "get_answers", "schedule_followup", "set_post_status",
        "run_pay_check", "run_quit_check", "run_post_search", "preserve_post"}
TEXT_MAX = 200


def _short(text: str, n: int = 40) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n] + "…"


def _law_re() -> re.Pattern:
    """law_params.json에 나오는 법 이름으로만 조항 이름을 찾는다 (예: 근로기준법 제36조, 청소년 보호법 제29조)."""
    from app.calc.params import P
    from app.law.lookup import _param_laws, parse_label
    names = sorted({parse_label(x)[0] for x in _param_laws(P()) if parse_label(x)}, key=len, reverse=True)
    return re.compile(f"(?:{'|'.join(map(re.escape, names))})(?: 시행령)? 제\\d+조(?:의\\d+)?(?: 제\\d+항)?")


def _laws(texts: list[str]) -> list[str]:
    """글에 나온 조항 이름을 나온 순서대로, 겹치지 않게."""
    pattern, out = _law_re(), []
    for t in texts:
        for m in pattern.findall(t or ""):
            if m not in out:
                out.append(m)
    return out


class Scene:
    """지금까지의 대화에서 가짜 AI가 볼 것: 목표, 쓸 수 있는 도구, 부른 도구와 결과."""
    def __init__(self, payload: dict):
        self.msgs = payload.get("messages", [])
        self.tools = {t["function"]["name"]: t["function"] for t in payload.get("tools", [])}
        first = next((m for m in self.msgs if m.get("role") == "user"), {})
        self.goal = first.get("content") if isinstance(first.get("content"), str) else ""
        self.done: list[tuple[str, object]] = []
        for m in self.msgs:
            if m.get("role") == "tool":
                try:
                    self.done.append((m.get("name", ""), json.loads(m.get("content") or "null")))
                except json.JSONDecodeError:
                    self.done.append((m.get("name", ""), m.get("content")))
        self.names = [n for n, _ in self.done]

    def result(self, name: str):
        hits = [r for n, r in self.done if n == name]
        return hits[-1] if hits else None

    def text(self, what: str, limit: int = TEXT_MAX) -> str:
        """AI가 쓰는 글: 무엇에 대한 답인지와 AI에게 넘긴 내용을 괄호에 적는다.
        칸의 글자 수(limit)를 넘으면 도구 목록을 줄인다 (끝을 잘라 괄호가 끊기지 않게)."""
        goal = _short(self.goal.split("\n")[0].removeprefix("목표: "))
        used = list(dict.fromkeys(n for n in self.names if n != "make_plan"))
        for k in range(len(used), -1, -1):
            names = ", ".join(used[:k]) + (f" 외 {len(used) - k}개" if k < len(used) else "") if used else "없음"
            out = f"{PREFIX} ({what}. AI에게 넘긴 내용: 목표 '{goal}', 받은 도구 결과: {names})"
            if len(out) <= limit:
                return out
        out = f"{PREFIX} ({what})"
        return out if len(out) <= limit else PREFIX[:limit]

    def law_candidates(self) -> list[str]:
        """도구 결과의 '관련 조항', 검토 항목의 '조항', 그 밖에 대화에 나온 조항 이름 순서."""
        texts = []
        for _, r in self.done:
            if isinstance(r, dict) and r.get("관련 조항"):
                texts += [str(x) for x in r["관련 조항"]]
        for _, r in self.done:
            if isinstance(r, list):
                texts += [str(x.get("조항", "")) for x in r if isinstance(x, dict)]
        texts += [json.dumps(r, ensure_ascii=False) for _, r in self.done]
        texts += [m["content"] for m in self.msgs if isinstance(m.get("content"), str)]
        return _laws(texts)

    def plan(self) -> list[str]:
        """목표 글에 이름이 나온 도구를 나온 순서대로, 끝에 메모와 조언."""
        found = sorted(((self.goal.find(n), n) for n in self.tools if n not in SKIP and self.goal.find(n) >= 0))
        order = [n for _, n in found]
        if "build_report" in order and "get_saved_checks" in self.tools:  # 문서에 넣을 근거 조항을 먼저 확인
            order.insert(order.index("build_report"), "get_saved_checks")
        if "set_post_status" in self.tools and "list_posts" in self.tools and "list_posts" not in order:
            order.append("list_posts")
        if "notify" in self.tools and "notify" not in order and re.search(r"알림|알려|알리", self.goal):
            order.append("notify")  # 목표가 '사용자에게 알려 주세요'라고 할 때
        order += [n for n in ("remember", "give_advice") if n in self.tools and n not in order]
        return order


def _month(scene: Scene) -> str | None:
    m = re.search(r"\d{4}-\d{2}", scene.goal)
    return m.group() if m else None


def _args(scene: Scene, name: str) -> dict | None:
    """도구 설명을 보고 입력을 채운다. 꼭 필요한 입력을 채울 수 없으면 None (그 도구는 건너뜀)."""
    fn = scene.tools[name]
    props = fn.get("parameters", {}).get("properties", {})
    required = fn.get("parameters", {}).get("required", [])
    laws = scene.law_candidates()
    out: dict = {}
    for key, spec in props.items():
        kind = spec.get("type")
        if key == "month":
            value = _month(scene)
        elif key == "label":
            value = laws[0] if laws else None
        elif kind == "array" and key in ("laws", "basis"):
            value = laws[:2]  # 찾은 조항이 없으면 빈 목록 (지어내지 않음)
        elif kind == "array":
            value = [scene.text(f"{name}의 {key}", spec.get("items", {}).get("maxLength", TEXT_MAX))]
        elif kind == "string" and spec.get("enum"):
            value = next((x for x in ("warn", "unclear") if x in spec["enum"]), spec["enum"][0])
        elif kind == "string" and key == "title":  # 제목은 짧게 (알림 제목은 100자까지)
            value = f"{PREFIX} ({name}의 제목)"
        elif kind == "string":
            value = scene.text(f"{name}의 {key}", spec.get("maxLength", TEXT_MAX))
        elif kind == "boolean":
            value = True
        else:
            value = None  # 번호 같은 값은 지어내지 않는다
        if value is not None:
            out[key] = value
    return out if all(k in out for k in required) else None


def _fit_fact(fact: str, n: int) -> str:
    """근거 사실이 칸보다 길면 앞에서부터 사실 단위('; ')로 들어가는 만큼만 (글자 중간에서 자르지 않게)."""
    out = ""
    for part in str(fact).split("; "):
        if len(out) + len(part) + 2 > n:
            break
        out = f"{out}; {part}" if out else part
    return out or str(fact)[:n]


def _finish(scene: Scene) -> dict:
    spec = scene.tools.get("finish", {}).get("parameters", {}).get("properties", {})
    out: dict = {}
    if "judgments" in spec:
        items = scene.result("check_rules")
        out["judgments"] = [{"i": it["i"], "status": "warn", "law": it["조항"],
                             "fact": str((it.get("사실") or [it.get("자료") or "입력 정보"])[0])[:200],
                             "reason": scene.text(f"{it['조항']} 판단 이유")}
                            for it in items if isinstance(it, dict) and "i" in it] if isinstance(items, list) else []
    if "status" in spec:  # 판단 하나 (급여, 퇴직 정산)
        laws = scene.law_candidates()
        facts = [r.get("사실") or (f"지급 기한 {r['지급 기한']}" if r.get("지급 기한") else "")
                 for _, r in scene.done if isinstance(r, dict)]
        fact = _fit_fact(next((f for f in facts if f), "기록 확인"), spec.get("fact", {}).get("maxLength", 300))
        out.update(status="warn", law=laws[0] if laws else "", fact=fact,
                   reason=scene.text("판단 이유"))
    if "headline" in spec:  # 종합 점검: 홈에 크게 보일 한 줄 결론
        out["headline"] = scene.text("한 줄 결론", spec["headline"].get("maxLength", TEXT_MAX))
    if "note" in spec:
        out["note"] = scene.text("한 일 요약")
    if "extra_questions" in spec:
        out["extra_questions"] = [scene.text("상담 때 물어볼 점")]
    return out


def _reply(calls: list[tuple[str, dict]], content: str) -> dict:
    tool_calls = [{"id": f"fake_{i}", "type": "function",
                   "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                  for i, (n, a) in enumerate(calls)]
    return {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}


def agent_step(scene: Scene) -> dict:
    """에이전트 반복의 한 단계: 계획 → 도구 → finish."""
    plan = scene.plan()
    if "make_plan" in scene.tools and "make_plan" not in scene.names:
        most = scene.tools["make_plan"].get("parameters", {}).get("properties", {}).get("steps", {}).get("maxItems", 99)
        steps = [f"{PREFIX} ({n} 부르기)" for n in plan][:most - 1] + [f"{PREFIX} (finish로 끝내기)"]  # 단계 수 제한 안에서
        return _reply([("make_plan", {"steps": steps})], scene.text("먼저 계획 세우기"))
    for name in plan:
        if name in scene.names:
            continue
        args = _args(scene, name)
        if args is not None:
            return _reply([(name, args)], scene.text(f"{name} 부르기"))
    posts = scene.result("list_posts")
    if "set_post_status" in scene.tools and "set_post_status" not in scene.names and isinstance(posts, dict):
        calls = [("set_post_status", {"post_id": p["post_id"], "status": "unclear", "reason": scene.text("게시물 판별 근거")})
                 for p in posts.get("게시물", []) if isinstance(p, dict) and "post_id" in p]
        if calls:
            return _reply(calls, scene.text("게시물 판별"))
    return _reply([("finish", _finish(scene))], scene.text("목표를 이뤄 끝내기"))


def read_image(payload: dict) -> dict:
    """사진 읽기: 요청한 항목마다 테스트 글을 채운다. 금액 같은 숫자는 비워 둔다."""
    parts = payload["messages"][0]["content"]
    prompt = next((p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text"), "")
    image = next((p for p in parts if isinstance(p, dict) and p.get("type") == "image_url"), {})
    size = len(image.get("image_url", {}).get("url", "")) * 3 // 4
    if "month" in prompt:  # 급여명세서
        answer = {"month": "", "net_pay": None, "base_pay": None, "weekly_holiday_pay": None, "deduction": None}
    else:  # 근로계약서: 프롬프트의 {"임금", "근로시간", ...}
        braces = re.search(r"\{([^{}]*)\}", prompt)
        keys = re.findall(r'"([^"]+)"', braces.group(1)) if braces else []
        # 칸마다 쓸 수 있는 글자가 정해져 있어 한글, 숫자, 쉼표만 쓴다
        answer = {k: f"{PREFIX}, 사진 속 {k} 칸, 사진 {size // 1024}킬로바이트를 넘김" for k in keys}
    return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}


def respond(payload: dict) -> dict:
    """vLLM /v1/chat/completions와 같은 모양의 응답. 요청을 받고 LLM_FAKE_DELAY초(기본 3초) 뒤에 답한다."""
    from app.config import LLM_FAKE_DELAY
    if LLM_FAKE_DELAY:
        time.sleep(LLM_FAKE_DELAY)
    msgs = payload.get("messages", [])
    if msgs and isinstance(msgs[0].get("content"), list):
        message = read_image(payload)
    elif payload.get("tools"):
        message = agent_step(Scene(payload))
    else:
        message = {"role": "assistant", "content": json.dumps({"답": f"{PREFIX} (AI에게 넘긴 내용: {_short(msgs[-1].get('content') if msgs else '')})"},
                                                             ensure_ascii=False)}
    return {"choices": [{"message": message}]}
