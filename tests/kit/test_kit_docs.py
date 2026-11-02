import re
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from pet_helper.conversation import build_assistant
from pet_helper.evals.cases import (
    CHECK_TYPES,
    PUBLIC_STATE_KEYS,
    TURNLESS_CHECK_TYPES,
    Check,
    EvalCase,
    JudgeEntry,
    load_cases,
    validate_structure,
)
from pet_helper.gateway import FakeGateway
from tests.kit.helpers import KIT_DIR, REMOVED_CASES, fold, nfc, section_of

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
SCHEMA_HEADING = "Esquema dos casos de eval"
KIT_SECTIONS = [
    "Data de referência",
    "Loja",
    "Dados fictícios",
    "Mapa de colunas e valores",
    "Regras inventadas",
    "Temas fora de propósito",
    "Mapa de armadilhas",
    "Orçamento de casos",
    SCHEMA_HEADING,
    "Revisão",
]
# orders and SKUs the trap map rows of the absorbing case must carry once the removed cases leave
ABSORBED_TRAP_REFS = {
    "rn03-south-exact-149-not-free": {
        "PED-10001",
        "PED-10002",
        "PED-50300",
        "PED-40800",
    },
    "rn03-midwest-discount-not-free": {
        "PED-10005",
        "PED-10006",
        "PX-0020",
        "PX-0041",
    },
    "multi-turn-species-switch": {"PX-0001", "PX-0002", "PX-0005"},
    "multi-turn-other-order": {"PED-10003", "PED-10004"},
    "adv-other-customer-order": {"PED-10004", "PED-99999"},
    "esc-explicit-request": {"PX-0026"},
}
# what the schema section must name so a generator can write a valid case
REFERENCE_KEYS = [
    "replies",
    "tool_calls",
    "state",
    "text",
    "sources",
    "escalation",
    "name",
    "args",
]
TOOL_NAMES = {"search_policies", "get_order", "search_catalog", "escalate_to_human"}
JUDGE_CRITERIA = {"tone", "escalation_judgment", "dont_know_accuracy"}
ESCALATION_REASONS = {
    "animal_health_question",
    "strong_negative_sentiment",
    "repeated_failure",
    "explicit_request",
}
TOOL_CHECK_TYPES = {"tool_called", "tool_not_called"}


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


def test_it_should_declare_the_case_budget_of_20(kit_text):
    section = kit_section(kit_text, "Orçamento de casos")

    assert re.search(r"\bSão 20 casos normais\b", section)


def test_it_should_state_the_ceiling_of_25_visible_cases_in_the_case_budget(kit_text):
    section = kit_section(kit_text, "Orçamento de casos")

    assert re.search(r"\b25\b", section)


def test_it_should_state_the_total_ceiling_of_30_cases_in_the_case_budget(kit_text):
    section = kit_section(kit_text, "Orçamento de casos")

    assert re.search(r"\b30\b", section)


def test_it_should_not_keep_the_old_case_budget_of_31(kit_text):
    section = kit_section(kit_text, "Orçamento de casos")

    assert not re.search(r"\b31\b", section)


def trap_map_ids(kit_text):
    traps = kit_section(kit_text, "Mapa de armadilhas")
    return set(re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", traps))


def trap_map_refs_of(kit_text, case_id):
    """Orders and SKUs on the trap map rows that cite the case."""
    rows = [
        row
        for row in kit_section(kit_text, "Mapa de armadilhas").splitlines()
        if row.startswith("|") and f"`{case_id}`" in row
    ]
    return set(re.findall(r"PED-\d{5}|PX-\d{4}", " ".join(rows)))


def test_it_should_cite_only_eval_case_files_as_case_ids_in_the_trap_map(
    kit_text, case_ids
):
    assert sorted(trap_map_ids(kit_text) - case_ids) == []


def test_it_should_cite_no_removed_case_id_in_the_trap_map(kit_text):
    assert trap_map_ids(kit_text) & REMOVED_CASES == set()


def test_it_should_map_every_eval_case_in_the_trap_map(kit_text, case_ids):
    assert sorted(case_ids - trap_map_ids(kit_text)) == []


@pytest.mark.parametrize(("case_id", "refs"), ABSORBED_TRAP_REFS.items())
def test_it_should_map_the_merged_traps_to_the_case_that_absorbed_them(
    kit_text, case_id, refs
):
    assert refs <= trap_map_refs_of(kit_text, case_id)


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


FIELD_NAMES = sorted(
    {*EvalCase.model_fields, *Check.model_fields, *JudgeEntry.model_fields}
)


def lines_naming(text: str, token: str) -> list[str]:
    return [fold(line) for line in text.splitlines() if f"`{token}`" in line]


def minimal_check(check_type: str, state_key: str | None, tool: str | None) -> dict:
    if check_type == "state":
        return {"type": check_type, "key": state_key}
    check: dict = {
        "type": check_type,
        "value": tool if check_type in TOOL_CHECK_TYPES else "x",
    }
    if check_type not in TURNLESS_CHECK_TYPES:
        check["turn"] = 1
    return check


@pytest.fixture(scope="module")
def schema_text(kit_text) -> str:
    return kit_section(kit_text, SCHEMA_HEADING)


@pytest.fixture(scope="module")
def schema_tokens(schema_text) -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", schema_text))


@pytest.fixture(scope="module")
def snapshot_keys() -> set[str]:
    escalate = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "escalate_to_human",
                "args": {"reason": "explicit_request"},
                "id": "call_1",
            }
        ],
    )
    assistant = build_assistant(
        gateway=FakeGateway([escalate]), kit_dir=KIT, today=date(2026, 11, 3)
    )
    assistant.turn("snapshot", "Oi, CPF 000.000.001-91. Quero falar com um atendente.")
    return set(assistant.snapshot("snapshot"))


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_it_should_name_each_case_check_and_judge_field_in_the_schema_section(
    schema_tokens, field
):
    assert field in schema_tokens


@pytest.mark.parametrize("key", REFERENCE_KEYS)
def test_it_should_name_each_reference_key_in_the_schema_section(schema_tokens, key):
    assert key in schema_tokens


@pytest.mark.parametrize("check_type", sorted(CHECK_TYPES))
def test_it_should_name_each_check_type_in_the_schema_section(
    schema_tokens, check_type
):
    assert check_type in schema_tokens


@pytest.mark.parametrize("check_type", sorted(CHECK_TYPES))
def test_it_should_state_the_scope_of_each_check_type_on_its_line(
    schema_text, check_type
):
    scope = "conversa" if check_type in TURNLESS_CHECK_TYPES else "turn"

    assert any(scope in line for line in lines_naming(schema_text, check_type))


def test_it_should_say_expected_doc_takes_a_list(schema_text):
    assert any("lista" in line for line in lines_naming(schema_text, "expected_doc"))


def test_it_should_state_the_calendar_date_rule_with_the_kit_reference_date(
    schema_text, order_book
):
    assert order_book.reference_date.isoformat() in schema_text


def test_it_should_name_the_fixtures_folder_a_fixture_points_to(schema_text):
    assert "evals/fixtures" in schema_text


def test_it_should_name_every_behavior_tag_the_visible_cases_use(schema_tokens):
    used = {tag for case in load_cases(CASES) for tag in case.behaviors}

    assert sorted(used - schema_tokens) == []


@pytest.mark.parametrize("criterion", sorted(JUDGE_CRITERIA))
def test_it_should_name_each_judge_criterion_in_the_schema_section(
    schema_tokens, criterion
):
    assert criterion in schema_tokens


@pytest.mark.parametrize("reason", sorted(ESCALATION_REASONS))
def test_it_should_name_each_escalation_reason_in_the_schema_section(
    schema_tokens, reason
):
    assert reason in schema_tokens


@pytest.mark.parametrize("tool", sorted(TOOL_NAMES))
def test_it_should_name_each_tool_in_the_schema_section(schema_tokens, tool):
    assert tool in schema_tokens


def test_it_should_list_exactly_the_public_snapshot_keys_a_state_check_may_name(
    schema_tokens, snapshot_keys
):
    documented = snapshot_keys & schema_tokens

    assert documented == PUBLIC_STATE_KEYS


def test_it_should_find_every_public_state_key_in_the_real_snapshot(snapshot_keys):
    assert snapshot_keys >= PUBLIC_STATE_KEYS


def test_it_should_validate_a_minimal_case_built_only_from_the_schema_section(
    schema_text, schema_tokens
):
    today = re.search(r"\d{4}-\d{2}-\d{2}", schema_text)
    state_key = min(PUBLIC_STATE_KEYS & schema_tokens, default=None)
    tool = min(TOOL_NAMES & schema_tokens, default=None)
    criterion = min(JUDGE_CRITERIA & schema_tokens, default=None)
    minimal = {
        "id": "minimal",
        "today": today.group() if today else None,
        "turns": ["Oi"],
        "checks": [
            minimal_check(check_type, state_key, tool)
            for check_type in sorted(CHECK_TYPES & schema_tokens)
        ],
        "judge": [{"criterion": criterion, "turn": 1}],
    }

    assert validate_structure(minimal) == []
