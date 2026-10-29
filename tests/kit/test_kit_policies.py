import re
import unicodedata
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

KIT = Path(__file__).resolve().parents[2] / "kit"
POLICIES = KIT / "policies"
PROMPTS = KIT / "PROMPTS.md"

EXPECTED_FILES = (
    "troca-e-devolucao.md",
    "reembolso.md",
    "frete.md",
    "cancelamento.md",
    "assinatura.md",
    "retirada-em-loja.md",
    "privacidade-e-dados-pessoais.md",
    "canais-e-horarios.md",
)
RULE_TERMS = {
    "troca-e-devolucao.md": (
        "7 dias corridos",
        "30 dias",
        "racao",
        "areia",
        "embalagem aberta",
        "defeito",
    ),
    "reembolso.md": (
        "meio de pagamento original",
        "pix",
        "5 dias uteis",
        "2 faturas",
    ),
    "frete.md": ("149", "249", "sul", "sudeste", "acima de"),
    "cancelamento.md": ("enviado",),
}
FORBIDDEN_TERMS = (
    "garantia estendida",
    "fidelidade",
    "programa de pontos",
    "cashback",
    "clube",
)
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def read_policy(name: str) -> str:
    return (POLICIES / name).read_text(encoding="utf-8")


def prompts_section(title: str) -> str:
    text = PROMPTS.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"PROMPTS.md has no '## {title}' section"
    return match.group(1)


@pytest.fixture(scope="module")
def policy_texts() -> dict[str, str]:
    assert POLICIES.is_dir(), f"{POLICIES} does not exist"
    texts = {
        path.name: path.read_text(encoding="utf-8") for path in POLICIES.glob("*.md")
    }
    assert texts, f"{POLICIES} has no .md files"
    return texts


@pytest.mark.parametrize("name", EXPECTED_FILES)
def test_it_should_have_each_expected_policy_file(name):
    assert (POLICIES / name).is_file()


@pytest.mark.parametrize("name", EXPECTED_FILES)
def test_it_should_start_each_policy_with_an_h1(name):
    first_line = read_policy(name).lstrip().splitlines()[0]

    assert re.match(r"# \S", first_line)


@pytest.mark.parametrize(
    ("name", "term"),
    [(name, term) for name, terms in RULE_TERMS.items() for term in terms],
)
def test_it_should_state_the_business_rule_term_in_its_policy(name, term):
    text = fold(read_policy(name))

    assert term in text


def test_it_should_state_that_shipping_is_not_part_of_the_purchase_value():
    text = fold(read_policy("frete.md"))

    assert "sem o frete" in text or "sem frete" in text


def test_it_should_refer_the_cancellation_policy_to_exchanges_and_returns():
    text = fold(read_policy("cancelamento.md"))

    assert re.search(r"troca[- ]e[- ]devolucao", text)


def test_it_should_not_mention_forbidden_programs_in_the_expected_policies(
    policy_texts,
):
    found = {
        (name, term)
        for name in EXPECTED_FILES
        for term in FORBIDDEN_TERMS
        if term in fold(policy_texts[name])
    }

    assert found == set()


def test_it_should_use_only_example_com_or_its_subdomains_for_emails_in_policies(
    policy_texts,
):
    domains = {
        match.group(1).lower()
        for text in policy_texts.values()
        for match in EMAIL.finditer(text)
    }

    outside = {
        d for d in domains if d != "example.com" and not d.endswith(".example.com")
    }
    assert outside == set()


@pytest.mark.parametrize("title", ["Pesquisa de FAQs", "Políticas"])
def test_it_should_record_a_fenced_prompt_in_each_policy_prompt_section(title):
    section = prompts_section(title)

    assert section.count("```") >= 2
