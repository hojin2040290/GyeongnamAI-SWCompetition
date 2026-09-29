""".env 읽기 테스트."""
from app.config import _parse_line


def test_parse_env_lines():
    assert _parse_line("LAW_OC=abc") == ("LAW_OC", "abc")
    assert _parse_line("  LAW_OC = abc  ") == ("LAW_OC", "abc")
    assert _parse_line("export LAW_OC=abc") == ("LAW_OC", "abc")
    assert _parse_line('LAW_OC="abc"') == ("LAW_OC", "abc")
    assert _parse_line("LAW_OC=abc  # 법제처 키") == ("LAW_OC", "abc")
    assert _parse_line('LAW_OC="abc"  # 법제처') == ("LAW_OC", "abc")
    assert _parse_line("# LAW_OC=abc") is None
    assert _parse_line("LAW_OC") is None
