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
