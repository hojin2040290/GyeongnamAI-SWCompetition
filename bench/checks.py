"""모델을 켠 뒤 시험 전에 하는 자동 점검 (bench/run.py가 부른다). 표준 라이브러리만 쓴다 (vLLM 가상환경에서 실행).

모델마다 공식 문서대로 켜도 실제로는 다르게 동작할 수 있어, 시험 전에 짧은 요청으로 확인하고 고칠 수 있는 것은 고친다.
1. 생각 모드: 끄는 옵션을 보냈는데도 생각 글이 오면, 대화 틀에서 찾은 다른 끄기 옵션을 차례로 시도한다.
2. 도구 호출: 파서가 도구 호출을 읽는지 (auto와 지정 둘 다). 못 읽으면 run.py가 다음 후보 파서로 다시 켠다.
3. 사진 읽기: 계약서 사진 한 장을 JSON으로 답하는지, 길이 제한에 걸리는지.
"""
import base64
import copy
import json
import re
import time
import urllib.request
from pathlib import Path

# 답(content)에 섞여 오는 생각 글 표시 (Qwen·EXAONE·HCX <think>, Mistral [THINK], Gemma 4 thought 채널)
THINK_MARKS = re.compile(r"<think>|</think>|\[THINK\]|\[/THINK\]|<\|channel>thought")
THINK_BLOCKS = [(r"<think>", r"</think>"), (r"\[THINK\]", r"\[/THINK\]"), (r"<\|channel>thought", r"<channel\|>")]

# 대화 틀(chat template)에 이 이름이 있으면 그 방법으로 생각 모드를 끌 수 있다 (모델 공식 문서에 나온 방식들)
OFF_SWITCHES = [
    ("enable_thinking", {"chat_template_kwargs": {"enable_thinking": False}}),  # Qwen3·EXAONE 4.5·GLM·Gemma 4
    ("thinking", {"chat_template_kwargs": {"thinking": False}}),
    ("skip_reasoning", {"chat_template_kwargs": {"skip_reasoning": True}}),
    ("force_reasoning", {"chat_template_kwargs": {"force_reasoning": False}}),
    ("reasoning_effort", {"chat_template_kwargs": {"reasoning_effort": "none"}}),
    ("reasoning_effort", {"reasoning_effort": "none"}),  # Mistral Small 4 (요청 칸)
]

NOTE_TOOL = {"type": "function", "function": {
    "name": "save_note", "description": "메모를 저장한다",
    "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "저장할 글"}},
                   "required": ["text"]}}}


def merge(a: dict, b: dict) -> dict:
    """b를 a 위에 겹친다 (chat_template_kwargs 같은 안쪽 칸도 합친다)."""
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def with_sampling(body: dict, sampling: dict) -> dict:
    """앱(client._post)과 같은 규칙: 값이 null이면 그 칸을 빼서 모델 기본값(generation_config.json)을 쓴다."""
    out = dict(body)
    for k, v in sampling.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def ask(port: int, body: dict, timeout: int = 300) -> dict:
    """요청 하나. 실패해도 예외 대신 error를 돌려준다."""
    t0 = time.time()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                     json.dumps({"model": "bench", **body}).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        ch = data["choices"][0]
        msg = ch.get("message") or {}
        return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or [],
                "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
                "finish": ch.get("finish_reason"), "tokens": (data.get("usage") or {}).get("completion_tokens"),
                "sec": round(time.time() - t0, 1), "error": None}
    except Exception as exc:  # noqa: BLE001
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode(errors="ignore")[:300]
            except Exception:  # noqa: BLE001
                pass
        return {"content": "", "tool_calls": [], "reasoning": "", "finish": None, "tokens": None,
                "sec": round(time.time() - t0, 1), "error": f"{type(exc).__name__}: {exc} {detail}"[:400]}


def thinking_in(r: dict) -> bool:
    return bool(r["reasoning"]) or bool(THINK_MARKS.search(r["content"]))


def strip_thinking(text: str) -> str:
    for opener, closer in THINK_BLOCKS:
        text = re.sub(opener + r".*?" + closer, "", text, flags=re.S)
        m = list(re.finditer(closer, text))
        if m:
            text = text[m[-1].end():]
        text = re.sub(opener + r".*", "", text, flags=re.S)
    return re.sub(r"<\|(begin|end)_of_box\|>", "", text)


def read_json(text: str):
    text = strip_thinking(text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def off_candidates(template: str) -> list[dict]:
    """대화 틀에 있는 이름의 끄기 옵션을 먼저, 나머지를 뒤에 (대화 틀을 못 읽은 모델도 시도해 보게)."""
    found = [patch for name, patch in OFF_SWITCHES if re.search(rf"\b{name}\b", template or "")]
    return found + [patch for _, patch in OFF_SWITCHES if patch not in found]


def brief(r: dict) -> dict:
    return {"finish": r["finish"], "tokens": r["tokens"], "sec": r["sec"], "error": r["error"],
            "reasoning_chars": len(r["reasoning"]), "content_head": r["content"][:300]}


def check_thinking(port: int, extra: dict, sampling: dict, template: str) -> dict:
    body = {"messages": [{"role": "user", "content": "최저임금이 무엇인지 한 문장으로 답하세요."}],
            "temperature": 0, "max_tokens": 1024}
    first = ask(port, merge(with_sampling(body, sampling), extra))
    out = {"sent": extra, "first": brief(first), "thinking": thinking_in(first), "extra_body": extra, "tried": []}
    if first["error"] or not out["thinking"]:
        out["ok"] = not first["error"]
        return out
    for patch in off_candidates(template):
        cand = merge(extra, patch)
        if cand == extra:
            continue
        r = ask(port, merge(with_sampling(body, sampling), cand))
        out["tried"].append({"extra_body": cand, **brief(r), "thinking": thinking_in(r)})
        if not r["error"] and not thinking_in(r):
            out.update(ok=True, fixed=True, extra_body=cand)
            return out
    out["ok"] = False  # 끄지 못함: 시험은 그대로 하고 비교표에 남긴다
    return out


def check_tools(port: int, extra: dict, sampling: dict) -> dict:
    msgs = [{"role": "system", "content": "도구만 부르세요. 글로 설명하지 마세요."},
            {"role": "user", "content": "save_note 도구로 '근무 기록 점검'이라는 글을 저장하세요."}]
    out = {}
    for mode, choice in (("auto", "auto"), ("forced", {"type": "function", "function": {"name": "save_note"}})):
        r = ask(port, merge(with_sampling({"messages": msgs, "tools": [NOTE_TOOL], "tool_choice": choice,
                                          "temperature": 0, "max_tokens": 1024}, sampling), extra))
        ok = False
        for c in r["tool_calls"]:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = None
            ok = ok or (fn.get("name") == "save_note" and isinstance(args, dict) and isinstance(args.get("text"), str))
        out[mode] = {"ok": ok, "calls": len(r["tool_calls"]), **brief(r),
                     "raw_tool_text": bool(re.search(r"<tool_call>|\"arguments\"|\[TOOL_CALLS\]|\[\w+\(", r["content"]))}
    out["ok"] = out["auto"]["ok"]
    return out


def check_vision(port: int, extra: dict, sampling: dict, image: Path) -> dict:
    if not image.exists():
        return {"ok": False, "error": f"사진이 없어요: {image}"}
    url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    prompt = ('한국 근로계약서 사진입니다. {"임금": "", "근무장소": ""} 형태의 JSON 하나로만 답하세요. '
              "사진에 적힌 글자를 그대로 옮기고 설명하지 마세요.")
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": url}}]}],
            "temperature": 0, "max_tokens": 1024}
    r = ask(port, merge(with_sampling(body, sampling), extra))
    got = read_json(r["content"])
    return {"ok": isinstance(got, dict) and r["finish"] != "length", "json": got, **brief(r),
            "content_tail": r["content"][-200:] if len(r["content"]) > 500 else ""}


def run_all(port: int, extra: dict, sampling: dict, template: str, image: Path) -> dict:
    """세 점검을 차례로. 생각 모드를 고쳤으면 고친 옵션으로 나머지를 점검한다."""
    think = check_thinking(port, extra, sampling, template)
    extra = think.get("extra_body", extra)
    return {"thinking": think, "tools": check_tools(port, extra, sampling),
            "vision": check_vision(port, extra, sampling, image), "extra_body": extra}
