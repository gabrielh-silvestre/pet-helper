import re
from dataclasses import dataclass
from pathlib import Path

from pet_helper.orders import is_valid_cpf

# plain regexes, independent of the Presidio-based mask they audit
_CPF = re.compile(r"(?<!\w)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\w)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# the hyphen is required: without it any 10-11 digit number would look like a phone
_PHONE = re.compile(
    r"(?<![\w-])(?:\+55\s?)?(?:\(\d{2}\)|\d{2})\s?9?\d{4}-\d{4}(?![\w-])"
)
_TEXT_SUFFIXES = {".jsonl", ".csv"}
_INSPECT_SUFFIX = ".eval"


@dataclass(frozen=True)
class PiiFinding:
    path: Path
    kind: str


def _kinds_in(text: str) -> set[str]:
    kinds = set()
    if any(is_valid_cpf(match) for match in _CPF.findall(text)):
        kinds.add("cpf")
    if _EMAIL.search(text):
        kinds.add("email")
    if _PHONE.search(text):
        kinds.add("phone")
    return kinds


def _inspect_log_text(path: Path) -> str:
    # lazy: inspect_ai is slow to import and only the eval rounds produce these logs
    from inspect_ai.log import read_eval_log

    samples = read_eval_log(str(path)).samples or []
    return "\n".join(sample.model_dump_json() for sample in samples)


def _text_of(path: Path) -> str | None:
    if path.suffix == _INSPECT_SUFFIX:
        return _inspect_log_text(path)
    if path.suffix in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8")
    return None


def find_raw_pii(directory: Path) -> list[PiiFinding]:
    findings = []
    for path in sorted(directory.rglob("*")):
        text = _text_of(path)
        if text is not None:
            findings.extend(PiiFinding(path, kind) for kind in sorted(_kinds_in(text)))
    return findings
