"""벤치마크 스크립트(bench/run.py)의 계산 함수."""
import sys
from pathlib import Path
from types import SimpleNamespace as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import run  # noqa: E402


def test_weight_bytes_counts_one_format():
    """Mistral처럼 같은 가중치가 두 형식(consolidated, model-*)으로 있으면 한 형식만 센다 (예전엔 두 배로 세어 건너뜀)."""
    mistral = [F(rfilename="consolidated-00001-of-00002.safetensors", size=60e9),
               F(rfilename="consolidated-00002-of-00002.safetensors", size=61e9),
               F(rfilename="model-00001-of-00003.safetensors", size=40e9),
               F(rfilename="model-00002-of-00003.safetensors", size=40e9),
               F(rfilename="model-00003-of-00003.safetensors", size=41e9),
               F(rfilename="tekken.json", size=1e7)]
    assert run.weight_bytes(mistral) == 121e9
    # safetensors와 예전 .bin이 함께 있으면 safetensors만 (vLLM이 safetensors를 먼저 쓴다)
    both = [F(rfilename="model.safetensors", size=10e9), F(rfilename="pytorch_model.bin", size=10e9)]
    assert run.weight_bytes(both) == 10e9
    assert run.weight_bytes([F(rfilename="pytorch_model.bin", size=7e9)]) == 7e9
    assert run.weight_bytes([F(rfilename="a.safetensors", size=None)]) == 0


def test_startup_fix_from_vllm_log():
    """켜지다 멈춘 vLLM 로그의 오류 문장에서 고칠 옵션을 찾는다 (GPU 서버의 Qwen3.5-122B 로그 문장 그대로)."""
    log = ("ValueError: max_num_seqs (1024) exceeds available Mamba cache blocks (868). Each decode sequence requires one "
           "Mamba cache block, so CUDA graph capture cannot proceed. Please lower max_num_seqs to at most 868 or increase "
           "gpu_memory_utilization.")
    assert run.startup_fix(log) == ["--max-num-seqs", "868"]
    kv = "Based on the available memory, the estimated maximum model length is 20480. Try increasing gpu_memory_utilization"
    assert run.startup_fix(kv) == ["--max-model-len", "20480"]
    assert run.startup_fix("Model architectures ['PixtralForConditionalGeneration'] failed to be inspected") is None


def test_with_flags_replaces_existing_value():
    cmd = ["vllm", "serve", "m", "--max-model-len", "32768"]
    assert run.with_flags(cmd, ["--max-model-len", "20480", "--max-num-seqs", "868"]) == \
        ["vllm", "serve", "m", "--max-model-len", "20480", "--max-num-seqs", "868"]


def test_parser_from_tool_text():
    """파서가 읽지 못한 답 속 도구 호출 모양으로 맞는 파서를 고른다 (GPU 서버에서 본 답 그대로)."""
    import checks
    hcx = "<tool_call>save_note <arg_key>text</arg_key> <arg_value>근무 기록 점검</arg_value> </tool_call>"
    assert checks.parser_from_text(hcx) == "glm45"
    assert checks.parser_from_text('<tool_call>{"name": "save_note", "arguments": {"text": "a"}}</tool_call>') == "hermes"
    assert checks.parser_from_text("<tool_call>\n<function=save_note>\n<parameter=text>a</parameter>") == "qwen3_coder"
    assert checks.parser_from_text('[TOOL_CALLS]save_note[ARGS]{"text": "a"}') == "mistral"
    # EXAONE 4.5가 쓴 모양: vLLM 0.30.0에 이 모양을 읽는 파서가 없다
    assert checks.parser_from_text('<tool> {"name": "save_note", "arguments": {"text": "a"}} </tool>') == ""


def test_pin_differences(monkeypatch):
    """vLLM이 시험한 버전과 다르게 설치된 패키지를 찾는다 (GPU 서버: transformers 5.18.0, vLLM 0.30.0 시험 버전 5.16.1)."""
    have = {"vllm": "0.30.0", "transformers": "5.18.0", "torch": "2.13.0+cu130", "mistral-common": "1.11.6"}
    monkeypatch.setattr(run, "installed", lambda pkg: have.get(pkg))
    diffs = run.pin_differences({"transformers": "5.16.1", "torch": "2.13.0+cu130", "mistral-common": "1.11.6"})
    assert len(diffs) == 1 and diffs[0].startswith("transformers 5.18.0") and 'transformers==5.16.1' in diffs[0]


def test_requires_below_skips_incompatible(monkeypatch):
    """vLLM 0.30.0 + transformers 5.17.0 이상에서는 Mistral(Pixtral)이 켜지지 않아 건너뛴다 (GPU 서버에서 확인)."""
    import json
    spec = next(m for m in json.loads((Path(run.ROOT) / "bench" / "models.json").read_text(encoding="utf-8"))["models"]
                if m["key"] == "mistral_small4_fp8")
    monkeypatch.setattr(run, "installed", lambda pkg: {"vllm": "0.30.0", "transformers": "5.18.0", "mistral_common": "1.12.0"}.get(pkg))
    probs = run.version_problems(spec)
    assert len(probs) == 1 and probs[0].startswith("transformers 5.18.0 >= 5.17.0")
    monkeypatch.setattr(run, "installed", lambda pkg: {"vllm": "0.30.0", "transformers": "5.16.1", "mistral_common": "1.11.6"}.get(pkg))
    assert run.version_problems(spec) == []
