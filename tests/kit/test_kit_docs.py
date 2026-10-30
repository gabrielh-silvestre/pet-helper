import re
from pathlib import Path

import pytest

from tests.kit.helpers import KIT_DIR, nfc, section_of

pytestmark = pytest.mark.integration

KIT = KIT_DIR
ROOT = KIT_DIR.parent
CASES = ROOT / "evals" / "cases"

BRANDS = ["Focinhex", "Bigodin", "Latidão", "Miauzito", "Patacorp"]
PROMPT_SECTIONS = [
    "Pesquisa de FAQs",
    "Catálogo",
    "Pedidos",
    "Políticas",
    "Casos de eval",
    "Auditoria do critic",
]
PROMPT_METADATA = ["- Agente:", "- Modelo:", "- Data:"]
CSV_COLUMNS = {
    "SKU": "sku",
    "nome": "name",
    "categoria": "category",
    "espécie": "species",
    "preço": "price",
    "estoque": "stock",
    "descrição": "description",
}
INVENTED_POLICIES = [
    "assinatura.md",
    "retirada-em-loja.md",
    "privacidade-e-dados-pessoais.md",
    "canais-e-horarios.md",
]
KNOWN_ABSENT_ORDERS = {"PED-50300", "PED-40800", "PED-99999"}
KIT_SECTIONS = [
    "Data de referência",
    "Loja",
    "Dados fictícios",
    "Mapa de colunas e valores",
    "Regras inventadas",
    "Temas fora de propósito",
    "Mapa de armadilhas",
    "Orçamento de casos",
    "Revisão",
]


def load_text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(ROOT)} does not exist"
    return nfc(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prompts_text() -> str:
    return load_text(KIT / "PROMPTS.md")


@pytest.fixture(scope="module")
def kit_text() -> str:
    return load_text(KIT / "KIT.md")


@pytest.fixture(scope="module")
def glossary_text() -> str:
    return load_text(ROOT / "GLOSSARY.md")


@pytest.fixture(scope="module")
def case_ids() -> set[str]:
    return {path.stem for path in CASES.glob("*.yaml")}


@pytest.fixture(scope="module")
def kit_order_numbers() -> set[str]:
    return set(re.findall(r"PED-\d{5}", (KIT / "orders.json").read_text("utf-8")))


@pytest.fixture(scope="module")
def kit_skus() -> set[str]:
    return set(re.findall(r"PX-\d{4}", (KIT / "catalog.csv").read_text("utf-8")))


def prompt_section(text: str, heading: str) -> str:
    body = section_of(text, heading)
    assert body is not None, f"PROMPTS.md has no '## {heading}' section"
    return body


def fenced_blocks(section: str) -> list[str]:
    return re.findall(r"^````[^\n`]*\n(.*?)^````[ \t]*$", section, re.M | re.S)


def kit_section(text: str, heading: str, level: int = 2) -> str:
    body = section_of(text, heading, level)
    assert body is not None, f"KIT.md has no '{'#' * level} {heading}' section"
    return body


@pytest.mark.parametrize("heading", PROMPT_SECTIONS)
def test_it_should_have_a_section_per_kit_part_in_the_prompts_file(
    prompts_text, heading
):
    assert section_of(prompts_text, heading) is not None


@pytest.mark.parametrize("label", PROMPT_METADATA)
@pytest.mark.parametrize("heading", PROMPT_SECTIONS)
def test_it_should_name_agent_model_and_date_in_every_prompt_section(
    prompts_text, heading, label
):
    section = prompt_section(prompts_text, heading)

    assert re.search(rf"^{re.escape(label)} \S", section, re.M)


@pytest.mark.parametrize("heading", PROMPT_SECTIONS)
def test_it_should_hold_a_non_empty_four_backtick_block_in_every_prompt_section(
    prompts_text, heading
):
    section = prompt_section(prompts_text, heading)

    assert any(block.strip() for block in fenced_blocks(section))


@pytest.mark.parametrize("heading", PROMPT_SECTIONS)
def test_it_should_not_nest_a_triple_backtick_fence_inside_a_prompt_block(
    prompts_text, heading
):
    section = prompt_section(prompts_text, heading)

    nested = [
        block
        for block in fenced_blocks(section)
        if re.search(r"^```(?!`)", block, re.M)
    ]

    assert nested == []


@pytest.mark.parametrize("brand", BRANDS)
def test_it_should_name_every_fixed_brand_in_the_catalog_prompt_section(
    prompts_text, brand
):
    assert brand in prompt_section(prompts_text, "Catálogo")


@pytest.mark.parametrize("heading", KIT_SECTIONS)
def test_it_should_have_every_required_section_in_the_kit_doc(kit_text, heading):
    assert section_of(kit_text, heading) is not None


@pytest.mark.parametrize("heading", ["Critic", "Script", "Dono"])
def test_it_should_have_every_review_subsection_in_the_kit_doc(kit_text, heading):
    review = kit_section(kit_text, "Revisão")

    assert section_of(review, heading, level=3) is not None


@pytest.mark.parametrize("term", ["Confirmação humana", "merge"])
def test_it_should_state_the_human_confirmation_in_the_owner_subsection(kit_text, term):
    owner = kit_section(kit_section(kit_text, "Revisão"), "Dono", level=3)

    assert term in owner


def test_it_should_give_the_reference_date_in_the_kit_doc(kit_text):
    assert "2026-11-03" in kit_section(kit_text, "Data de referência")


def test_it_should_name_the_store_in_the_kit_doc(kit_text):
    assert "petx" in kit_section(kit_text, "Loja")


@pytest.mark.parametrize("brand", BRANDS)
def test_it_should_list_every_fixed_brand_in_the_fictional_data_section(
    kit_text, brand
):
    assert brand in kit_section(kit_text, "Dados fictícios")


@pytest.mark.parametrize("column", CSV_COLUMNS)
def test_it_should_map_every_csv_column_in_the_column_map(kit_text, column):
    assert column in kit_section(kit_text, "Mapa de colunas e valores")


@pytest.mark.parametrize("field", CSV_COLUMNS.values())
def test_it_should_name_every_english_field_in_the_column_map(kit_text, field):
    section = kit_section(kit_text, "Mapa de colunas e valores")

    assert field in section.lower()


@pytest.mark.parametrize("policy", INVENTED_POLICIES)
def test_it_should_cite_every_invented_policy_file_in_the_invented_rules(
    kit_text, policy
):
    assert policy in kit_section(kit_text, "Regras inventadas")


@pytest.mark.parametrize("topic", ["garantia estendida", "fidelidade"])
def test_it_should_mention_every_off_topic_theme(kit_text, topic):
    assert topic in kit_section(kit_text, "Temas fora de propósito").lower()


def test_it_should_declare_the_case_budget_of_31(kit_text):
    section = kit_section(kit_text, "Orçamento de casos")

    assert re.search(r"\bSão 31 casos normais\b", section)


def test_it_should_cite_only_eval_case_files_as_case_ids_in_the_trap_map(
    kit_text, case_ids
):
    traps = kit_section(kit_text, "Mapa de armadilhas")
    cited = set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", traps))

    assert sorted(cited - case_ids) == []


def test_it_should_cite_only_existing_orders_in_the_kit_doc(
    kit_text, kit_order_numbers
):
    cited = set(re.findall(r"PED-\d{5}", kit_text))

    assert sorted(cited - kit_order_numbers - KNOWN_ABSENT_ORDERS) == []


def test_it_should_cite_only_existing_skus_in_the_kit_doc(kit_text, kit_skus):
    cited = set(re.findall(r"PX-\d{4}", kit_text))

    assert sorted(cited - kit_skus) == []


@pytest.mark.parametrize("term", ["Category", "Payment Method"])
def test_it_should_define_the_new_glossary_terms(glossary_text, term):
    pattern = rf"^(#+ |\*\*){re.escape(term)}\b"

    assert re.search(pattern, glossary_text, re.M)
