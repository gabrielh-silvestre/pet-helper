import hashlib
import hmac
import logging
import os
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from typing import Any

from pet_helper.catalog import Catalog
from pet_helper.catalog.models import SKU_PATTERN
from pet_helper.orders.cpf import CPF_PATTERN, CPF_REGEX, is_valid_cpf, normalize_cpf
from pet_helper.text import fold_text

TRACE_SALT_ENV = "PET_HELPER_TRACE_SALT"
_CPF_PLACEHOLDER = "[CPF informado]"
_PROCESS_SALT = secrets.token_hex(16)

_SKU_IN_TEXT_REGEX = re.compile(rf"\b{SKU_PATTERN}\b")
_LIST_BULLET_PREFIX = re.compile(r"^[-*]\s+")
_CITATION_LINE_PATTERN = re.compile(
    r"^[*_]*fonte[*_]*\s*:[*_]*\s*(.+)$",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ".,;:!?"
_TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)$")

_INJECTION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:ignore|desconsidere|esqueca)\s+(?:todas\s+)?(?:as\s+|suas\s+|os\s+|seus\s+)?(?:instrucoes|regras|diretrizes|comandos|limites)\b",
        r"\bagora\s+voce\s+e\b",
        r"\bfinja\s+(?:ser|que\s+e)\b",
        r"\baja\s+como\b",
        r"\bsystem\s*prompt\b",
        r"\bprompt\s+de\s+sistema\b",
        r"\bmodo\s+(?:desenvolvedor|admin|administrador|sem\s+regras)\b",
    )
)


@dataclass(frozen=True)
class InputVerdict:
    model_text: str
    cpf: str | None
    injection_suspected: bool


@dataclass(frozen=True)
class OutputVerdict:
    invalid_skus: list[str]
    unretrieved_sources: list[str]


def _extract_first_valid_cpf(text: str) -> str | None:
    for match in CPF_REGEX.finditer(text):
        candidate = match.group(0)
        if is_valid_cpf(candidate):
            return normalize_cpf(candidate)
    return None


def _has_injection_signal(text: str) -> bool:
    folded = fold_text(text)
    return any(pattern.search(folded) is not None for pattern in _INJECTION_PATTERNS)


def _clean_citation_title(raw_title: str) -> str:
    cleaned = raw_title.replace("*", "").replace("_", "").strip()
    cleaned = cleaned.rstrip(_TRAILING_PUNCTUATION).strip()
    cleaned = _TRAILING_PARENTHETICAL.sub("", cleaned).strip()
    return cleaned.rstrip(_TRAILING_PUNCTUATION).strip()


def normalize_source_title(title: str) -> str:
    return fold_text(_clean_citation_title(title))


def extract_cited_titles(text: str) -> list[str]:
    titles: list[str] = []
    for line in text.splitlines():
        without_bullet = _LIST_BULLET_PREFIX.sub("", line.strip())
        match = _CITATION_LINE_PATTERN.match(without_bullet)
        if match is not None:
            titles.append(_clean_citation_title(match.group(1)))
    return titles


def _pseudonymize_cpf(raw_cpf: str, salt: str) -> str:
    digits = normalize_cpf(raw_cpf)
    digest = hmac.new(salt.encode("utf-8"), digits.encode("utf-8"), hashlib.sha256)
    return f"cpf#{digest.hexdigest()[:8]}"


@cache
def _get_presidio_engines() -> tuple[Any, Any, Any]:
    from presidio_analyzer import (
        AnalyzerEngine,
        Pattern,
        PatternRecognizer,
        RecognizerRegistry,
    )
    from presidio_analyzer.nlp_engine import NoOpNlpEngine
    from presidio_analyzer.predefined_recognizers import PhoneRecognizer
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)
    nlp_engine = NoOpNlpEngine(models=[{"lang_code": "pt", "model_name": "none"}])
    registry = RecognizerRegistry(supported_languages=["pt"])
    registry.load_predefined_recognizers(languages=["pt"], nlp_engine=nlp_engine)
    registry.remove_recognizer("PhoneRecognizer")
    registry.add_recognizer(
        PhoneRecognizer(supported_language="pt", supported_regions=("BR",))
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="CPF",
            supported_language="pt",
            patterns=[Pattern(name="cpf", regex=CPF_PATTERN, score=1.0)],
        )
    )
    analyzer = AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=["pt"],
    )
    anonymizer = AnonymizerEngine()
    return analyzer, anonymizer, OperatorConfig


def _prioritize_cpf_matches(results: list[Any]) -> list[Any]:
    cpf_spans = [
        (result.start, result.end) for result in results if result.entity_type == "CPF"
    ]
    return [
        result
        for result in results
        if result.entity_type == "CPF"
        or not any(
            result.start < cpf_end and cpf_start < result.end
            for cpf_start, cpf_end in cpf_spans
        )
    ]


class Guard:
    def __init__(self, catalog: Catalog):
        self._catalog = catalog
        self._salt = os.environ.get(TRACE_SALT_ENV) or _PROCESS_SALT

    def inspect_input(self, text: str) -> InputVerdict:
        return InputVerdict(
            model_text=CPF_REGEX.sub(_CPF_PLACEHOLDER, text),
            cpf=_extract_first_valid_cpf(text),
            injection_suspected=_has_injection_signal(text),
        )

    def check_reply(
        self,
        text: str,
        retrieved_sources: Iterable[str],
    ) -> OutputVerdict:
        invalid_skus = [
            sku
            for sku in dict.fromkeys(_SKU_IN_TEXT_REGEX.findall(text))
            if not self._catalog.exists(sku)
        ]
        allowed_titles = {
            normalize_source_title(source) for source in retrieved_sources
        }
        unretrieved_sources = [
            title
            for title in extract_cited_titles(text)
            if normalize_source_title(title) not in allowed_titles
        ]
        return OutputVerdict(
            invalid_skus=invalid_skus,
            unretrieved_sources=unretrieved_sources,
        )

    def mask(self, text: str) -> str:
        analyzer, anonymizer, operator_config = _get_presidio_engines()
        raw_results = analyzer.analyze(
            text=text,
            language="pt",
            entities=["CPF", "EMAIL_ADDRESS", "PHONE_NUMBER"],
        )
        results = _prioritize_cpf_matches(raw_results)
        salt = self._salt
        anonymized = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={
                "CPF": operator_config(
                    "custom",
                    {"lambda": lambda raw_cpf: _pseudonymize_cpf(raw_cpf, salt)},
                ),
                "EMAIL_ADDRESS": operator_config("replace", {"new_value": "[e-mail]"}),
                "PHONE_NUMBER": operator_config("replace", {"new_value": "[telefone]"}),
            },
        )
        return anonymized.text
