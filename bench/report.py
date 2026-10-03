"""벤치마크 결과 폴더(bench/results/<시각>)의 모델별 결과를 모아 비교표(report.md)를 만든다.

사용: python bench/report.py bench/results/20261002_0300
"""
import json
import sys
from pathlib import Path


def fmt(v, unit: str = "") -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, float):
        v = round(v, 1)
    return f"{v}{unit}"


def frac(a, b) -> str:
    return f"{a}/{b}" if b else "-"


def load(outdir: Path) -> list[dict]:
    rows = []
    for meta_path in sorted(outdir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        res_path = outdir / f"{meta['key']}.json"
        meta["result"] = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else None
        rows.append(meta)
    return rows


def flag(r: dict, name: str) -> str:
    """실행 명령에서 옵션 값 (예: --tool-call-parser hermes → hermes)."""
    cmd = r.get("serve_cmd") or []
    return cmd[cmd.index(name) + 1] if name in cmd[:-1] else "-"


def table(head: list[str], body: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in body]


def build(outdir: Path) -> str:
    rows = load(outdir)
    lines = [f"# 알바지킴이 모델 벤치마크 ({outdir.name})", "",
             "같은 시험 데이터(알바 5개)와 같은 앱 코드로 모델마다 실행한 결과예요. 숫자는 모두 이 GPU 서버에서 잰 값이에요.", ""]
    lines += ["## 1. 준비", ""] + table(
        ["모델", "상태", "Hugging Face", "크기", "받기", "켜기", "GPU 최대", "도구 파서", "생각 파서"],
        [[r["label"], r["status"], (r.get("preflight") or {}).get("hf_id", "-"),
          fmt((r.get("preflight") or {}).get("size_gb"), "GB"),
          fmt(r.get("download_s") and r["download_s"] / 60, "분"), fmt(r.get("load_s") and r["load_s"] / 60, "분"),
          fmt(r.get("gpu_peak_mib") and r["gpu_peak_mib"] / 1024, "GiB"),
          flag(r, "--tool-call-parser"), flag(r, "--reasoning-parser")]
         for r in rows])
    ok = lambda v: "성공" if v else ("실패" if v is not None else "-")  # noqa: E731

    def think(c: dict) -> str:
        t = c.get("thinking") or {}
        if not t:
            return "-"
        return "꺼짐" if t.get("ok") and not t.get("fixed") else ("옵션 바꿔 꺼짐" if t.get("fixed") else "못 끔")
    checked = [r for r in rows if r.get("checks")]
    lines += ["", "## 2. 자동 점검 (켠 뒤 시험 전)", "",
              "공식 문서대로 켠 뒤 짧은 요청으로 확인한 결과예요. 생각 모드가 꺼지지 않으면 대화 틀에서 찾은 다른 끄기 옵션으로 "
              "바꾸고, 도구 호출을 못 읽으면 다음 후보 파서로 다시 켜요. 생성 설정은 공식 문서의 권장값이에요.", ""] + table(
        ["모델", "쓴 도구 파서", "시도한 파서", "생각 모드", "도구 auto", "도구 지정", "사진 JSON", "사진 끝난 이유", "생성 설정"],
        [[r["label"], r.get("tool_parser_used") or "-", ", ".join(a["tool_parser"] or "없음" for a in r.get("attempts", [])),
          think(r["checks"]), ok(r["checks"]["tools"]["auto"]["ok"]), ok(r["checks"]["tools"]["forced"]["ok"]),
          ok(r["checks"]["vision"].get("ok")), r["checks"]["vision"].get("finish") or "-",
          "`" + json.dumps(r.get("sampling") or {}, ensure_ascii=False) + "`"] for r in checked])
    done = [r for r in rows if r.get("result")]
    S = lambda r: r["result"]["summary"]  # noqa: E731
    lines += ["", "## 3. 속도", "",
              "첫 글자까지 시간(TTFT)과 초당 생성 토큰은 한국어 질문 3개를 2번씩 보낸 중간값이에요. "
              "요청 시간은 에이전트와 사진 읽기에서 보낸 모든 요청이에요. 100초를 넘으면 cloudflared가 끊어요.", ""] + table(
        ["모델", "TTFT", "초당 토큰", "요청 수", "요청 중간값", "요청 95%", "요청 최대", "100초 초과", "길이 제한 걸림",
         "생각 글자(속도 시험)"],
        [[r["label"], fmt(S(r)["speed"]["ttft_p50"], "초"), fmt(S(r)["speed"]["tok_per_s_p50"]),
          S(r)["requests"]["count"], fmt(S(r)["requests"]["sec_p50"], "초"), fmt(S(r)["requests"]["sec_p95"], "초"),
          fmt(S(r)["requests"]["sec_max"], "초"), S(r)["requests"]["over_100s"], S(r)["requests"].get("truncated", "-"),
          S(r)["speed"]["reasoning_chars"]]
         for r in done])
    lines += ["", "## 4. 에이전트 (실제 앱 흐름 13개)", "",
              "계약서 점검 5, 급여 점검 4, 퇴직 정산 2, 상담 자료 1, 신고 후 보호 1.", ""] + table(
        ["모델", "끝까지 마침", "반복 10회 초과", "AI 응답 없음", "오류", "평균 반복", "흐름 평균", "흐름 최대",
         "검증 장치 돌려보냄", "끝내기 전 확인 걸림", "도구 오류", "질문", "조문 질문", "흐름당 토큰"],
        [[r["label"], frac(S(r)["agent"]["finished"], S(r)["agent"]["flows"]), S(r)["agent"]["stopped_10"],
          S(r)["agent"]["no_ai"], S(r)["agent"]["errors"], fmt(S(r)["agent"]["turns_avg"]),
          fmt(S(r)["agent"]["sec_avg"], "초"), fmt(S(r)["agent"]["sec_max"], "초"), S(r)["agent"]["reflects"],
          S(r)["agent"]["finish_rejected"], S(r)["agent"]["tool_errors"], S(r)["agent"]["ask_user"],
          S(r)["agent"]["ask_law"], fmt(S(r)["agent"]["tokens_per_flow"])] for r in done])
    lines += ["", "## 5. 정확도", "",
              "판단: 코드 규칙이 분명히 정상이나 위반 의심으로 본 항목에서 AI 판단이 같음 / 확인 필요로 조심 / 반대. "
              "사진: 계약서 4장 x 7항목, 명세서 3장 x 5항목이 정답과 맞은 수. 사진 읽기 실패는 답을 JSON으로 읽지 못한 장 수, "
              "길이 제한은 그중 출력 토큰 한도(LLM_MAX_TOKENS)까지 쓰고 끊긴 장 수예요. 한글 비율은 AI가 쓴 글의 글자 중 한글 비율, "
              "한자는 중국어가 섞였을 수 있는 글자 수예요.", ""] + table(
        ["모델", "AI가 판단한 항목", "판단 대기로 남음", "같음", "조심", "반대", "계약서 사진", "명세서 사진",
         "사진 읽기 실패", "사진 길이 제한", "사진 평균 시간", "한글 비율", "한자"],
        [[r["label"], frac(S(r)["judgment"]["ai_judged"], S(r)["judgment"]["items"]), S(r)["judgment"]["pending"],
          frac(S(r)["judgment"]["match"], S(r)["judgment"]["clear"]), S(r)["judgment"]["conservative"],
          S(r)["judgment"]["wrong"], frac(S(r)["vision"]["contract_score"], S(r)["vision"]["contract_total"]),
          frac(S(r)["vision"]["payslip_score"], S(r)["vision"]["payslip_total"]),
          frac(S(r)["vision"].get("errors", 0), len(r["result"]["vision"])), S(r)["vision"].get("truncated", "-"),
          fmt(S(r)["vision"]["sec_avg"], "초"),
          fmt(S(r)["korean"]["hangul_ratio"]), S(r)["korean"]["han_chars"]] for r in done])
    lines += ["", "## 6. 도구 호출 형식", "",
              "도구를 준 요청 중 도구 호출 없이 글만 온 수, 글 속에 호출 모양이 들어 있어 파서가 읽지 못한 수, "
              "입력이 JSON이 아닌 수, 지정한 도구(마지막 반복의 finish 등)를 따르지 않은 수.", ""] + table(
        ["모델", "도구 요청", "도구 호출 수", "호출 없음", "글 속 호출", "입력 형식 오류", "지정 무시", "요청 오류"],
        [[r["label"], S(r)["tool_calls"]["requests_with_tools"], S(r)["tool_calls"]["calls"],
          S(r)["tool_calls"]["no_call_responses"], S(r)["tool_calls"]["raw_tool_text"], S(r)["tool_calls"]["bad_args"],
          S(r)["tool_calls"]["forced_ignored"], S(r)["requests"]["errors"]] for r in done])
    lines += ["", "## 7. 모델별 자세히", ""]
    for r in rows:
        lines += [f"### {r['label']}", "", f"- 상태: {r['status']}"]
        if r.get("error"):
            lines.append(f"- 오류: `{r['error'][-400:]}`")
        if (r.get("preflight") or {}).get("notes"):
            lines.append(f"- 이름 확인: {'; '.join(r['preflight']['notes'])}")
        if r.get("serve_cmd"):
            lines.append(f"- 실행 명령: `{' '.join(r['serve_cmd'])}`")
        for d in r.get("docs") or []:
            lines.append(f"- 공식 문서: {d}")
        pre = r.get("preflight") or {}
        if pre.get("versions"):
            lines.append("- 설치된 버전: " + ", ".join(f"{k} {v or '없음'}" for k, v in pre["versions"].items()))
        for v in pre.get("version_problems") or []:
            lines.append(f"- 버전 문제: {v}")
        if r.get("sampling_note"):
            lines.append(f"- 생성 설정 근거: {r['sampling_note']}")
        rd = pre.get("readme") or {}
        if rd.get("generation_config"):
            lines.append(f"- 모델 기본 생성 설정(generation_config.json): `{json.dumps(rd['generation_config'], ensure_ascii=False)}`")
        if rd.get("template_switches") is not None and pre.get("readme"):
            lines.append(f"- 대화 틀의 생각 끄기 이름: {', '.join(rd['template_switches']) or '없음'}")
        for x in rd.get("sampling_lines") or []:
            lines.append(f"- README 생성 설정 줄: `{x.replace('`', '')}`")
        ct = r.get("chat_template") or {}
        if ct.get("want"):
            lines.append(f"- 대화 틀: {ct.get('url') or ct.get('error')}")
        if r.get("extra_body_used") is not None:
            lines.append(f"- 실제로 쓴 요청 옵션: `{json.dumps(r['extra_body_used'], ensure_ascii=False)}`")
        for a in r.get("attempts") or []:
            c = a.get("checks")
            if a.get("error"):
                lines.append(f"- 파서 {a['tool_parser'] or '없음'}: 켜지지 않음 `{a['error'][-300:].replace(chr(10), ' ')}`")
            elif c:
                t = c["thinking"]
                lines.append(f"- 파서 {a['tool_parser'] or '없음'}: 생각 {think(c)} (생각 글자 {t['first']['reasoning_chars']}), "
                             f"도구 auto {ok(c['tools']['auto']['ok'])} (호출 {c['tools']['auto']['calls']}), "
                             f"도구 지정 {ok(c['tools']['forced']['ok'])}, 사진 JSON {ok(c['vision'].get('ok'))}")
                for name, x in (("도구 auto", c["tools"]["auto"]), ("사진", c["vision"])):
                    if not x.get("ok") and (x.get("content_head") or x.get("error")):
                        lines.append(f"  - {name} 답 원문: `{(x.get('content_head') or x.get('error') or '')[:200].replace(chr(10), ' ').replace('`', '')}`")
        res = r.get("result")
        if res:
            lines.append(f"- 법 기준표: {res.get('law_table')}")
            lines.append(f"- 요청 옵션(LLM_EXTRA_BODY): `{json.dumps(res.get('extra_body'), ensure_ascii=False)}`")
            lines.append(f"- 토큰 합계: 입력 {res['summary']['requests']['prompt_tokens']}, 출력 {res['summary']['requests']['completion_tokens']}")
            lines += ["", "| 흐름 | 시간 | 반복 | 요청 | 마침 | 판단(같음/조심/반대) | 문제 |", "|---|---|---|---|---|---|---|"]
            for f in res["flows"]:
                j = f["judge"]
                err_last = (f["error"] or "").strip().splitlines()[-1:] or [""]
                prob = (f["ai_error"] or err_last[0])[:80]
                lines.append(f"| {f['flow']} | {f['sec']}초 | {f['turns']} | {f['requests']} | {'예' if f['finished'] else '아니요'} | "
                             f"{j['match']}/{j['conservative']}/{j['wrong']} | {prob.replace('|', '/')} |")
            long = next((x for x in res["speed"] if x["label"] == "긴 답" and not x["error"]), None)
            if long:
                lines += ["", "긴 답 예시 (앞부분):", "", "> " + long["sample"].replace("\n", " ")[:400]]
            guard = next((f["samples"]["guard_message"] for f in res["flows"] if f["samples"]["guard_message"]), "")
            if guard:
                lines += ["", "신고 후 보호 안내 문구:", "", "> " + guard.replace("\n", " ")[:400]]
            reasons = [x for f in res["flows"] for x in f["samples"]["reasons"]][:3]
            if reasons:
                lines += ["", "판단 이유 예시:", ""] + [f"- {x[:200]}" for x in reasons]
            bad_v = [f"{v['image']}: {', '.join(v.get('wrong') or [])}{' ' + v['error'] if v.get('error') else ''}"
                     for v in res["vision"] if v.get("wrong") or v.get("error")]
            if bad_v:
                lines += ["", "사진 읽기에서 틀린 항목:", ""] + [f"- {x}" for x in bad_v]
            raw_v = [v for v in res["vision"] if v.get("error") and (v.get("content_head") or v.get("finish_reason"))][:3]
            if raw_v:
                lines += ["", "사진 읽기에 실패한 답 원문 (앞부분 / 끝부분):", ""]
                for v in raw_v:
                    one = lambda t: t.replace("\n", " ").replace("`", "'")  # noqa: E731
                    lines.append(f"- {v['image']} (끝난 이유 {v.get('finish_reason')}, 출력 토큰 {v.get('completion_tokens')}): "
                                 f"`{one(v.get('content_head', ''))[:300]}`"
                                 + (f" … `{one(v['content_tail'])[-200:]}`" if v.get("content_tail") else ""))
        elif r.get("suite_tail"):
            lines += ["", "```", r["suite_tail"][-800:], "```"]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    outdir = Path(sys.argv[1])
    (outdir / "report.md").write_text(build(outdir), encoding="utf-8")
    print(f"비교표: {outdir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
