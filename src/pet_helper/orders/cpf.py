import re

CPF_PATTERN = r"(?<![0-9])[0-9]{3}\.?[0-9]{3}\.?[0-9]{3}-?[0-9]{2}(?![0-9])"
CPF_REGEX = re.compile(CPF_PATTERN)


def normalize_cpf(text: str) -> str:
    return re.sub(r"\D", "", text) if CPF_REGEX.fullmatch(text) else ""


def _check_digit(digits: str) -> int:
    weights = range(len(digits) + 1, 1, -1)
    total = sum(int(d) * w for d, w in zip(digits, weights, strict=True))
    return total * 10 % 11 % 10


def is_valid_cpf(text: str) -> bool:
    digits = normalize_cpf(text)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    return digits[9:] == f"{_check_digit(digits[:9])}{_check_digit(digits[:10])}"
