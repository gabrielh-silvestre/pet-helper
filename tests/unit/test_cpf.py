import pytest
from pet_helper.orders.cpf import is_valid_cpf, normalize_cpf


@pytest.mark.parametrize("cpf", ["529.982.247-25", "111.444.777-35"])
def test_it_should_accept_a_valid_cpf_with_punctuation(cpf):
    assert is_valid_cpf(cpf) is True


@pytest.mark.parametrize("cpf", ["52998224725", "11144477735"])
def test_it_should_accept_a_valid_cpf_without_punctuation(cpf):
    assert is_valid_cpf(cpf) is True


@pytest.mark.parametrize(
    "cpf",
    [
        "529.982.247-26",  # só o segundo dígito errado
        "529.982.247-35",  # só o primeiro dígito errado
    ],
)
def test_it_should_reject_a_cpf_with_a_wrong_check_digit(cpf):
    assert is_valid_cpf(cpf) is False


@pytest.mark.parametrize("cpf", ["111.111.111-11", "00000000000", "99999999999"])
def test_it_should_reject_a_cpf_with_all_digits_equal(cpf):
    assert is_valid_cpf(cpf) is False


@pytest.mark.parametrize("cpf", ["", "5299822472", "529982247250", "529.982.247"])
def test_it_should_reject_a_cpf_with_the_wrong_length(cpf):
    assert is_valid_cpf(cpf) is False


@pytest.mark.parametrize("cpf", ["abc.def.ghi-jk", "5299822472a", "cpf inválido"])
def test_it_should_reject_a_non_numeric_cpf(cpf):
    assert is_valid_cpf(cpf) is False


def test_it_should_strip_punctuation_when_normalizing_a_cpf():
    assert normalize_cpf("529.982.247-25") == "52998224725"


def test_it_should_keep_a_digits_only_cpf_unchanged_when_normalizing():
    assert normalize_cpf("52998224725") == "52998224725"
