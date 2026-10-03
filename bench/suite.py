"""모델 하나를 시험한다 (bench/run.py가 vLLM을 켠 뒤 부른다). 앱 코드를 그대로 써서 실제와 같은 요청을 보낸다.

1. 속도: 한국어 질문 3개를 스트리밍으로 보내 첫 글자까지 시간(TTFT), 초당 생성 토큰, 전체 시간, 토큰 수
2. 사진 읽기: 계약서 4장, 명세서 3장 (테스트자료) → 항목이 정답과 맞는 비율
3. 에이전트: 시험 데이터(알바 5개)로 계약서 점검, 급여 점검, 퇴직 정산, 상담 자료, 신고 후 보호를 실제 앱 흐름으로 실행
   → 끝까지 마친 비율, 반복 횟수, 검증 장치 돌려보냄, 반복 한도 초과, 요청마다 시간과 토큰, 도구 호출 형식 오류,
     코드 규칙이 분명한 항목에서 AI 판단이 맞은 비율, 한국어 비율, 조문을 사용자에게 물은 횟수
결과는 JSON 하나로 쓴다 (bench/report.py가 모아 비교표를 만든다).

사용: python bench/suite.py --key qwen3vl --base-url http://127.0.0.1:8100/v1 --model bench --out bench/results/qwen3vl.json
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIAL = ROOT / "테스트자료"

SPEED_PROMPTS = [
    ("짧은 답", "근로기준법의 휴게시간 규정을 청소년이 이해하기 쉽게 한 문장으로 설명해 주세요.", 160),
    ("보통 답", "청소년 아르바이트생이 근로계약서를 쓸 때 꼭 확인할 점 다섯 가지를 존댓말로 설명해 주세요.", 600),
    ("긴 답", "편의점에서 일한 만 17세 아르바이트생이 주휴수당을 받지 못했어요. 노동 상담 기관에 가져갈 상담 사전 자료의 "
              "상황 요약과 상담 때 물어볼 점을 1000자 정도로 써 주세요. 숫자는 지어내지 마세요.", 1200),
]

# 사진 읽기 정답: 항목마다 들어 있어야 할 글자 (빈 문자열이면 비어 있어야 정답)
CONTRACTS = {
    "02_근로계약서.png": {"임금": "9288", "근로시간": "17", "휴게시간": "", "휴일": "일요일", "근무장소": "도계동",
                       "업무내용": "편의점", "연차휴가": "법령"},
    "알바5개/2_근로계약서.png": {"임금": "10320", "근로시간": "17", "휴게시간": "19", "휴일": "일요일", "근무장소": "시험로10",
                            "업무내용": "서빙", "연차휴가": "법령"},
    "알바5개/3_근로계약서.png": {"임금": "11000", "근로시간": "15", "휴게시간": "없음", "휴일": "일요일", "근무장소": "시험로20",
                            "업무내용": "음료", "연차휴가": "법령"},
    "알바5개/5_근로계약서.png": {"임금": "10320", "근로시간": "18", "휴게시간": "없음", "휴일": "일요일", "근무장소": "시험로30",
                            "업무내용": "포장", "연차휴가": "법령"},
}
PAYSLIPS = {
    "06_급여명세서.png": {"month": "2026-09", "net_pay": 557280, "base_pay": 557280, "weekly_holiday_pay": 0, "deduction": 0},
    "알바5개/2_급여명세서.png": {"month": "2026-08", "net_pay": 753360, "base_pay": 753360, "weekly_holiday_pay": 0, "deduction": 0},
    "알바5개/3_급여명세서.png": {"month": "2026-08", "net_pay": 308000, "base_pay": 308000, "weekly_holiday_pay": 0, "deduction": 0},
}

# 에이전트 흐름 (시험 데이터의 알바 이름으로 찾는다)
FLOWS = [("contract", "행복편의점 도계점", ""), ("contract", "가상분식 시험점", ""), ("contract", "가상카페 시험점", ""),
         ("contract", "가상베이커리 시험점", ""), ("contract", "가상치킨 시험점", ""),
         ("payday", "행복편의점 도계점", "2026-09"), ("payday", "가상분식 시험점", "2026-08"),
         ("payday", "가상카페 시험점", "2026-08"), ("payday", "가상베이커리 시험점", "2026-09"),
         ("quit", "행복편의점 도계점", ""), ("quit", "가상치킨 시험점", ""),
         ("report", "가상분식 시험점", ""), ("guard", "가상치킨 시험점", "")]

REC: dict = {"current": "", "requests": []}


# ---------- 공통 ----------
def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 2)


def text_stats(texts: list[str]) -> dict:
    """AI가 쓴 글의 한글, 영문, 한자 비율 (한자가 섞이면 중국어가 섞였을 수 있다)."""
    joined = "".join(texts)
    hangul = len(re.findall(r"[가-힣]", joined))
    latin = len(re.findall(r"[A-Za-z]", joined))
    han = len(re.findall(r"[一-鿿]", joined))
    letters = hangul + latin + han
    return {"chars": len(joined), "hangul_ratio": round(hangul / letters, 3) if letters else None, "han_chars": han}


def strings_in(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in strings_in(x)]
    if isinstance(v, list):
        return [s for x in v for s in strings_in(x)]
    return []


def record(payload: dict, data, err: str | None, sec: float) -> None:
    """요청 하나의 시간, 토큰, 도구 호출 형식을 남긴다."""
    e = {"flow": REC["current"], "sec": round(sec, 3), "error": err, "tools_offered": bool(payload.get("tools")),
         "forced": isinstance(payload.get("tool_choice"), dict),
         "image": any(isinstance(m.get("content"), list) for m in payload.get("messages", []))}
    if data:
        usage = data.get("usage") or {}
        e["prompt_tokens"], e["completion_tokens"] = usage.get("prompt_tokens"), usage.get("completion_tokens")
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            msg = {}
        calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        bad = 0
        texts = [content]
        for c in calls:
            raw = (c.get("function") or {}).get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                texts += strings_in(args)
            except json.JSONDecodeError:
                bad += 1
        try:
            finish = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            finish = None
        e.update(n_tool_calls=len(calls), bad_args=bad, texts=[t for t in texts if t], finish_reason=finish,
                 # 도구를 줬는데 도구 호출 대신 글 속에 호출 모양이 들어 있으면 파서가 읽지 못한 것
                 raw_tool_text=bool(payload.get("tools") and not calls and re.search(r"<tool_call>|\"arguments\"|\[\w+\(", content)),
                 reasoning_chars=len(msg.get("reasoning_content") or msg.get("reasoning") or ""))
        # 사진 읽기와 길이 제한에 걸린 답은 원문 앞뒤를 남긴다 (JSON을 못 읽은 이유를 보려고)
        if e["image"] or finish == "length":
            e["content_chars"] = len(content)
            e["content_head"] = content[:600]
            e["content_tail"] = content[-300:] if len(content) > 900 else ""
    REC["requests"].append(e)


def install_recorder(client) -> None:
    orig = client._post

    def rec_post(payload: dict) -> dict:
        t0, data, err = time.time(), None, None
        try:
            data = orig(payload)
            return data
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            record(payload, data, err, time.time() - t0)
    client._post = rec_post


# ---------- 1. 속도 ----------
def speed_test(base_url: str, model: str, extra: dict, sampling: dict) -> list[dict]:
    import httpx
    out = []
    for label, prompt, max_tokens in SPEED_PROMPTS:
        for n in range(2):
            payload = {**extra, "model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                       "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}
            for k, v in sampling.items():  # 앱(client._post)과 같은 규칙으로 공식 권장 생성 설정을 쓴다
                if v is None:
                    payload.pop(k, None)
                else:
                    payload[k] = v
            t0 = time.time()
            ttft, usage, content, reasoning, err = None, {}, "", "", None
            try:
                with httpx.stream("POST", f"{base_url.rstrip('/')}/chat/completions", json=payload, timeout=600) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line.startswith("data:") or line.strip() == "data: [DONE]":
                            continue
                        chunk = json.loads(line[5:])
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for ch in chunk.get("choices") or []:
                            d = ch.get("delta") or {}
                            piece = d.get("content") or ""
                            rpiece = d.get("reasoning_content") or d.get("reasoning") or ""
                            if (piece or rpiece) and ttft is None:
                                ttft = time.time() - t0
                            content += piece
                            reasoning += rpiece
            except Exception as exc:  # noqa: BLE001 (모델마다 실패 이유를 남기고 계속)
                err = f"{type(exc).__name__}: {exc}"[:300]
            total = time.time() - t0
            ct = usage.get("completion_tokens")
            gen = (total - ttft) if ttft else None
            out.append({"label": label, "run": n + 1, "error": err, "ttft": round(ttft, 3) if ttft else None,
                        "total": round(total, 2), "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": ct,
                        "tok_per_s": round(ct / gen, 1) if ct and gen and gen > 0 else None,
                        "content_chars": len(content), "reasoning_chars": len(reasoning), "sample": content[:400],
                        **text_stats([content])})
    return out


# ---------- 2. 사진 읽기 ----------
def norm(s) -> str:
    return re.sub(r"[\s,]", "", str(s or ""))


def raw_answer(name: str) -> dict:
    """그 사진에 보낸 마지막 요청의 끝난 이유와 답 원문 (사진 읽기가 실패했을 때 원인 확인용)."""
    r = next((x for x in reversed(REC["requests"]) if x["flow"] == f"vision:{name}"), None)
    if not r:
        return {}
    return {"finish_reason": r.get("finish_reason"), "completion_tokens": r.get("completion_tokens"),
            "content_head": r.get("content_head", ""), "content_tail": r.get("content_tail", "")}


def vision_test(ocr) -> list[dict]:
    out = []
    for name, expect in CONTRACTS.items():
        REC["current"] = f"vision:{name}"
        t0 = time.time()
        try:
            got = ocr.read_contract((MATERIAL / name).read_bytes(), "image/png")["fields"]
            ok = {k: (norm(got.get(k)) == "" if v == "" else norm(v) in norm(got.get(k))) for k, v in expect.items()}
            out.append({"image": name, "kind": "contract", "sec": round(time.time() - t0, 2), "score": sum(ok.values()),
                        "total": len(ok), "wrong": [k for k, v in ok.items() if not v], "got": got})
        except Exception as exc:  # noqa: BLE001
            out.append({"image": name, "kind": "contract", "sec": round(time.time() - t0, 2), "score": 0,
                        "total": len(expect), "error": f"{type(exc).__name__}: {exc}"[:300], **raw_answer(name)})
    for name, expect in PAYSLIPS.items():
        REC["current"] = f"vision:{name}"
        t0 = time.time()
        try:
            got = ocr.read_payslip((MATERIAL / name).read_bytes(), "image/png")
            ok = {k: (got.get(k) or 0) == v if isinstance(v, int) else got.get(k) == v for k, v in expect.items()}
            out.append({"image": name, "kind": "payslip", "sec": round(time.time() - t0, 2), "score": sum(ok.values()),
                        "total": len(ok), "wrong": [k for k, v in ok.items() if not v], "got": got})
        except Exception as exc:  # noqa: BLE001
            out.append({"image": name, "kind": "payslip", "sec": round(time.time() - t0, 2), "score": 0,
                        "total": len(expect), "error": f"{type(exc).__name__}: {exc}"[:300], **raw_answer(name)})
    return out


# ---------- 3. 에이전트 흐름 ----------
def judge(items: list[dict]) -> dict:
    """코드 규칙이 분명한(정상/위반 의심) 항목에서 AI 판단 비교: 같음, 조심스럽게 확인 필요, 반대."""
    c = {"items": len(items), "ai_judged": 0, "pending": 0, "clear": 0, "match": 0, "conservative": 0, "wrong": 0}
    for it in items:
        if it.get("ai_reason"):
            c["ai_judged"] += 1
        if it.get("status") == "pending":
            c["pending"] += 1
        rule = it.get("rule_status")
        if it.get("ai_reason") and rule in ("ok", "bad"):
            c["clear"] += 1
            st = it.get("status")
            c["match" if st == rule else "conservative" if st == "warn" else "wrong"] += 1
    return c


def run_flow(s, core, uid: int, job, kind: str, month: str):
    if kind == "contract":
        r = core.run_contract_check(s, uid, job.id)
        return r, r.get("items", [])
    if kind == "payday":
        r = core.run_payday(s, uid, job.id, month)
        return r, [r["compare"]]
    if kind == "quit":
        st = core.run_quit_check(s, uid, job.id)
        return {"settlement": st}, [st] if st else []
    if kind == "report":
        return core.run_report(s, uid, job.id), []
    if kind == "guard":
        return core.run_guard_toggle(s, uid, job.id, True), []
    raise ValueError(kind)


def flows_test(on_flow=None) -> list[dict]:
    from sqlmodel import Session, func, select

    from app.agent import core
    from app.db import engine
    from app.models import AgentLog, Job, User
    out = []
    with Session(engine) as s:
        uid = s.exec(select(User)).first().id
        jobs = {j.name: j for j in s.exec(select(Job)).all()}
        for kind, name, month in FLOWS:
            label = f"{kind}:{name}" + (f":{month}" if month else "")
            REC["current"] = label
            before = s.exec(select(func.max(AgentLog.id))).one() or 0
            t0, err, items, res = time.time(), None, [], {}
            try:
                res, items = run_flow(s, core, uid, jobs[name], kind, month)
            except Exception:  # noqa: BLE001 (흐름이 실패해도 다음 흐름으로)
                s.rollback()
                err = traceback.format_exc()[-600:]
            sec = time.time() - t0
            logs = s.exec(select(AgentLog).where(AgentLog.id > before).order_by(AgentLog.id)).all()
            steps = [(lg.step, lg.detail) for lg in logs]
            reqs = [r for r in REC["requests"] if r["flow"] == label]
            job = s.get(Job, jobs[name].id)
            out.append({
                "flow": label, "kind": kind, "sec": round(sec, 2), "error": err,
                "finished": any(st == "AI 끝냄" for st, _ in steps),
                "turns": sum(bool(re.fullmatch(r"AI 판단 \d+", st)) for st, _ in steps),  # 'AI 판단 결과' 줄은 빼고
                "stopped_10": any(st == "멈춤" and "반복" in d for st, d in steps),
                "no_ai": any(st == "AI 응답 없음" for st, _ in steps),
                "ai_error": next((d for st, d in steps if st in ("멈춤", "AI 응답 없음", "오류")), ""),
                "reflects": sum(st.startswith("검증 장치") for st, _ in steps),
                "finish_rejected": sum(st == "끝내기 전 확인" for st, _ in steps),
                "tool_errors": sum(st.startswith("도구 ") and '"error"' in d for st, d in steps),
                "ask_user": sum(st == "도구 ask_user" for st, _ in steps),
                "ask_law": sum(st == "도구 ask_user" and bool(re.search(r"조문|제\d+조", d)) for st, d in steps),
                "requests": len(reqs), "req_sec_max": max((r["sec"] for r in reqs), default=0),
                "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in reqs),
                "completion_tokens": sum(r.get("completion_tokens") or 0 for r in reqs),
                "judge": judge(items),
                "samples": {"reasons": [it.get("ai_reason") for it in items if it.get("ai_reason")][:3],
                            "guard_message": job.guard_ai_message[:500] if kind == "guard" else "",
                            "result": json.dumps(res, ensure_ascii=False, default=str)[:300] if not items else ""},
            })
            if on_flow:
                on_flow(list(out))
    return out


# ---------- 요약 ----------
def summarize(speed: list, vision: list, flows: list) -> dict:
    reqs = REC["requests"]
    secs = [r["sec"] for r in reqs if not r.get("error")]
    texts = [t for r in reqs for t in r.get("texts", [])]
    j = {k: sum(f["judge"][k] for f in flows) for k in ("items", "ai_judged", "pending", "clear", "match", "conservative", "wrong")}
    tool_reqs = [r for r in reqs if r["tools_offered"] and not r.get("error")]
    ok_speed = [x for x in speed if not x["error"]]
    agent = [f for f in flows]
    return {
        "speed": {"ttft_p50": pct([x["ttft"] for x in ok_speed if x["ttft"]], .5),
                  "tok_per_s_p50": pct([x["tok_per_s"] for x in ok_speed if x["tok_per_s"]], .5),
                  "errors": sum(1 for x in speed if x["error"]),
                  "reasoning_chars": sum(x["reasoning_chars"] for x in speed)},
        "requests": {"count": len(reqs), "errors": sum(1 for r in reqs if r.get("error")),
                     "truncated": sum(1 for r in reqs if r.get("finish_reason") == "length"),
                     "sec_p50": pct(secs, .5), "sec_p95": pct(secs, .95), "sec_max": max(secs, default=None),
                     "over_100s": sum(1 for x in secs if x > 100),
                     "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in reqs),
                     "completion_tokens": sum(r.get("completion_tokens") or 0 for r in reqs)},
        "tool_calls": {"requests_with_tools": len(tool_reqs),
                       "calls": sum(r.get("n_tool_calls", 0) for r in tool_reqs),
                       "no_call_responses": sum(1 for r in tool_reqs if not r.get("n_tool_calls")),
                       "raw_tool_text": sum(1 for r in tool_reqs if r.get("raw_tool_text")),
                       "bad_args": sum(r.get("bad_args", 0) for r in tool_reqs),
                       "forced_ignored": sum(1 for r in tool_reqs if r["forced"] and not r.get("n_tool_calls"))},
        "agent": {"flows": len(agent), "finished": sum(f["finished"] for f in agent),
                  "stopped_10": sum(f["stopped_10"] for f in agent), "errors": sum(1 for f in agent if f["error"]),
                  "no_ai": sum(f["no_ai"] for f in agent),
                  "turns_avg": round(statistics.mean(f["turns"] for f in agent), 1) if agent else None,
                  "sec_avg": round(statistics.mean(f["sec"] for f in agent), 1) if agent else None,
                  "sec_max": max((f["sec"] for f in agent), default=None),
                  "reflects": sum(f["reflects"] for f in agent), "finish_rejected": sum(f["finish_rejected"] for f in agent),
                  "tool_errors": sum(f["tool_errors"] for f in agent), "ask_user": sum(f["ask_user"] for f in agent),
                  "ask_law": sum(f["ask_law"] for f in agent),
                  "tokens_per_flow": round(statistics.mean(f["prompt_tokens"] + f["completion_tokens"] for f in agent))
                  if agent else None},
        "judgment": j,
        "vision": {"contract_score": sum(v["score"] for v in vision if v["kind"] == "contract"),
                   "contract_total": sum(v["total"] for v in vision if v["kind"] == "contract"),
                   "payslip_score": sum(v["score"] for v in vision if v["kind"] == "payslip"),
                   "payslip_total": sum(v["total"] for v in vision if v["kind"] == "payslip"),
                   "errors": sum(1 for v in vision if v.get("error")),
                   "truncated": sum(1 for v in vision if v.get("finish_reason") == "length"),
                   "sec_avg": round(statistics.mean(v["sec"] for v in vision), 1) if vision else None},
        "korean": text_stats(texts),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="bench")
    ap.add_argument("--extra-body", default="{}")
    ap.add_argument("--sampling", default="{}", help="공식 권장 생성 설정 JSON (앱의 LLM_SAMPLING)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip", default="", help="건너뛸 시험 (speed,vision,flows 중 쉼표로)")
    args = ap.parse_args()
    skip = set(filter(None, args.skip.split(",")))
    data_dir = f"data/bench_{args.key}"
    env = {"DB_PATH": str(ROOT / data_dir / "app.db"), "UPLOAD_DIR": str(ROOT / data_dir / "uploads"),
           "REPORT_DIR": str(ROOT / data_dir / "reports"), "LLM_ENABLED": "true", "LLM_FAKE": "false",
           "LLM_BASE_URL": args.base_url, "LLM_MODEL": args.model, "LLM_VISION_MODEL": "", "LLM_TIMEOUT": "600",
           "LLM_EXTRA_BODY": args.extra_body, "LLM_SAMPLING": args.sampling, "LLM_MAX_TOKENS": os.environ.get("LLM_MAX_TOKENS", "4096"),
           "LAW_REFRESH_ON_START": "false", "TEST_DATA": "false",
           "NAVER_CLIENT_ID": "", "NAVER_CLIENT_SECRET": ""}
    os.environ.update(env)
    # 시험 데이터 (알바 5개, 사진, 평소 DB의 법 기준표 복사)를 이 모델 전용 폴더에 새로 만든다
    built = subprocess.run([sys.executable, "-m", "app.demo_db", "--dir", data_dir, "--force"], cwd=ROOT,
                           capture_output=True, text=True, env={**os.environ, **env})
    law_line = next((x for x in built.stdout.splitlines() if x.startswith("법 기준표")), built.stderr[-300:])
    sys.path.insert(0, str(ROOT))
    from app import ocr
    from app.llm import client
    install_recorder(client)
    extra = json.loads(args.extra_body or "{}")
    started = time.time()
    state = {"speed": [], "vision": [], "flows": []}

    def save(stage: str) -> None:
        """단계마다 지금까지의 결과를 쓴다 (중간에 멈춰도 남게)."""
        result = {"key": args.key, "base_url": args.base_url, "extra_body": extra, "sampling": client.sampling(),
                  "sampling_sent": args.sampling, "law_table": law_line,
                  "max_tokens": os.environ.get("LLM_MAX_TOKENS"), "stage": stage,
                  "suite_sec": round(time.time() - started, 1),
                  "summary": summarize(state["speed"], state["vision"], state["flows"]), **state,
                  "requests": [{k: v for k, v in r.items() if k != "texts"} for r in REC["requests"]]}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if "speed" not in skip:
        state["speed"] = speed_test(args.base_url, args.model, extra, client.sampling())
        save("속도 끝")
    if "vision" not in skip:
        state["vision"] = vision_test(ocr)
        save("사진 끝")
    if "flows" not in skip:
        flows_test(lambda done: (state.__setitem__("flows", done), save(f"흐름 {len(done)}/{len(FLOWS)}")))
    save("완료")
    print(f"시험 결과: {args.out} ({round(time.time() - started, 1)}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
