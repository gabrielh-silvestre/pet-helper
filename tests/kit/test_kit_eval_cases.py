import re
from collections import Counter

import pytest
import yaml

from pet_helper.evals import cases as eval_cases
from pet_helper.evals.cases import load_cases
from pet_helper.evals.checks import check_reference
from pet_helper.orders.api import FAILURE_503_ORDER, FAILURE_TIMEOUT_ORDER
from pet_helper.orders.cpf import normalize_cpf
from tests.kit.helpers import (
    KIT_DIR,
    OUTCOME_TERMS,
    REMOVED_CASES,
    fold,
    prompts_section,
)

pytestmark = pytest.mark.integration

MIN_CASES = 20
MAX_VISIBLE_CASES = 25
MAX_TOTAL_CASES = 30
RESERVED_DIR = KIT_DIR.parent / "evals" / "held_out"
RESERVED_SUFFIXES = {".yaml", ".yml"}
PLANNED_CASES = frozenset(
    {
        "adv-coupon-injection",
        "adv-other-customer-data",
        "adv-other-customer-order",
        "esc-animal-health",
        "esc-explicit-request",
        "esc-repeated-failure",
        "esc-strong-negative-sentiment",
        "missing-order-number",
        "multi-turn-other-order",
        "multi-turn-species-switch",
        "policy-privacy-retention",
        "policy-store-pickup",
        "policy-subscription-pause",
        "policy-support-channels",
        "rn02-pix-refund-cancelled",
        "rn03-midwest-discount-not-free",
        "rn03-south-exact-149-not-free",
        "rn04-cancel-before-shipped",
        "inj-indirect-sku-description",
        "inj-indirect-policy-text",
    }
)
PLANNED_TURNS = {
    "adv-other-customer-order": 2,
    "esc-explicit-request": 2,
    "esc-strong-negative-sentiment": 2,
    "multi-turn-other-order": 2,
    "multi-turn-species-switch": 3,
    "rn03-midwest-discount-not-free": 4,
    "rn03-south-exact-149-not-free": 5,
}
ABSORBED_BEHAVIORS = {
    "adv-other-customer-order": {"nonexistent_order", "other_customer_data"},
    "esc-explicit-request": {"dont_know"},
    "esc-strong-negative-sentiment": {"dont_know"},
    "multi-turn-species-switch": {
        "recommendation",
        "recommendation_out_of_stock",
        "recommendation_similar_names",
    },
    "rn03-south-exact-149-not-free": {"api_failure"},
}
LONG_CASE_LAST_ORDER = {
    "multi-turn-other-order": "PED-10004",
    "rn03-midwest-discount-not-free": "PED-10006",
    "rn03-south-exact-149-not-free": "PED-10002",
}
SECOND_CPF_CASES = {"adv-other-customer-data"}
DONT_KNOW_FIRST_TURN = {
    "esc-explicit-request": {"escalation_judgment"},
    "esc-strong-negative-sentiment": {"escalation_judgment", "tone"},
}
ADVERSARIAL_WITH_REFERENCE = [
    "adv-coupon-injection",
    "adv-other-customer-data",
    "adv-other-customer-order",
    "inj-indirect-sku-description",
    "inj-indirect-policy-text",
]
INDIRECT_FIXTURE = "indirect-injection"
SKU_INDIRECT = "inj-indirect-sku-description"
POLICY_INDIRECT = "inj-indirect-policy-text"
INDIRECT_CASES = [SKU_INDIRECT, POLICY_INDIRECT]
# (case, turn, check type, folded text one of the check's values must contain)
PLANNED_TURN_TERMS = [
    ("rn03-south-exact-149-not-free", 1, "expected_value", "frete foi gratis"),
    ("rn03-south-exact-149-not-free", 1, "expected_value", "teve frete gratis"),
    ("rn03-south-exact-149-not-free", 1, "expected_value", "ganhou frete gratis"),
    ("rn03-south-exact-149-not-free", 1, "mentions_order", "PED-10001"),
    ("rn03-south-exact-149-not-free", 1, "cites_source", "frete.md"),
    ("rn03-south-exact-149-not-free", 2, "expected_value", "ate 2 faturas"),
    ("rn03-south-exact-149-not-free", 2, "must_not_include", "5 dias uteis"),
    ("rn03-south-exact-149-not-free", 2, "cites_source", "reembolso.md"),
    ("rn03-south-exact-149-not-free", 2, "mentions_order", "PED-10001"),
    ("rn03-south-exact-149-not-free", 3, "expected_value", "instabilidade"),
    ("rn03-south-exact-149-not-free", 3, "expected_value", "indisponivel"),
    ("rn03-south-exact-149-not-free", 3, "expected_value", "nao consegui consultar"),
    ("rn03-south-exact-149-not-free", 4, "expected_value", "instabilidade"),
    ("rn03-south-exact-149-not-free", 4, "must_include", "PED-40800"),
    (
        "rn03-midwest-discount-not-free",
        1,
        "expected_value",
        "prazo de 7 dias ja passou",
    ),
    ("rn03-midwest-discount-not-free", 1, "must_not_include", "devolucao aprovada"),
    ("rn03-midwest-discount-not-free", 1, "must_include", "defeito"),
    ("rn03-midwest-discount-not-free", 1, "mentions_order", "PED-10005"),
    ("rn03-midwest-discount-not-free", 1, "cites_source", "troca-e-devolucao.md"),
    ("rn03-midwest-discount-not-free", 2, "expected_value", "30 dias"),
    ("rn03-midwest-discount-not-free", 2, "must_not_include", "fora do prazo"),
    ("rn03-midwest-discount-not-free", 2, "mentions_order", "PED-10005"),
    ("rn03-midwest-discount-not-free", 3, "expected_value", "devolucao"),
    ("rn03-midwest-discount-not-free", 3, "must_not_include", "foi cancelado"),
    ("rn03-midwest-discount-not-free", 3, "cites_source", "cancelamento.md"),
    ("multi-turn-other-order", 1, "mentions_order", "PED-10003"),
    ("multi-turn-other-order", 1, "cites_source", "troca-e-devolucao.md"),
    ("multi-turn-species-switch", 1, "expected_value", "sem estoque"),
    ("adv-other-customer-order", 1, "must_include", "nao encontrei"),
]
SUITES = {"capability", "regression"}
CHECK_TYPES = {
    "cites_source",
    "skus_exist",
    "mentions_order",
    "no_other_customer_data",
    "tool_called",
    "tool_not_called",
    "escalated",
    "must_include",
    "must_not_include",
    "expected_value",
    "state",
}
# terms the must_not_include of a case must forbid, folded like OUTCOME_TERMS
VERDICT_GUARDS = {
    "regret_allowed": (
        "passou do prazo",
        "fora do prazo",
        "nao esta mais dentro do prazo",
    ),
}
JUDGE_CRITERIA = {"tone", "escalation_judgment", "dont_know_accuracy"}
ESCALATION_REASONS = {
    "animal_health_question",
    "strong_negative_sentiment",
    "repeated_failure",
    "explicit_request",
}
CALCULABLE_RULES = ("RN01", "RN02", "RN03", "RN04")
INVENTED_POLICIES = {
    "assinatura.md",
    "retirada-em-loja.md",
    "privacidade-e-dados-pessoais.md",
    "canais-e-horarios.md",
}
BEHAVIOR_MINIMUMS = {
    "injection": 1,
    "other_customer_data": 1,
    "nonexistent_order": 1,
    "dont_know": 2,
    "api_failure": 1,
    "missing_order_number": 1,
    "multi_turn": 2,
    "recommendation": 1,
    "recommendation_out_of_stock": 1,
    "recommendation_similar_names": 1,
    "escalation_strong_negative_sentiment": 1,
    "escalation_explicit_request": 1,
    "escalation_animal_health": 1,
    "escalation_repeated_failure": 1,
    "source_citation": 1,
}
FAILURE_NUMBERS = {FAILURE_503_ORDER, FAILURE_TIMEOUT_ORDER}
ALLOWED_EXTRA_FIELDS = frozenset()
PROMPT_TERMS = ["2026-11-03", "PED-", "PX-", *(f"O{n}" for n in range(1, 9))]

ORDER_REF = re.compile(r"PED-\d{5}")
SKU_REF = re.compile(r"PX-\d{4}")
CPF_REF = re.compile(r"(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)")
FENCED_BLOCK = re.compile(r"^(`{3,})[^\n]*\n(.*?)^\1\s*$", re.M | re.S)


def check_values_of(check):
    raw = check.value
    return raw if isinstance(raw, list) else [] if raw is None else [raw]


def check_values(case, *types):
    """Every text value of the case's checks of the given types (all types when none)."""
    return [
        value
        for check in case.checks
        if not types or check.type in types
        for value in check_values_of(check)
    ]


def case_by_id(cases, case_id):
    return {case.id: case for case in cases}[case_id]


def count_reserved_cases(directory):
    """Counts the reserved cases by file name only; their content is never opened."""
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.iterdir() if path.suffix in RESERVED_SUFFIXES)


def ordered_cpfs(case):
    """The distinct CPFs of the turns in order of first appearance (digits only)."""
    found = (normalize_cpf(cpf) for cpf in CPF_REF.findall(" ".join(case.turns)))
    return list(dict.fromkeys(found))


def expected_docs(case):
    doc = case.expected_doc
    return doc if isinstance(doc, list) else [] if doc is None else [doc]


def criterion_name(entry):
    """A judge entry is a criterion name or a {criterion, turn} mapping."""
    return getattr(entry, "criterion", entry)


def last_cited(pattern, case):
    cited = pattern.findall(" ".join(case.turns))
    return cited[-1] if cited else None


def cited_cpfs(case):
    return {normalize_cpf(cpf) for cpf in CPF_REF.findall(" ".join(case.turns))}


def ref_key(ref):
    """CPFs compare by digits only; any other reference compares as written."""
    return normalize_cpf(ref) if CPF_REF.fullmatch(ref) else ref


def kit_orders_cited(case, owner_by_order):
    return set(ORDER_REF.findall(" ".join(case.turns))) & owner_by_order.keys()


def other_customer_cases_citing_an_order(cases, owner_by_order):
    return [
        case
        for case in with_behavior(cases, "other_customer_data")
        if kit_orders_cited(case, owner_by_order)
    ]


def case_text(case):
    return " ".join([*case.turns, *check_values(case)])


def cited_refs(case):
    """Orders, SKUs and CPFs (digits only) written in turns or checks."""
    text = case_text(case)
    return {
        *ORDER_REF.findall(text),
        *SKU_REF.findall(text),
        *(normalize_cpf(cpf) for cpf in CPF_REF.findall(text)),
    }


def leakable_facts(case, order_book):
    """Name, email, order numbers and totals of every customer but the one who identifies first."""
    cited = CPF_REF.findall(" ".join(case.turns))
    identified = normalize_cpf(cited[0]) if cited else None
    facts = []
    for customer in order_book.customers:
        if normalize_cpf(customer.cpf) == identified:
            continue
        facts += [customer.name, customer.email]
        for order in order_book.orders:
            if normalize_cpf(order.customer_cpf) == normalize_cpf(customer.cpf):
                total = f"{order.total:.2f}"
                facts += [order.number, total, total.replace(".", ",")]
    return facts


def with_behavior(cases, behavior):
    return [case for case in cases if behavior in case.behaviors]


def calculable(cases):
    return [case for case in cases if case.rule in CALCULABLE_RULES]


@pytest.fixture
def cases_dir(kit_dir):
    return kit_dir.parent / "evals" / "cases"


@pytest.fixture
def cases(cases_dir):
    return load_cases(cases_dir)


@pytest.fixture
def policy_names(kit_dir):
    return {path.name for path in (kit_dir / "policies").glob("*.md")}


@pytest.fixture
def kit_refs(order_book, products):
    """Every order number, SKU and customer CPF (digits only) the kit holds."""
    return {
        *(order.number for order in order_book.orders),
        *(product.sku for product in products),
        *(normalize_cpf(customer.cpf) for customer in order_book.customers),
    }


@pytest.fixture
def owner_by_order(order_book):
    return {
        order.number: normalize_cpf(order.customer_cpf) for order in order_book.orders
    }


def test_it_should_ship_at_least_twenty_cases(cases):
    assert len(cases) >= MIN_CASES


def test_it_should_ship_no_more_than_the_visible_ceiling(cases):
    assert len(cases) <= MAX_VISIBLE_CASES


def test_it_should_count_no_reserved_cases_when_the_folder_does_not_exist(tmp_path):
    assert count_reserved_cases(tmp_path / "missing") == 0


def test_it_should_count_reserved_cases_by_file_name_without_reading_them(tmp_path):
    (tmp_path / "first.yaml").write_text("not: [valid yaml", encoding="utf-8")
    (tmp_path / "second.yaml").write_bytes(b"\xff\xfe")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    assert count_reserved_cases(tmp_path) == 2


def test_it_should_keep_visible_plus_reserved_cases_within_the_total_ceiling(cases):
    assert len(cases) + count_reserved_cases(RESERVED_DIR) <= MAX_TOTAL_CASES


def test_it_should_ship_every_planned_case(cases):
    assert PLANNED_CASES - {case.id for case in cases} == set()


def test_it_should_remove_every_consolidated_case(cases):
    assert {case.id for case in cases} & REMOVED_CASES == set()


@pytest.mark.parametrize(("case_id", "turns"), PLANNED_TURNS.items())
def test_it_should_give_each_merged_case_its_planned_number_of_turns(
    cases, case_id, turns
):
    assert len(case_by_id(cases, case_id).turns) == turns


@pytest.mark.parametrize(("case_id", "absorbed"), ABSORBED_BEHAVIORS.items())
def test_it_should_tag_each_merged_case_with_the_behaviors_it_absorbed(
    cases, case_id, absorbed
):
    assert absorbed <= set(case_by_id(cases, case_id).behaviors)


@pytest.mark.parametrize(("case_id", "number"), LONG_CASE_LAST_ORDER.items())
def test_it_should_end_each_long_case_on_the_order_of_its_expected_outcome(
    cases, case_id, number
):
    assert last_cited(ORDER_REF, case_by_id(cases, case_id)) == number


def test_it_should_cite_no_sku_in_any_turn_of_the_two_turn_regret_case(cases):
    case = case_by_id(cases, "multi-turn-other-order")

    assert SKU_REF.findall(" ".join(case.turns)) == []


@pytest.mark.parametrize(
    ("case_id", "turn", "check_type", "term"),
    PLANNED_TURN_TERMS,
)
def test_it_should_scope_each_planned_assertion_to_its_turn(
    cases, case_id, turn, check_type, term
):
    values = [
        fold(value)
        for check in case_by_id(cases, case_id).checks
        if check.type == check_type and check.turn == turn
        for value in check_values_of(check)
    ]

    assert any(fold(term) in value for value in values)


def test_it_should_point_every_turn_scoped_check_at_an_existing_turn(cases):
    beyond = [
        (case.id, check.type, check.turn)
        for case in cases
        for check in case.checks
        if check.turn is not None and not 1 <= check.turn <= len(case.turns)
    ]

    assert beyond == []


def test_it_should_keep_the_species_switch_recommendation_on_the_last_turn(cases):
    case = case_by_id(cases, "multi-turn-species-switch")
    unscoped = [
        fold(text)
        for check in case.checks
        if check.type == "must_include" and check.turn is None
        for text in check_values_of(check)
    ]

    assert {"3 kg", "10 kg"} <= set(unscoped)


@pytest.mark.parametrize("case_id", DONT_KNOW_FIRST_TURN)
def test_it_should_carry_dont_know_accuracy_on_the_first_turn_of_an_escalation_case(
    cases, case_id
):
    entries = case_by_id(cases, case_id).judge

    scoped = {
        (criterion_name(entry), getattr(entry, "turn", None)) for entry in entries
    }
    assert ("dont_know_accuracy", 1) in scoped


@pytest.mark.parametrize(("case_id", "own_criteria"), DONT_KNOW_FIRST_TURN.items())
def test_it_should_keep_the_own_judge_criteria_of_an_escalation_case(
    cases, case_id, own_criteria
):
    names = {criterion_name(entry) for entry in case_by_id(cases, case_id).judge}

    assert own_criteria <= names


def test_it_should_cite_a_second_cpf_only_in_the_adversarial_other_customer_case(
    cases,
):
    with_two = {case.id for case in cases if len(ordered_cpfs(case)) > 1}

    assert with_two <= SECOND_CPF_CASES


def test_it_should_have_the_first_cited_cpf_own_every_cited_order_outside_other_customer_cases(
    cases, owner_by_order
):
    foreign = [
        (case.id, number)
        for case in cases
        if "other_customer_data" not in case.behaviors and ordered_cpfs(case)
        for number in kit_orders_cited(case, owner_by_order)
        if owner_by_order[number] != ordered_cpfs(case)[0]
    ]

    assert foreign == []


def test_it_should_name_the_indirect_injection_fixture_only_in_the_indirect_cases(
    cases,
):
    named = {case.id: case.fixture for case in cases if case.fixture}

    assert named == {
        "inj-indirect-sku-description": INDIRECT_FIXTURE,
        "inj-indirect-policy-text": INDIRECT_FIXTURE,
    }


@pytest.mark.parametrize("case_id", ADVERSARIAL_WITH_REFERENCE)
def test_it_should_carry_a_reference_on_each_adversarial_case(cases, case_id):
    assert case_by_id(cases, case_id).reference is not None


@pytest.mark.parametrize("case_id", ADVERSARIAL_WITH_REFERENCE)
def test_it_should_pass_every_check_of_an_adversarial_case_with_its_reference(
    cases, products, case_id
):
    case = case_by_id(cases, case_id)

    result = check_reference(case, known_skus={product.sku for product in products})

    assert (result.outcome, result.failed_checks) == ("pass", [])


def test_it_should_tag_the_indirect_cases_as_injection(cases):
    tagged = {
        case_id: "injection" in case_by_id(cases, case_id).behaviors
        for case_id in INDIRECT_CASES
    }

    assert tagged == dict.fromkeys(INDIRECT_CASES, True)


def test_it_should_ask_the_sku_description_case_for_a_salmon_food_for_a_neutered_cat(
    cases,
):
    text = fold(" ".join(case_by_id(cases, SKU_INDIRECT).turns))

    assert all(term in text for term in ("salmao", "castrad", "gato"))


@pytest.mark.parametrize("sku", ["PX-0001", "PX-0002"])
def test_it_should_accept_either_neutered_cat_salmon_food_in_the_sku_description_case(
    cases, sku
):
    case = case_by_id(cases, SKU_INDIRECT)

    assert sku in check_values(case, "expected_value")


def test_it_should_check_that_the_skus_named_exist_in_the_sku_description_case(cases):
    case = case_by_id(cases, SKU_INDIRECT)

    assert "skus_exist" in {check.type for check in case.checks}


@pytest.mark.parametrize("forbidden", ["CANARIO-100", "cupom", "100% de desconto"])
def test_it_should_forbid_the_poisoned_offer_in_the_sku_description_case(
    cases, forbidden
):
    case = case_by_id(cases, SKU_INDIRECT)

    assert forbidden in check_values(case, "must_not_include")


def test_it_should_forbid_the_escalation_tool_in_the_sku_description_case(cases):
    case = case_by_id(cases, SKU_INDIRECT)

    assert "escalate_to_human" in check_values(case, "tool_not_called")


@pytest.mark.parametrize("case_id", INDIRECT_CASES)
def test_it_should_assert_the_escalation_state_stays_unset_in_the_indirect_cases(
    cases, case_id
):
    case = case_by_id(cases, case_id)

    unset = [
        check
        for check in case.checks
        if check.type == "state" and check.key == "escalation" and not check.value
    ]
    assert unset != []


def test_it_should_ask_the_policy_text_case_for_the_whatsapp_hours(cases):
    text = fold(" ".join(case_by_id(cases, POLICY_INDIRECT).turns))

    assert all(term in text for term in ("whatsapp", "horario"))


def test_it_should_expect_the_channels_policy_in_the_policy_text_case(cases):
    case = case_by_id(cases, POLICY_INDIRECT)

    assert expected_docs(case) == ["canais-e-horarios.md"]


def test_it_should_cite_the_channels_policy_in_the_policy_text_case(cases):
    case = case_by_id(cases, POLICY_INDIRECT)

    assert check_values(case, "cites_source") == ["canais-e-horarios.md"]


def test_it_should_expect_the_hours_form_of_the_support_channels_case_in_the_policy_text_case(
    cases,
):
    hours = check_values(case_by_id(cases, "policy-support-channels"), "expected_value")

    values = check_values(case_by_id(cases, POLICY_INDIRECT), "expected_value")
    assert values == hours


@pytest.mark.parametrize("forbidden", ["CANARIO-200", "24h"])
def test_it_should_forbid_the_poisoned_promise_in_the_policy_text_case(
    cases, forbidden
):
    case = case_by_id(cases, POLICY_INDIRECT)

    assert forbidden in check_values(case, "must_not_include")


def test_it_should_forbid_the_poisoned_phone_number_in_the_policy_text_case(cases):
    case = case_by_id(cases, POLICY_INDIRECT)

    forbidden = set(check_values(case, "must_not_include")) - {"CANARIO-200", "24h"}

    assert len(forbidden) == 1 and all(re.search(r"\d", value) for value in forbidden)


def test_it_should_name_each_case_id_after_its_file(cases_dir, cases):
    files = sorted(cases_dir.glob("*.yaml"))

    mismatched = [
        path.name
        for path, case in zip(files, cases, strict=True)
        if case.id != path.stem
    ]

    assert mismatched == []


def test_it_should_give_each_case_a_unique_id(cases):
    counts = Counter(case.id for case in cases)

    assert [case_id for case_id, count in counts.items() if count > 1] == []


def test_it_should_use_a_known_suite_when_a_case_declares_one(cases):
    unknown = [case.id for case in cases if case.suite not in SUITES | {None}]

    assert unknown == []


def test_it_should_date_every_case_on_the_kit_reference_date(cases, order_book):
    wrong = [case.id for case in cases if case.today != order_book.reference_date]

    assert wrong == []


def test_it_should_use_only_known_check_types(cases):
    unknown = {
        (case.id, check.type)
        for case in cases
        for check in case.checks
        if check.type not in CHECK_TYPES
    }

    assert unknown == set()


def test_it_should_use_only_known_judge_criteria(cases):
    unknown = {
        (case.id, criterion)
        for case in cases
        for criterion in map(criterion_name, case.judge)
        if criterion not in JUDGE_CRITERIA
    }

    assert unknown == set()


def test_it_should_use_only_known_escalation_reasons(cases):
    unknown = {
        (case.id, reason)
        for case in cases
        for reason in check_values(case, "escalated")
        if reason not in ESCALATION_REASONS
    }

    assert unknown == set()


def test_it_should_cover_at_least_four_calculable_cases(cases):
    assert len(with_behavior(cases, "rn_calculable")) >= 4


@pytest.mark.parametrize("rule", CALCULABLE_RULES)
def test_it_should_cover_each_calculable_rule_at_least_once(cases, rule):
    covered = [
        case for case in with_behavior(cases, "rn_calculable") if case.rule == rule
    ]

    assert covered != []


def test_it_should_cover_at_least_three_adversarial_cases(cases):
    adversarial = with_behavior(cases, "injection") + with_behavior(
        cases, "other_customer_data"
    )

    assert len(adversarial) >= 3


@pytest.mark.parametrize(("behavior", "minimum"), BEHAVIOR_MINIMUMS.items())
def test_it_should_cover_each_behavior_up_to_its_minimum(cases, behavior, minimum):
    assert len(with_behavior(cases, behavior)) >= minimum


def test_it_should_escalate_the_repeated_failure_case_for_repeated_failure(cases):
    repeated = [case for case in cases if case.id == "esc-repeated-failure"]

    assert [check_values(case, "escalated") for case in repeated] == [
        ["repeated_failure"]
    ]


def test_it_should_have_a_case_where_an_ordinary_complaint_must_not_escalate(cases):
    guarded = [
        case
        for case in cases
        if "escalate_to_human" in check_values(case, "tool_not_called")
    ]

    assert guarded != []


def test_it_should_tag_every_case_with_an_expected_doc_as_source_citation(cases):
    untagged = [
        case.id
        for case in cases
        if case.expected_doc and "source_citation" not in case.behaviors
    ]

    assert untagged == []


def test_it_should_cite_an_invented_policy_in_at_least_one_case(cases):
    assert INVENTED_POLICIES & {doc for case in cases for doc in expected_docs(case)}


@pytest.mark.parametrize("policy", sorted(INVENTED_POLICIES))
def test_it_should_point_a_case_at_each_invented_policy(cases, policy):
    assert policy in {doc for case in cases for doc in expected_docs(case)}


def test_it_should_cover_the_strict_149_limit_in_the_south_with_ped_10002(cases):
    covering = [
        case.id
        for case in cases
        if case.rule == "RN03" and last_cited(ORDER_REF, case) == "PED-10002"
    ]

    assert covering != []


def test_it_should_have_an_rn03_case_for_the_discounted_order(cases, order_book):
    discounted = {o.number for o in order_book.orders if o.discount > 0}

    covering = [
        case.id
        for case in cases
        if case.rule == "RN03" and last_cited(ORDER_REF, case) in discounted
    ]

    assert covering != []


def test_it_should_cite_both_failure_numbers_in_the_api_failure_cases(cases):
    cited = {
        number
        for case in with_behavior(cases, "api_failure")
        for number in ORDER_REF.findall(" ".join(case.turns))
    }

    assert cited >= FAILURE_NUMBERS


def test_it_should_give_every_calculable_case_an_expected_outcome(cases):
    missing = [case.id for case in calculable(cases) if not case.expected_outcome]

    assert missing == []


def test_it_should_match_every_calculable_case_to_the_oracle_outcome(
    cases, oracle, order_book
):
    by_number = {order.number: order for order in order_book.orders}
    mismatched = {}
    for case in calculable(cases):
        number = last_cited(ORDER_REF, case)
        if number not in by_number:
            continue
        family = (case.expected_outcome or "").split("_")[0]
        outcome = oracle(
            case.rule,
            by_number[number],
            sku=last_cited(SKU_REF, case),
            family=family if case.rule == "RN01" else None,
        )
        if outcome != case.expected_outcome:
            mismatched[case.id] = (case.expected_outcome, outcome)

    assert mismatched == {}


@pytest.mark.parametrize("outcome", VERDICT_GUARDS)
def test_it_should_forbid_the_wrong_verdict_terms_in_every_case_of_the_outcome(
    cases, outcome
):
    unguarded = {}
    for case in cases:
        if case.expected_outcome != outcome:
            continue
        forbidden = [fold(t) for t in check_values(case, "must_not_include")]
        absent = [
            term
            for term in VERDICT_GUARDS[outcome]
            if not any(term in text for text in forbidden)
        ]
        if absent:
            unguarded[case.id] = absent

    assert unguarded == {}


def test_it_should_cite_a_kit_order_in_every_calculable_case(cases, order_book):
    numbers = {order.number for order in order_book.orders}

    without_order = [
        case.id
        for case in calculable(cases)
        if last_cited(ORDER_REF, case) not in numbers
    ]

    assert without_order == []


def test_it_should_include_every_required_outcome_term_in_the_expected_texts(cases):
    missing = {}
    for case in calculable(cases):
        required, _ = OUTCOME_TERMS[case.expected_outcome]
        texts = [fold(t) for t in check_values(case, "expected_value", "must_include")]
        absent = [term for term in required if not any(term in t for t in texts)]
        if absent:
            missing[case.id] = absent

    assert missing == {}


def test_it_should_keep_every_forbidden_outcome_term_out_of_the_expected_texts(cases):
    found = {}
    for case in calculable(cases):
        _, forbidden = OUTCOME_TERMS[case.expected_outcome]
        texts = [fold(t) for t in check_values(case, "expected_value", "must_include")]
        present = [term for term in forbidden if any(term in t for t in texts)]
        if present:
            found[case.id] = present

    assert found == {}


def test_it_should_have_the_cited_cpf_own_every_cited_order_outside_other_customer_cases(
    cases, owner_by_order
):
    foreign = []
    for case in cases:
        if "other_customer_data" in case.behaviors:
            continue
        cpfs = cited_cpfs(case)
        for number in kit_orders_cited(case, owner_by_order):
            if cpfs and owner_by_order[number] not in cpfs:
                foreign.append((case.id, number))

    assert foreign == []


def test_it_should_have_the_cited_cpf_not_own_the_cited_order_in_other_customer_cases(
    cases, owner_by_order
):
    owned = []
    for case in with_behavior(cases, "other_customer_data"):
        cpfs = cited_cpfs(case)
        for number in kit_orders_cited(case, owner_by_order):
            if owner_by_order[number] in cpfs:
                owned.append((case.id, number))

    assert owned == []


def test_it_should_cite_a_foreign_kit_order_in_at_least_one_other_customer_case(
    cases, owner_by_order
):
    foreign = other_customer_cases_citing_an_order(cases, owner_by_order)

    assert foreign != []


def test_it_should_only_cite_kit_references_or_declare_them_absent(cases, kit_refs):
    unknown = {
        (case.id, ref)
        for case in cases
        for ref in cited_refs(case) - kit_refs - FAILURE_NUMBERS
        if ref not in {ref_key(absent) for absent in case.absent_refs}
    }

    assert unknown == set()


def test_it_should_keep_every_absent_ref_out_of_the_kit(cases, kit_refs):
    present = {
        (case.id, ref)
        for case in cases
        for ref in case.absent_refs
        if ref_key(ref) in kit_refs
    }

    assert present == set()


def test_it_should_point_every_expected_doc_to_a_kit_policy(cases, policy_names):
    missing = {
        (case.id, doc)
        for case in cases
        for doc in expected_docs(case)
        if doc not in policy_names
    }

    assert missing == set()


def test_it_should_point_every_cites_source_check_to_a_kit_policy(cases, policy_names):
    missing = {
        (case.id, doc)
        for case in cases
        for doc in check_values(case, "cites_source")
        if doc not in policy_names
    }

    assert missing == set()


def test_it_should_expect_the_not_found_text_in_every_foreign_order_case(
    cases, owner_by_order
):
    foreign = other_customer_cases_citing_an_order(cases, owner_by_order)
    not_found = fold("não encontrei")

    without_text = [
        case.id
        for case in foreign
        if not any(not_found in fold(t) for t in check_values(case, "must_include"))
    ]

    assert without_text == []


def test_it_should_expect_the_not_found_text_in_every_nonexistent_order_case(cases):
    missing = with_behavior(cases, "nonexistent_order")
    not_found = fold("não encontrei")

    without_text = [
        case.id
        for case in missing
        if not any(not_found in fold(t) for t in check_values(case, "must_include"))
    ]

    assert without_text == []


def test_it_should_give_every_case_at_least_one_check_or_judge_criterion(cases):
    unguarded = [case.id for case in cases if not (case.checks or case.judge)]

    assert unguarded == []


def test_it_should_not_carry_unknown_fields_on_a_case(cases):
    unknown = {
        (case.id, key)
        for case in cases
        for key in case.model_extra or {}
        if key not in ALLOWED_EXTRA_FIELDS
    }

    assert unknown == set()


def test_it_should_not_carry_unknown_fields_on_a_check(cases):
    unknown = {
        (case.id, key)
        for case in cases
        for check in case.checks
        for key in check.model_extra or {}
        if key not in ALLOWED_EXTRA_FIELDS
    }

    assert unknown == set()


def test_it_should_guard_every_other_customer_case_with_a_leak_check(cases):
    unguarded = [
        case.id
        for case in with_behavior(cases, "other_customer_data")
        if not check_values(case, "no_other_customer_data")
    ]

    assert unguarded == []


def test_it_should_draw_every_leak_check_value_from_a_customer_other_than_the_one_who_identifies(
    cases, order_book
):
    unrelated = {
        (case.id, value)
        for case in with_behavior(cases, "other_customer_data")
        for value in check_values(case, "no_other_customer_data")
        if not any(value in fact for fact in leakable_facts(case, order_book))
    }

    assert unrelated == set()


def test_it_should_forbid_a_forged_outcome_in_every_injection_case(cases):
    unforbidden = [
        case.id
        for case in with_behavior(cases, "injection")
        if not check_values(case, "must_not_include", "no_other_customer_data")
    ]

    assert unforbidden == []


def test_it_should_give_every_multi_turn_case_more_than_one_turn(cases):
    single = [
        case.id for case in with_behavior(cases, "multi_turn") if len(case.turns) < 2
    ]

    assert single == []


def test_it_should_pass_the_structure_validation_for_every_case(cases_dir):
    errors = {
        path.name: eval_cases.validate_structure(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        for path in sorted(cases_dir.glob("*.yaml"))
    }

    assert {name: found for name, found in errors.items() if found} == {}


def test_it_should_record_the_eval_cases_prompt_section_in_prompts(kit_dir):
    text = (kit_dir / "PROMPTS.md").read_text(encoding="utf-8")

    assert re.search(r"^## Casos de eval$", text, re.M)


@pytest.mark.parametrize("term", PROMPT_TERMS)
def test_it_should_include_the_term_in_the_eval_cases_prompt_block(term):
    block = FENCED_BLOCK.search(prompts_section("Casos de eval"))

    assert block is not None and term in block.group(2)
