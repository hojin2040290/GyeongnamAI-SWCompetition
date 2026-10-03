"""GPU 서버에서 후보 모델을 차례로 받고, 켜고, 시험하고, 끈다. 마지막에 비교표(report.md)를 만든다.

GPU 서버의 vLLM 가상환경 파이썬으로 실행한다 (huggingface_hub가 들어 있다). 자세한 사용법: bench/README.md
  /home/work/llm_alba/venv/bin/python bench/run.py --preflight       # 모델 이름, 크기, 파서만 확인 (받지 않음)
  /home/work/llm_alba/venv/bin/python bench/run.py --yes             # 전부 실행 (8000번 vLLM을 끄고, 끝나면 다시 켬)
  /home/work/llm_alba/venv/bin/python bench/run.py --models hcx_seed_32b_think,exaone45_33b --yes

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
BASE = Path("/home/work/llm_alba")
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
def parsers_from_readme(api, hid: str) -> dict:
    """모델 README의 vllm serve 예시에서 도구 호출 파서와 생각 파서를 찾는다."""
    from huggingface_hub import hf_hub_download
    try:
        text = Path(hf_hub_download(hid, "README.md", token=api.token)).read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return {}
    tp = re.findall(r"--tool-call-parser[ =]+[\"']?([\w\-]+)", text)
    rp = re.findall(r"--reasoning-parser[ =]+[\"']?([\w\-]+)", text)
    return {"tool_parser": tp[0] if tp else "", "reasoning_parser": rp[0] if rp else "",
            "trust_remote_code": "--trust-remote-code" in text}


def preflight(spec: dict, gpu_mib: int) -> dict:
    from huggingface_hub import HfApi
    api = HfApi(token=os.getenv("HF_TOKEN") or None)
    notes = []
    for hid in spec["hf_ids"]:
        try:
            info = api.model_info(hid, files_metadata=True)
        except Exception as exc:  # noqa: BLE001 (없음, 동의 필요 등)
            notes.append(f"{hid}: {type(exc).__name__}")
            continue
        size = sum((s.size or 0) for s in info.siblings or [] if s.rfilename.endswith((".safetensors", ".bin", ".pt")))
        readme = parsers_from_readme(api, hid)
        util = spec.get("gpu_memory_utilization", 0.85)
        out = {"hf_id": hid, "size_gb": round(size / 1e9, 1), "gated": bool(info.gated),
               "license": (info.card_data or {}).get("license") if info.card_data else None, "readme": readme,
               "notes": notes, "util": util}
        if gpu_mib and size / 2 ** 20 > gpu_mib * util * 0.95:
            out["skip"] = f"모델({out['size_gb']}GB)이 GPU 메모리({gpu_mib / 1024:.0f}GiB x {util})보다 커요"
        return out
    return {"skip": "Hugging Face에서 모델을 찾지 못했거나 접근 권한이 없어요 (HF_TOKEN, 라이선스 동의 확인)", "notes": notes}


def pick(spec: dict, readme: dict, name: str) -> str:
    v = spec.get(name, "")
    if v == "auto":
        v = readme.get(name) or spec.get(f"{name}_fallback", "")
    return v


def download(hid: str) -> float:
    from huggingface_hub import snapshot_download
    t0 = time.time()
    snapshot_download(hid, token=os.getenv("HF_TOKEN") or None)
    return round(time.time() - t0, 1)


# ---------- 켜기, 시험, 끄기 ----------
def serve_cmd(args, spec: dict, pre: dict) -> list[str]:
    if args.serve_cmd:  # 로컬 확인용 (가짜 서버)
        return args.serve_cmd.format(port=args.port).split()
    tp, rp = pick(spec, pre.get("readme", {}), "tool_parser"), pick(spec, pre.get("readme", {}), "reasoning_parser")
    cmd = [args.vllm, "serve", pre["hf_id"], "--served-model-name", "bench", "--host", "127.0.0.1",
           "--port", str(args.port), "--max-model-len", str(args.max_len),
           "--gpu-memory-utilization", str(pre.get("util", 0.85))]
    if tp:
        cmd += ["--enable-auto-tool-choice", "--tool-call-parser", tp]
    if rp:
        cmd += ["--reasoning-parser", rp]
    extra = list(spec.get("args", []))
    if pre.get("readme", {}).get("trust_remote_code") and "--trust-remote-code" not in extra:
        extra.append("--trust-remote-code")
    return cmd + extra


def bench_one(args, spec: dict, outdir: Path, gpu_mib: int) -> dict:
    meta = {"key": spec["key"], "label": spec["label"], "status": "시작"}
    path = outdir / f"{spec['key']}.meta.json"

    def save():
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    pre = {"hf_id": "fake", "util": 0.85} if args.serve_cmd else preflight(spec, gpu_mib)
    meta["preflight"] = pre
    if pre.get("skip"):
        meta["status"] = f"건너뜀: {pre['skip']}"
        save()
        return meta
    if args.preflight:
        meta["status"] = "확인만"
        meta["serve_cmd"] = serve_cmd(args, spec, pre)
        save()
        return meta
    try:
        if not args.serve_cmd:
            say(f"[{spec['key']}] 받는 중: {pre['hf_id']} ({pre['size_gb']}GB)")
            meta["download_s"] = download(pre["hf_id"])
        cmd = serve_cmd(args, spec, pre)
        meta["serve_cmd"] = cmd
        vlog = outdir / f"{spec['key']}.vllm.log"
        say(f"[{spec['key']}] 켜는 중 (로그 {vlog.name})")
        peak = GpuPeak()
        peak.start()
        t0 = time.time()
        with open(vlog, "w") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT,
                                    env={**os.environ, "HF_HOME": str(args.hf_home)})
        try:
            while not ready(args.port):
                if proc.poll() is not None:
                    raise RuntimeError("vLLM이 켜지다 멈췄어요: " + vlog.read_text(errors="ignore")[-1500:])
                if time.time() - t0 > args.ready_timeout:
                    raise RuntimeError(f"{args.ready_timeout}초 안에 켜지지 않았어요")
                time.sleep(5)
            meta["load_s"] = round(time.time() - t0, 1)
            meta["gpu_after_load_mib"] = gpu_used_mib()
            say(f"[{spec['key']}] 켜짐 ({meta['load_s']}초). 시험 시작")
            out_json = outdir / f"{spec['key']}.json"
            t1 = time.time()
            suite = subprocess.run([args.suite_python, str(ROOT / "bench" / "suite.py"), "--key", spec["key"],
                                    "--base-url", f"http://127.0.0.1:{args.port}/v1", "--model", "bench",
                                    "--extra-body", json.dumps(spec.get("extra_body", {})), "--out", str(out_json)]
                                   + (["--skip", args.skip] if args.skip else []),
                                   cwd=ROOT, capture_output=True, text=True, timeout=args.suite_timeout)
            meta["suite_s"] = round(time.time() - t1, 1)
            meta["suite_tail"] = (suite.stdout + suite.stderr)[-1500:]
            meta["status"] = "완료" if suite.returncode == 0 and out_json.exists() else "시험 실패"
        finally:
            peak.stop = True
            meta["gpu_peak_mib"] = peak.peak
            say(f"[{spec['key']}] 끄는 중")
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
    except Exception as exc:  # noqa: BLE001 (한 모델이 실패해도 다음 모델로)
        meta["status"] = "실패"
        meta["error"] = f"{type(exc).__name__}: {exc}"[-2000:]
    say(f"[{spec['key']}] {meta['status']}")
    save()
    return meta


def main() -> int:
    global LOG
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
    ap.add_argument("--out", default="")
    ap.add_argument("--resume", default="", help="이 결과 폴더에 이어서 (완료된 모델은 건너뜀)")
    args = ap.parse_args()
    os.environ["HF_HOME"] = args.hf_home  # huggingface_hub를 불러오기 전에
    specs = json.loads((ROOT / "bench" / "models.json").read_text(encoding="utf-8"))["models"]
    if args.serve_cmd:
        specs = [{"key": "fake", "label": "가짜 서버 (로컬 확인용)", "hf_ids": [], "extra_body": {}}]
    elif args.models != "all":
        want = args.models.split(",")
        specs = [s for s in specs if s["key"] in want]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_preflight" if args.preflight else "")
    outdir = Path(args.resume or args.out) if (args.resume or args.out) else ROOT / "bench" / "results" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    LOG = outdir / "run.log"
    gpu_mib = gpu_total_mib()
    say(f"벤치마크 시작: 모델 {len(specs)}개, GPU {gpu_mib} MiB, 결과 {outdir}")
    stopped = False
    try:
        if not args.preflight and not args.serve_cmd:
            stopped = stop_app_vllm(args.yes)
        for spec in specs:
            done = outdir / f"{spec['key']}.meta.json"
            if args.resume and done.exists() and json.loads(done.read_text(encoding="utf-8")).get("status") == "완료":
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
