import re
from collections import Counter

import pytest

from pet_helper.evals.cases import load_cases
from pet_helper.orders.api import FAILURE_503_ORDER, FAILURE_TIMEOUT_ORDER
from pet_helper.orders.cpf import normalize_cpf
from tests.kit.helpers import OUTCOME_TERMS, fold, prompts_section

pytestmark = pytest.mark.integration

MIN_CASES = 20
CASE_BUDGET = 31
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


def check_values(case, *types):
    """Every text value of the case's checks of the given types (all types when none)."""
    values = []
    for check in case.checks:
        if types and check.type not in types:
            continue
        raw = check.value
        values.extend(raw if isinstance(raw, list) else [] if raw is None else [raw])
    return values


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


def test_it_should_ship_the_budgeted_number_of_cases(cases):
    assert len(cases) == CASE_BUDGET


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
        for criterion in case.judge
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
    assert INVENTED_POLICIES & {case.expected_doc for case in cases}


@pytest.mark.parametrize("policy", sorted(INVENTED_POLICIES))
def test_it_should_point_a_case_at_each_invented_policy(cases, policy):
    assert policy in {case.expected_doc for case in cases}


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
    missing = [
        case.id
        for case in cases
        if case.expected_doc and case.expected_doc not in policy_names
    ]

    assert missing == []


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


def test_it_should_record_the_eval_cases_prompt_section_in_prompts(kit_dir):
    text = (kit_dir / "PROMPTS.md").read_text(encoding="utf-8")

    assert re.search(r"^## Casos de eval$", text, re.M)


@pytest.mark.parametrize("term", PROMPT_TERMS)
def test_it_should_include_the_term_in_the_eval_cases_prompt_block(term):
    block = FENCED_BLOCK.search(prompts_section("Casos de eval"))

    assert block is not None and term in block.group(2)
