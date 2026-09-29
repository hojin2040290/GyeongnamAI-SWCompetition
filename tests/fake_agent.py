"""vLLM tool calling 흉내 (테스트용, 가상 응답).

실제 모델 대신 '정책' 함수가 목표와 지금까지 받은 도구 결과를 보고 다음에 부를 도구를 정한다.
에이전트 반복, 도구 실행, 기록, 검증 장치는 실제 코드가 그대로 돈다.
"""
import json
import re


def reply(calls: list[tuple[str, dict]] | None = None, content: str | None = None) -> dict:
    tool_calls = [{"id": f"call_{i}", "type": "function",
                   "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                  for i, (n, a) in enumerate(calls or [])]
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


class FakeAgent:
    """plan_first면 먼저 make_plan으로 계획을 세우고, 그다음부터 정책에 맡긴다 (정책에는 계획 결과를 빼고 보여 줌)."""
    def __init__(self, policy, plan_first: bool = True):
        self.policy, self.plan_first = policy, plan_first
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> dict:
        self.payloads.append(payload)
        msgs = payload["messages"]
        goal = msgs[1]["content"] if len(msgs) > 1 else ""
        done = [(m["name"], json.loads(m["content"])) for m in msgs if m["role"] == "tool"]
        tools = [t["function"]["name"] for t in payload.get("tools", [])]
        if self.plan_first and "make_plan" in tools:
            if not any(n == "make_plan" for n, _ in done):
                return reply([("make_plan", {"steps": ["기록 확인", "도구로 사실과 계산 확인", "판단하고 끝내기"]})],
                             "먼저 계획을 세울게요.")
            done = [(n, r) for n, r in done if n != "make_plan"]
        return self.policy(goal, done, tools)


def called(done: list, name: str):
    """이미 부른 도구의 마지막 결과 (안 불렀으면 None)."""
    hits = [r for n, r in done if n == name]
    return hits[-1] if hits else None


def smart_policy(goal: str, done: list, tools: list[str]) -> dict:
    """사건 목표에 맞게 도구를 차례로 부르는 가짜 AI."""
    names = [n for n, _ in done]
    if "check_rules" in tools:  # 계약서, 퇴근, 지원 전 점검: 모든 항목을 정상이라 해서 검증 장치가 되돌리는지 본다
        items = called(done, "check_rules")
        if items is None:
            return reply([("check_rules", {})], "먼저 검토할 항목을 받아 볼게요.")
        if "get_article" not in names:
            return reply([("get_article", {"label": items[0]["조항"]})])
        # 처음에는 모두 정상이라 하고, 검증 장치가 돌려보낸 항목은 확인 필요로 다시 판단한다
        fb = called(done, "finish") or {}
        flagged = {f["i"] for f in fb.get("검증 장치", [])}
        judgments = [{"i": it["i"], "status": "warn" if it["i"] in flagged else "ok", "law": it["조항"],
                      "fact": (it["사실"] or ["입력 정보"])[0],
                      "reason": "검증 장치 의견 반영" if it["i"] in flagged else "테스트"} for it in items]
        return reply([("finish", {"judgments": judgments, "extra_questions": ["주휴수당을 주나요"]})])
    if "compare_pay" in tools:
        month = re.search(r"\d{4}-\d{2}", goal).group()
        if "compare_pay" not in names:
            return reply([("compare_pay", {"month": month})])
        if "notify" not in names:
            return reply([("notify", {"title": f"{month} 급여를 적게 받은 것 같아요", "body": "AI가 쓴 급여 알림"})])
        return reply([("finish", {"status": "bad", "law": "근로기준법 제36조", "fact": called(done, "compare_pay")["사실"],
                                  "reason": "계산한 금액보다 적게 받았어요"})])
    if "get_overview" in tools:  # 매일 점검: 신고한 사업장이면 게시물 검색만
        ov = called(done, "get_overview")
        if ov is None:
            return reply([("get_overview", {})])
        if ov["신고함"] and "run_post_search" not in names:
            return reply([("run_post_search", {})])
        return reply([("finish", {"note": "필요한 점검을 했어요"})])
    if "build_report" in tools:
        if "get_saved_checks" not in names:
            return reply([("get_saved_checks", {})])
        if "build_report" not in names:
            return reply([("build_report", {"summary": "AI가 쓴 사건 요약", "points": ["물어볼 점"],
                                            "basis": ["근로기준법 제70조"]})])
        return reply([("finish", {"note": "만들었어요"})])
    if "save_warning_message" in tools and "list_posts" not in tools:  # 신고했어요 켬
        if "save_warning_message" not in names:
            return reply([("get_article", {"label": "근로기준법 제104조 제2항"}),
                          ("save_warning_message", {"message": "AI가 쓴 안내 문구", "laws": ["근로기준법 제104조 제2항"]})])
        if "notify" not in names:
            return reply([("notify", {"title": "보복 대응을 시작했어요", "body": "AI가 안내 문구를 준비했어요"})])
        return reply([("finish", {"note": "문구 저장"})])
    if "list_posts" in tools:  # 게시물 검색, 판별
        if "search_posts" in tools and "search_posts" not in names:
            return reply([("search_posts", {})])
        posts = called(done, "list_posts")
        if posts is None:
            return reply([("list_posts", {"pending_only": True})])
        if "set_post_status" not in names and posts:
            return reply([("set_post_status", {"post_id": p["post_id"], "reason": "테스트 판별",
                                               "status": "suspect" if "신고" in p["제목"] else "ok"}) for p in posts])
        if any(p["제목"] and "신고" in p["제목"] for p in posts) and "notify" not in names:
            return reply([("notify", {"title": "보복이 의심되는 게시물이 있어요", "body": "보호 탭에서 확인해 보세요"})])
        return reply([("finish", {"note": "판별 끝"})])
    if "settlement" in tools:
        if "settlement" not in names:
            return reply([("settlement", {})])
        return reply([("finish", {"status": "warn", "law": "근로기준법 제36조", "fact": "지급 기한 확인", "reason": "테스트"})])
    return reply([("finish", {})])
