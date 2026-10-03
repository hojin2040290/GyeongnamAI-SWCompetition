"""GPU 서버에서 후보 모델을 차례로 받고, 켜고, 시험하고, 끈다. 마지막에 비교표(report.md)를 만든다.

GPU 서버의 vLLM 가상환경 파이썬으로 실행한다 (huggingface_hub가 들어 있다). 자세한 사용법: bench/README.md
  /home/work/alba/venv/bin/python bench/run.py --preflight       # 모델 이름, 크기, 파서만 확인 (받지 않음)
  /home/work/alba/venv/bin/python bench/run.py --yes             # 전부 실행 (8000번 vLLM을 끄고, 끝나면 다시 켬)
  /home/work/alba/venv/bin/python bench/run.py --models hcx_seed_32b_think,exaone45_33b --yes

8000번 vLLM(앱이 쓰는 것)은 시작할 때 확인을 받은 뒤 vllm.pid의 번호로만 끄고, 끝나거나 중간에 멈춰도
start_vllm.sh로 다시 켠다. 벤치마크용 vLLM은 8100번으로 켜고, 이 스크립트가 켠 프로세스만 끈다.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))  # checks.py
BASE = Path("/home/work/alba")
LOG = None


def say(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------- GPU, 포트 ----------
def gpu_used_mib() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return int(out.split()[0])
    except Exception:  # noqa: BLE001
        return -1


def gpu_total_mib() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return int(out.split()[0])
    except Exception:  # noqa: BLE001
        return 0


def ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def wait_gpu_free(limit_mib: int = 3000, timeout: int = 180) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        used = gpu_used_mib()
        if 0 <= used <= limit_mib:
            return True
        time.sleep(5)
    return False


class GpuPeak(threading.Thread):
    """모델이 켜져 있는 동안 GPU 메모리 최대 사용량을 잰다."""
    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, False

    def run(self):
        while not self.stop:
            self.peak = max(self.peak, gpu_used_mib())
            time.sleep(3)


# ---------- 앱이 쓰는 8000번 vLLM ----------
def app_vllm_pid() -> int | None:
    p = BASE / "vllm.pid"
    try:
        pid = int(p.read_text().strip())
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
        return pid if "vllm" in cmd else None  # 다른 프로세스 번호가 남아 있으면 건드리지 않는다
    except (OSError, ValueError):
        return None


def stop_app_vllm(yes: bool) -> bool:
    """앱이 쓰는 8000번 vLLM을 끈다 (확인 후, vllm.pid의 번호로만). 끈 경우 True."""
    pid = app_vllm_pid()
    if not pid and not ready(8000):
        return False
    if not pid:
        raise SystemExit("8000번 vLLM이 켜져 있는데 vllm.pid로 번호를 찾지 못했어요. 직접 끈 뒤 다시 실행해 주세요.")
    say(f"앱이 쓰는 8000번 vLLM(번호 {pid})을 벤치마크 동안 꺼요. 끝나면 start_vllm.sh로 다시 켜요.")
    if not yes and input("계속할까요? (y/N) ").strip().lower() != "y":
        raise SystemExit("멈췄어요.")
    os.kill(pid, signal.SIGTERM)
    for _ in range(60):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(2)
    if not wait_gpu_free():
        raise SystemExit("GPU 메모리가 비지 않았어요. nvidia-smi로 확인해 주세요.")
    say("8000번 vLLM을 껐어요.")
    return True


def start_app_vllm() -> None:
    script = BASE / "start_vllm.sh"
    if not script.exists():
        say("start_vllm.sh가 없어 8000번 vLLM을 다시 켜지 못했어요. 직접 켜 주세요.")
        return
    say("8000번 vLLM을 다시 켜요 (start_vllm.sh).")
    subprocess.run(["bash", str(script)], cwd=BASE)


# ---------- 모델 확인, 받기 ----------
DOC_FILES = ("README.md", "generation_config.json", "chat_template.jinja", "chat_template.json", "tokenizer_config.json")


def model_docs(api, hid: str, keep: Path | None) -> dict:
    """모델의 README, 생성 기본값, 대화 틀을 받아 keep 폴더에 남기고 필요한 값을 꺼낸다 (가중치는 받지 않음)."""
    from huggingface_hub import hf_hub_download
    texts = {}
    for name in DOC_FILES:
        try:
            texts[name] = Path(hf_hub_download(hid, name, token=api.token)).read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 (모델마다 있는 파일이 다르다)
            continue
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / name).write_text(texts[name], encoding="utf-8")
    readme = texts.get("README.md", "")
    tp = re.findall(r"--tool-call-parser[ =]+[\"']?([\w\-]+)", readme)
    rp = re.findall(r"--reasoning-parser[ =]+[\"']?([\w\-]+)", readme)
    try:
        gen = json.loads(texts.get("generation_config.json") or "{}")
    except json.JSONDecodeError:
        gen = {}
    template = texts.get("chat_template.jinja", "") + texts.get("chat_template.json", "")
    try:
        t = json.loads(texts.get("tokenizer_config.json") or "{}").get("chat_template") or ""
        template += t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
    except (json.JSONDecodeError, AttributeError):
        pass
    return {"tool_parser": tp[0] if tp else "", "reasoning_parser": rp[0] if rp else "",
            "trust_remote_code": "--trust-remote-code" in readme,
            # 비교표에 남겨 공식 권장 생성 설정을 다시 확인할 수 있게 (README의 temperature가 들어간 줄)
            "sampling_lines": [x.strip()[:200] for x in readme.splitlines() if re.search(r"temperature|top_p|presence_penalty", x)][:8],
            "generation_config": {k: gen[k] for k in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty",
                                                      "presence_penalty") if k in gen},
            "template_switches": sorted({n for n in ("enable_thinking", "thinking", "skip_reasoning", "force_reasoning",
                                                     "reasoning_effort") if re.search(rf"\b{n}\b", template)}),
            "template_text": template}


def weight_bytes(siblings: list) -> float:
    """가중치 파일 크기 합. 같은 가중치가 여러 형식으로 있으면 한 형식만 센다.
    - Mistral: Mistral 형식(consolidated*.safetensors)과 HF 형식(model-*.safetensors)이 함께 있다
    - safetensors와 예전 .bin/.pt가 함께 있으면 vLLM은 safetensors를 쓴다"""
    st = [s for s in siblings if s.rfilename.endswith(".safetensors")]
    if st:
        cons = [s for s in st if Path(s.rfilename).name.startswith("consolidated")]
        rest = [s for s in st if s not in cons]
        st = rest if cons and rest else st
    else:
        st = [s for s in siblings if s.rfilename.endswith((".bin", ".pt"))]
    return sum((s.size or 0) for s in st)


def installed(pkg: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


def vllm_tested_pins(pkgs=("transformers", "mistral-common", "torch")) -> dict:
    """설치된 vLLM 버전이 공식 시험에 쓴 패키지 버전 (GitHub의 requirements/test/cuda.txt)."""
    ver = installed("vllm")
    if not ver:
        return {}
    url = f"https://raw.githubusercontent.com/vllm-project/vllm/v{ver}/requirements/test/cuda.txt"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            text = r.read().decode(errors="ignore")
    except Exception:  # noqa: BLE001
        return {}
    pins = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", line.strip())
        if m and m.group(1).lower().replace("_", "-") in pkgs:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def pin_differences(pins: dict) -> list[str]:
    """vLLM이 시험한 버전과 다르게 설치된 패키지 (torch는 +cu130 같은 꼬리를 빼고 비교)."""
    out = []
    for pkg, want in pins.items():
        have = installed(pkg)
        if have is None:
            continue
        if have.split("+")[0] != want.split("+")[0]:
            out.append(f"{pkg} {have} (vLLM {installed('vllm')} 시험 버전 {want}, "
                       f"맞추기: uv pip install --python {BASE}/venv/bin/python \"{pkg}=={want.split('+')[0]}\")")
    return out


def version_problems(spec: dict) -> list[str]:
    """공식 문서의 최소 버전보다 낮거나 없는 패키지 (이 파이썬 = GPU 서버의 vLLM 가상환경)."""
    from packaging.version import Version
    out = []
    for pkg, need in (spec.get("requires") or {}).items():
        have = installed(pkg)
        if have is None or Version(have) < Version(need):
            out.append(f"{pkg} {have or '없음'} < 필요 {need} "
                       f"(설치: uv pip install --python {BASE}/venv/bin/python \"{pkg}>={need}\")")
    return out


def preflight(spec: dict, gpu_mib: int, keep: Path | None = None, ignore_versions: bool = False) -> dict:
    from huggingface_hub import HfApi
    api = HfApi(token=os.getenv("HF_TOKEN") or None)
    notes = []
    versions = {pkg: installed(pkg) for pkg in ["vllm", "transformers", *(spec.get("requires") or {})]}
    for hid in spec["hf_ids"]:
        try:
            info = api.model_info(hid, files_metadata=True)
        except Exception as exc:  # noqa: BLE001 (없음, 동의 필요 등)
            notes.append(f"{hid}: {type(exc).__name__}")
            continue
        size = weight_bytes(info.siblings or [])
        docs = model_docs(api, hid, keep)
        util = spec.get("gpu_memory_utilization", 0.85)
        out = {"hf_id": hid, "size_gb": round(size / 1e9, 1), "gated": bool(info.gated),
               "license": (info.card_data or {}).get("license") if info.card_data else None,
               "readme": {k: v for k, v in docs.items() if k != "template_text"}, "notes": notes, "util": util,
               "versions": versions, "version_problems": version_problems(spec)}
        out["_template"] = docs["template_text"]
        if gpu_mib and size / 2 ** 20 > gpu_mib * util * 0.95:
            out["skip"] = f"모델({out['size_gb']}GB)이 GPU 메모리({gpu_mib / 1024:.0f}GiB x {util})보다 커요"
        elif out["version_problems"] and not ignore_versions:
            out["skip"] = "공식 문서의 최소 버전보다 낮아요: " + "; ".join(out["version_problems"])
        return out
    return {"skip": "Hugging Face에서 모델을 찾지 못했거나 접근 권한이 없어요 (HF_TOKEN, 라이선스 동의 확인)", "notes": notes}


def tool_parser_list(spec: dict, readme: dict) -> list[str]:
    """도구 파서 후보 (공식 문서 값 먼저, auto는 README 값). 같은 이름은 한 번만."""
    out = []
    for v in spec.get("tool_parsers") or [spec.get("tool_parser", "")]:
        v = readme.get("tool_parser", "") if v == "auto" else v
        if v and v not in out:
            out.append(v)
    return out or [""]


def reasoning_parser(spec: dict, readme: dict) -> str:
    v = spec.get("reasoning_parser", "")
    return (readme.get("reasoning_parser") or spec.get("reasoning_parser_fallback", "")) if v == "auto" else v


def sampling_of(spec: dict) -> dict:
    """공식 권장 생성 설정. auto는 temperature를 보내지 않아 모델 기본값(generation_config.json)을 쓰게 한다."""
    v = spec.get("sampling", {})
    return {"temperature": None} if v == "auto" else dict(v or {})


def chat_template_file(spec: dict, outdir: Path) -> tuple[str, str]:
    """vllm:examples/파일 → 설치된 vLLM 버전의 GitHub 파일을 받는다 (없으면 main). (경로, 받은 주소)"""
    ref = spec.get("chat_template", "")
    if not ref.startswith("vllm:"):
        return ref, ""
    rel = ref[5:]
    dest = outdir / "templates" / Path(rel).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    ver = installed("vllm")
    for tag in ([f"v{ver}"] if ver else []) + ["main"]:
        url = f"https://raw.githubusercontent.com/vllm-project/vllm/{tag}/{rel}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                dest.write_bytes(r.read())
            return str(dest), url
        except Exception:  # noqa: BLE001
            continue
    return "", ""


def download(hid: str) -> float:
    from huggingface_hub import snapshot_download
    t0 = time.time()
    snapshot_download(hid, token=os.getenv("HF_TOKEN") or None)
    return round(time.time() - t0, 1)


# ---------- 켜기, 시험, 끄기 ----------
def serve_cmd(args, spec: dict, pre: dict, tool_parser: str = "", template: str = "") -> list[str]:
    if args.serve_cmd:  # 로컬 확인용 (가짜 서버)
        return args.serve_cmd.format(port=args.port, parser=tool_parser).split()
    readme = pre.get("readme", {})
    rp = reasoning_parser(spec, readme)
    cmd = [args.vllm, "serve", pre["hf_id"], "--served-model-name", "bench", "--host", "127.0.0.1",
           "--port", str(args.port), "--max-model-len", str(args.max_len),
           "--gpu-memory-utilization", str(pre.get("util", 0.85))]
    if tool_parser:
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", tool_parser]
    if template:
        cmd += ["--chat-template", template]
    if rp:
        cmd += ["--reasoning-parser", rp]
    extra = list(spec.get("args", []))
    if readme.get("trust_remote_code") and "--trust-remote-code" not in extra:
        extra.append("--trust-remote-code")
    return cmd + extra


def start_server(args, cmd: list[str], vlog: Path) -> subprocess.Popen:
    """vLLM을 켜고 /v1/models가 답할 때까지 기다린다. 켜지다 멈추면 로그 끝을 담아 예외."""
    t0 = time.time()
    with open(vlog, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT,
                                env={**os.environ, "HF_HOME": str(args.hf_home)})
    while not ready(args.port):
        if proc.poll() is not None:
            stop_server(args, proc)  # 남은 하위 프로세스(엔진)가 GPU를 잡고 있지 않게
            raise RuntimeError("vLLM이 켜지다 멈췄어요: " + vlog.read_text(errors="ignore")[-1500:])
        if time.time() - t0 > args.ready_timeout:
            stop_server(args, proc)
            raise RuntimeError(f"{args.ready_timeout}초 안에 켜지지 않았어요")
        time.sleep(5)
    return proc


def stop_server(args, proc: subprocess.Popen) -> None:
    """이 스크립트가 켠 vLLM만 끈다 (프로세스 묶음 번호로)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=120)
    except Exception:  # noqa: BLE001
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not args.serve_cmd:
        wait_gpu_free()


def run_suite(args, spec: dict, outdir: Path, extra: dict, sampling: dict) -> tuple[int, str, Path]:
    out_json = outdir / f"{spec['key']}.json"
    suite = subprocess.run([args.suite_python, str(ROOT / "bench" / "suite.py"), "--key", spec["key"],
                            "--base-url", f"http://127.0.0.1:{args.port}/v1", "--model", "bench",
                            "--extra-body", json.dumps(extra), "--sampling", json.dumps(sampling), "--out", str(out_json)]
                           + (["--skip", args.skip] if args.skip else []),
                           cwd=ROOT, capture_output=True, text=True, timeout=args.suite_timeout)
    return suite.returncode, (suite.stdout + suite.stderr)[-1500:], out_json


# 켜지다 멈춘 vLLM 로그에서 알아볼 수 있는 오류와 고치는 옵션 (오류 문장이 알려 주는 값을 그대로 쓴다)
STARTUP_FIXES = [
    # Mamba 계층이 있는 모델(Qwen3.5 등): 동시 처리 수 기본값(1024)이 Mamba 칸보다 많음
    (r"lower max_num_seqs to at most (\d+)", lambda m: ["--max-num-seqs", m.group(1)]),
    # KV 캐시가 최대 길이를 담지 못함: vLLM이 계산한 최대 길이로 줄인다
    (r"estimated maximum model length is (\d+)", lambda m: ["--max-model-len", m.group(1)]),
]
PARSER_ERROR = re.compile(r"tool[_ -]?call[_ -]?parser|ToolParser|tool_parser|reasoning[_ -]?parser|chat[_ -]?template", re.I)


def startup_fix(log_text: str) -> list[str] | None:
    for pattern, make in STARTUP_FIXES:
        m = re.search(pattern, log_text)
        if m:
            return make(m)
    return None


def with_flags(cmd: list[str], flags: list[str]) -> list[str]:
    """--옵션 값 쌍을 넣는다. 이미 있는 옵션이면 값만 바꾼다."""
    cmd = list(cmd)
    for i in range(0, len(flags), 2):
        name, value = flags[i], flags[i + 1]
        if name in cmd[:-1]:
            cmd[cmd.index(name) + 1] = value
        else:
            cmd += [name, value]
    return cmd


def check_line(c: dict) -> str:
    t = c["thinking"]
    think = "생각 꺼짐" if t.get("ok") and not t.get("fixed") else ("생각 끄기 옵션 바꿈" if t.get("fixed") else "생각 못 끔")
    return (f"{think}, 도구 auto {'성공' if c['tools']['auto']['ok'] else '실패'}, "
            f"도구 지정 {'성공' if c['tools']['forced']['ok'] else '실패'}, 사진 JSON {'성공' if c['vision']['ok'] else '실패'}")


def bench_one(args, spec: dict, outdir: Path, gpu_mib: int) -> dict:
    meta = {"key": spec["key"], "label": spec["label"], "status": "시작", "docs": spec.get("docs", []),
            "sampling_note": spec.get("sampling_note", "")}
    path = outdir / f"{spec['key']}.meta.json"

    def save():
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    keep = outdir / "docs" / spec["key"]
    pre = {"hf_id": "fake", "util": 0.85} if args.serve_cmd else preflight(spec, gpu_mib, keep, args.ignore_versions)
    template_text = pre.pop("_template", "")
    meta["preflight"] = pre
    sampling, extra = sampling_of(spec), dict(spec.get("extra_body", {}))
    meta["sampling"], meta["extra_body"] = sampling, extra
    parsers = (args.fake_parsers.split(",") if args.serve_cmd else tool_parser_list(spec, pre.get("readme", {})))
    meta["tool_parsers"] = parsers
    if pre.get("skip"):
        meta["status"] = f"건너뜀: {pre['skip']}"
        save()
        return meta
    template, template_url = ("", "") if args.serve_cmd else chat_template_file(spec, outdir)
    meta["chat_template"] = {"want": spec.get("chat_template", ""), "path": template, "url": template_url}
    if spec.get("chat_template") and not template:
        meta["chat_template"]["error"] = "대화 틀 파일을 받지 못해 모델 기본 대화 틀로 켜요 (도구 호출이 안 될 수 있음)"
    if args.preflight:
        meta["status"] = "확인만"
        meta["serve_cmd"] = serve_cmd(args, spec, pre, parsers[0], template)
        save()
        return meta
    meta["attempts"] = []
    peak = GpuPeak()
    try:
        if not args.serve_cmd:
            say(f"[{spec['key']}] 받는 중: {pre['hf_id']} ({pre['size_gb']}GB)")
            meta["download_s"] = download(pre["hf_id"])
        peak.start()
        fixes: list[str] = []  # 시작 오류를 보고 더한 옵션 (다음 파서에도 쓴다)
        n = 0
        i = -1
        while i + 1 < len(parsers):  # 점검에서 맞는 파서를 찾으면 목록 뒤에 더한다
            i += 1
            tp = parsers[i]
            last = i == len(parsers) - 1
            for _ in range(4):  # 같은 파서로 옵션을 고쳐 다시 켜기 (오류 종류마다 한 번)
                cmd = with_flags(serve_cmd(args, spec, pre, tp, template), fixes)
                vlog = outdir / (f"{spec['key']}.vllm.log" if n == 0 else f"{spec['key']}.{n}.vllm.log")
                n += 1
                attempt = {"tool_parser": tp, "serve_cmd": cmd, "log": vlog.name, "fixes": list(fixes)}
                meta["attempts"].append(attempt)
                meta["serve_cmd"] = cmd
                say(f"[{spec['key']}] 켜는 중 (도구 파서 {tp or '없음'}{', 더한 옵션 ' + ' '.join(fixes) if fixes else ''}, 로그 {vlog.name})")
                t0 = time.time()
                try:
                    proc = start_server(args, cmd, vlog)
                    break
                except RuntimeError as exc:
                    attempt["error"] = str(exc)[-1500:]
                    text = vlog.read_text(errors="ignore") if vlog.exists() else str(exc)
                    fix = startup_fix(text)
                    if fix and not all(x in fixes for x in fix):
                        fixes = with_flags(fixes, fix) if fixes else fix
                        attempt["fix_next"] = fix
                        say(f"[{spec['key']}] 켜지지 않음: 로그가 알려 준 대로 {' '.join(fix)}를 더해 다시 켜요")
                        continue
                    proc = None
                    # 오류 문장만 본다 (vLLM 사용법 안내 줄에도 --tool-call-parser가 들어 있어서)
                    err_lines = "\n".join([x for x in text.splitlines() if re.search(r"Error|error:", x)][-5:])
                    if last or not PARSER_ERROR.search(err_lines):
                        raise  # 파서와 상관없는 오류는 다른 파서로 켜도 같아서 멈춘다
                    say(f"[{spec['key']}] 도구 파서 오류로 켜지지 않아 다음 도구 파서로 다시 해요")
                    break
            else:
                raise RuntimeError("옵션을 고쳐도 켜지지 않았어요")
            if proc is None:
                continue
            try:
                meta["load_s"] = round(time.time() - t0, 1)
                meta["gpu_after_load_mib"] = gpu_used_mib()
                say(f"[{spec['key']}] 켜짐 ({meta['load_s']}초). 자동 점검")
                from checks import run_all
                c = run_all(args.port, extra, sampling, template_text, ROOT / "테스트자료" / "02_근로계약서.png")
                attempt["checks"] = c
                say(f"[{spec['key']}] 점검: {check_line(c)}")
                guess = c["tools"].get("parser_guess", "")
                if not c["tools"]["ok"] and guess and guess not in parsers:
                    parsers.append(guess)  # 답 속 호출 모양을 읽는 파서
                    meta["tool_parsers"] = parsers
                    last = False
                    say(f"[{spec['key']}] 답 속 도구 호출 모양이 {guess} 파서 형식이라 그 파서를 후보에 더해요")
                if not c["tools"]["ok"] and not last:
                    say(f"[{spec['key']}] 도구 호출을 읽지 못해 다음 도구 파서로 다시 켜요")
                    continue
                meta["checks"], meta["extra_body_used"] = c, c["extra_body"]
                meta["tool_parser_used"], meta["startup_fixes"] = tp, fixes
                say(f"[{spec['key']}] 시험 시작")
                t1 = time.time()
                code, tail, out_json = run_suite(args, spec, outdir, c["extra_body"], sampling)
                meta["suite_s"] = round(time.time() - t1, 1)
                meta["suite_tail"] = tail
                meta["status"] = "완료" if code == 0 and out_json.exists() else "시험 실패"
                break
            finally:
                say(f"[{spec['key']}] 끄는 중")
                stop_server(args, proc)
    except Exception as exc:  # noqa: BLE001 (한 모델이 실패해도 다음 모델로)
        meta["status"] = "실패"
        meta["error"] = f"{type(exc).__name__}: {exc}"[-2000:]
    except KeyboardInterrupt:
        meta["status"] = "멈춤"  # 위의 finally에서 벤치마크 vLLM은 이미 껐다. 기록을 남기고 main의 정리로
        say(f"[{spec['key']}] 멈춤")
        save()
        raise
    finally:
        peak.stop = True
        meta["gpu_peak_mib"] = peak.peak
    say(f"[{spec['key']}] {meta['status']}")
    save()
    return meta


def on_term(signum, frame) -> None:
    """kill로 멈춰도 정리(벤치마크 vLLM 끄기, 8000번 다시 켜기, 비교표)가 돌게 KeyboardInterrupt로 바꾼다."""
    raise KeyboardInterrupt(f"신호 {signum}로 멈춤")


def main() -> int:
    global LOG
    signal.signal(signal.SIGTERM, on_term)
    ap = argparse.ArgumentParser(description="알바지킴이 모델 벤치마크")
    ap.add_argument("--models", default="all", help="모델 key를 쉼표로 (기본 all)")
    ap.add_argument("--yes", action="store_true", help="8000번 vLLM을 끄는 확인을 묻지 않음 (nohup으로 돌릴 때)")
    ap.add_argument("--preflight", action="store_true", help="모델 이름, 크기, 파서만 확인 (받지 않음, GPU 사용 안 함)")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--max-len", type=int, default=32768)
    ap.add_argument("--vllm", default=str(BASE / "venv/bin/vllm"))
    ap.add_argument("--hf-home", default=str(BASE / "hf"))
    ap.add_argument("--suite-python", default=str(BASE / "bench-venv/bin/python"))
    ap.add_argument("--ready-timeout", type=int, default=3600)
    ap.add_argument("--suite-timeout", type=int, default=3 * 3600)
    ap.add_argument("--skip", default="", help="건너뛸 시험 (speed,vision,flows)")
    ap.add_argument("--serve-cmd", default="", help="(로컬 확인용) vLLM 대신 띄울 명령, {port} 치환")
    ap.add_argument("--fake-sampling", default="{}", help="(로컬 확인용) 가짜 서버에 보낼 생성 설정 JSON")
    ap.add_argument("--fake-parsers", default="", help="(로컬 확인용) 도구 파서 후보 (쉼표, --serve-cmd의 {parser})")
    ap.add_argument("--out", default="")
    ap.add_argument("--resume", default="", help="이 결과 폴더에 이어서 (완료된 모델은 건너뜀)")
    ap.add_argument("--ignore-versions", action="store_true", help="공식 문서의 최소 버전보다 낮아도 실행")
    ap.add_argument("--rerun", default="", help="--resume에서 완료됐어도 다시 할 모델 key (쉼표)")
    args = ap.parse_args()
    os.environ["HF_HOME"] = args.hf_home  # huggingface_hub를 불러오기 전에
    specs = json.loads((ROOT / "bench" / "models.json").read_text(encoding="utf-8"))["models"]
    if args.serve_cmd:
        specs = [{"key": "fake", "label": "가짜 서버 (로컬 확인용)", "hf_ids": [], "extra_body": {},
                  "sampling": json.loads(args.fake_sampling)}]
    elif args.models != "all":
        want = args.models.split(",")
        specs = [s for s in specs if s["key"] in want]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_preflight" if args.preflight else "")
    outdir = Path(args.resume or args.out) if (args.resume or args.out) else ROOT / "bench" / "results" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    LOG = outdir / "run.log"
    gpu_mib = gpu_total_mib()
    say(f"벤치마크 시작: 모델 {len(specs)}개, GPU {gpu_mib} MiB, 결과 {outdir}")
    if not args.serve_cmd:
        pins = vllm_tested_pins()
        diffs = pin_differences(pins)
        (outdir / "versions.json").write_text(json.dumps(
            {"installed": {k: installed(k) for k in ("vllm", "torch", "transformers", "mistral-common")},
             "vllm_tested": pins, "differences": diffs}, ensure_ascii=False, indent=1), encoding="utf-8")
        for d in diffs:  # 버전이 다르면 모델 코드가 맞지 않아 켜지지 않거나 사진 처리가 틀릴 수 있다
            say(f"주의: vLLM이 시험한 버전과 달라요: {d}")
    stopped = False
    try:
        if not args.preflight and not args.serve_cmd:
            stopped = stop_app_vllm(args.yes)
        for spec in specs:
            done = outdir / f"{spec['key']}.meta.json"
            if (args.resume and spec["key"] not in args.rerun.split(",") and done.exists()
                    and json.loads(done.read_text(encoding="utf-8")).get("status") == "완료"):
                say(f"[{spec['key']}] 이미 완료 → 건너뜀")
                continue
            bench_one(args, spec, outdir, gpu_mib)
    finally:
        if stopped:
            start_app_vllm()
        subprocess.run([sys.executable, str(ROOT / "bench" / "report.py"), str(outdir)])
        say(f"끝. 비교표: {outdir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
