"""사업자등록번호 확인 테스트. 실제 사업장 번호가 아닌 계산으로 만든 가상 번호만 쓴다."""
import pytest

from app.calc.bizno import check_digit, is_valid, normalize


def test_check_digit_by_hand():
    # 1*1+2*3+3*7+4*1+5*3+6*7+7*1+8*3+9*5 = 165, 9*5//10 = 4, 합 169 -> 10-9 = 1
    assert check_digit("123456789") == 1


def test_valid_and_invalid():
    assert is_valid("123-45-67891")
    assert not is_valid("123-45-67890")
    assert not is_valid("12345")


def test_normalize():
    assert normalize("1234567891") == "123-45-67891"
    assert normalize(" 123 45 67891 ") == "123-45-67891"
    assert normalize("") == ""
    with pytest.raises(ValueError, match="10자리"):
        normalize("123-45-678")
    with pytest.raises(ValueError, match="맞지 않아요"):
        normalize("123-45-67890")
