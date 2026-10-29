import re

import pytest

from tests.kit.helpers import KIT_DIR, fold, prompts_section

pytestmark = pytest.mark.integration

POLICIES = KIT_DIR / "policies"

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
RULE_VERDICTS = {
    "troca-e-devolucao.md": (
        r"por arrependimento em ate 7 dias corridos apos a entrega",
        r"defeito, voce pode pedir a devolucao ou a troca em ate 30 dias corridos",
        r"racao e areia com embalagem aberta so sao trocadas em caso de defeito",
        r"lacrado, vale a regra de arrependimento: ate 7 dias corridos",
        "racao",
        "areia",
        "defeito",
        "embalagem aberta",
    ),
    "reembolso.md": (
        r"sempre feito no meio de pagamento original",
        r"pix: [^\n]*ate 5 dias uteis",
        r"cartao: [^\n]*ate 2 faturas",
    ),
    "frete.md": (
        r"sul e sudeste: [^\n]*acima de r\$ 149\.",
        r"centro-oeste, nordeste e norte: [^\n]*acima de r\$ 249\.",
        r"exatamente r\$ 149 [^.\n]*paga frete",
        r"exatamente r\$ 249 [^.\n]*tambem paga frete",
        r"estritamente maior",
    ),
    "cancelamento.md": (
        r"so e permitido antes de o pedido chegar ao status enviado",
        r"enquanto ele esta em pago ou em em separacao",
        r"enviado ou entregue, o pedido nao pode mais ser cancelado",
    ),
}
FORBIDDEN_TERMS = (
    "garantia estendida",
    "fidelidade",
    "programa de pontos",
    "cashback",
    "clube",
)
EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")


def read_policy(name: str) -> str:
    return (POLICIES / name).read_text(encoding="utf-8")


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
    ("name", "pattern"),
    [
        (name, pattern)
        for name, patterns in RULE_VERDICTS.items()
        for pattern in patterns
    ],
)
def test_it_should_state_the_business_rule_verdict_in_its_policy(name, pattern):
    text = fold(read_policy(name))

    assert re.search(pattern, text)


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
