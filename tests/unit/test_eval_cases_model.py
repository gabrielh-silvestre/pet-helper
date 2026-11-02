import importlib
import sys
from datetime import date

import pytest
import yaml

from pet_helper import evals
from pet_helper.evals import cases as eval_cases
from pet_helper.evals.cases import load_cases, to_sample_kwargs


def write_case(directory, name, **fields):
    case = {"id": name, "today": "2026-11-03", "turns": ["Hello"], **fields}
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(case, allow_unicode=True), encoding="utf-8")
    return path


def full_case(directory):
    write_case(
        directory,
        "full",
        suite="capability",
        rule="RN01",
        turns=["first turn", "second turn"],
        checks=[{"type": "must_include", "value": "7 dias"}],
        expected_doc="troca-e-devolucao.md",
        judge=["tone"],
        behaviors=["rn_calculable"],
        expected_outcome="regret_allowed",
        absent_refs=["PED-99999"],
    )
    return load_cases(directory)[0]


def test_it_should_load_cases_in_file_name_order(tmp_path):
    write_case(tmp_path, "b-second")
    write_case(tmp_path, "a-first")

    cases = load_cases(tmp_path)

    assert [case.id for case in cases] == ["a-first", "b-second"]


def test_it_should_keep_an_extra_field_on_the_case(tmp_path):
    write_case(tmp_path, "extra", note="written by hand")

    (case,) = load_cases(tmp_path)

    assert case.model_extra == {"note": "written by hand"}


def test_it_should_coerce_an_unquoted_numeric_cpf_to_text(tmp_path):
    (tmp_path / "cpf.yaml").write_text(
        "id: cpf\ntoday: 2026-11-03\nturns: [52998224725]\n", encoding="utf-8"
    )

    (case,) = load_cases(tmp_path)

    assert case.turns == ["52998224725"]


def test_it_should_load_a_case_without_suite_and_behaviors(tmp_path):
    write_case(tmp_path, "minimal")

    (case,) = load_cases(tmp_path)

    assert (case.suite, case.behaviors) == (None, [])


def test_it_should_return_only_id_input_and_metadata_as_sample_keys(tmp_path):
    case = full_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert set(sample) == {"id", "input", "metadata"}


def test_it_should_send_one_user_message_per_turn(tmp_path):
    case = full_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert sample["input"] == [
        {"role": "user", "content": "first turn"},
        {"role": "user", "content": "second turn"},
    ]


def test_it_should_copy_exactly_the_case_fields_into_metadata(tmp_path):
    case = full_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert sample["metadata"] == {
        "suite": "capability",
        "rule": "RN01",
        "today": "2026-11-03",
        "checks": [{"type": "must_include", "value": "7 dias"}],
        "expected_doc": "troca-e-devolucao.md",
        "judge": ["tone"],
        "behaviors": ["rn_calculable"],
        "expected_outcome": "regret_allowed",
        "absent_refs": ["PED-99999"],
        "fixture": None,
        "reference": None,
    }


def extended_case(directory):
    write_case(
        directory,
        "extended",
        turns=["first turn", "second turn"],
        fixture="no-cpf-in-session",
        reference={"replies": [{"text": "a", "sources": []}, {"text": "b"}]},
        expected_doc=["frete.md", "troca-e-devolucao.md"],
        judge=["tone", {"criterion": "dont_know_accuracy", "turn": 2}],
        checks=[
            {"type": "must_include", "value": "7 dias", "turn": 1},
            {
                "type": "tool_called",
                "value": "get_order",
                "args": {"order_number": "PED-10003"},
            },
            {"type": "state", "key": "session_cpf", "value": "00000000191"},
        ],
    )
    return load_cases(directory)[0]


def test_it_should_load_turn_args_and_key_as_fields_of_a_check(tmp_path):
    case = extended_case(tmp_path)

    turn_check, args_check, key_check = case.checks

    assert (turn_check.turn, args_check.args, key_check.key) == (
        1,
        {"order_number": "PED-10003"},
        "session_cpf",
    )


def test_it_should_not_leave_turn_args_and_key_as_extras_of_a_check(tmp_path):
    case = extended_case(tmp_path)

    extras = [check.model_extra for check in case.checks]

    assert extras == [{}, {}, {}]


def test_it_should_load_fixture_and_reference_on_the_case(tmp_path):
    case = extended_case(tmp_path)

    assert (case.fixture, case.reference) == (
        "no-cpf-in-session",
        {"replies": [{"text": "a", "sources": []}, {"text": "b"}]},
    )


def test_it_should_load_a_list_of_expected_docs(tmp_path):
    case = extended_case(tmp_path)

    assert case.expected_doc == ["frete.md", "troca-e-devolucao.md"]


def test_it_should_default_fixture_and_reference_to_none(tmp_path):
    write_case(tmp_path, "minimal")

    (case,) = load_cases(tmp_path)

    assert (case.fixture, case.reference) == (None, None)


def test_it_should_accept_a_judge_entry_as_a_name_or_a_criterion_with_a_turn(tmp_path):
    case = extended_case(tmp_path)

    names = [getattr(entry, "criterion", entry) for entry in case.judge]
    turns = [getattr(entry, "turn", None) for entry in case.judge]

    assert (names, turns) == (["tone", "dont_know_accuracy"], [None, 2])


def test_it_should_carry_the_new_check_fields_into_the_sample_metadata(tmp_path):
    case = extended_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert sample["metadata"]["checks"] == [
        {"type": "must_include", "value": "7 dias", "turn": 1},
        {
            "type": "tool_called",
            "value": "get_order",
            "args": {"order_number": "PED-10003"},
        },
        {"type": "state", "key": "session_cpf", "value": "00000000191"},
    ]


def test_it_should_carry_fixture_and_reference_into_the_sample_metadata(tmp_path):
    case = extended_case(tmp_path)

    metadata = to_sample_kwargs(case)["metadata"]

    assert (metadata["fixture"], metadata["reference"]) == (
        "no-cpf-in-session",
        {"replies": [{"text": "a", "sources": []}, {"text": "b"}]},
    )


def test_it_should_carry_a_list_of_expected_docs_into_the_sample_metadata(tmp_path):
    case = extended_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert sample["metadata"]["expected_doc"] == [
        "frete.md",
        "troca-e-devolucao.md",
    ]


def test_it_should_carry_a_judge_entry_with_a_turn_into_the_sample_metadata(tmp_path):
    case = extended_case(tmp_path)

    sample = to_sample_kwargs(case)

    assert sample["metadata"]["judge"] == [
        "tone",
        {"criterion": "dont_know_accuracy", "turn": 2},
    ]


def valid_raw(**overrides):
    raw = {
        "id": "full",
        "suite": "capability",
        "rule": "RN01",
        "today": "2026-11-03",
        "turns": ["first turn", "second turn"],
        "checks": [
            {"type": "cites_source", "value": "troca-e-devolucao.md", "turn": 1},
            {"type": "must_include", "value": ["7 dias"]},
            {"type": "skus_exist"},
            {"type": "mentions_order", "value": "PED-10003"},
            {"type": "no_other_customer_data", "value": ["Zuleica"]},
            {
                "type": "tool_called",
                "value": "get_order",
                "args": {"order_number": "PED-10003"},
            },
            {"type": "tool_not_called", "value": "escalate_to_human"},
            {"type": "escalated", "value": "repeated_failure"},
            {"type": "must_not_include", "value": ["fora do prazo"]},
            {"type": "expected_value", "value": ["dentro do prazo"]},
            {"type": "state", "key": "session_cpf", "value": "00000000191"},
        ],
        "expected_doc": ["troca-e-devolucao.md", "frete.md"],
        "judge": ["tone", {"criterion": "dont_know_accuracy", "turn": 2}],
        "behaviors": ["rn_calculable"],
        "expected_outcome": "regret_allowed",
        "absent_refs": ["PED-99999"],
        "fixture": "no-cpf-in-session",
        "reference": {"replies": [{"text": "a"}, {"text": "b"}]},
    }
    return {**raw, **overrides}


def with_checks(*checks):
    return valid_raw(checks=list(checks))


def test_it_should_accept_every_field_of_the_schema():
    errors = eval_cases.validate_structure(valid_raw())

    assert errors == []


@pytest.mark.parametrize("today", ["2026-11-03", date(2026, 11, 3)])
def test_it_should_accept_today_as_text_or_as_a_date(today):
    errors = eval_cases.validate_structure(valid_raw(today=today))

    assert errors == []


def test_it_should_accept_a_single_string_expected_doc():
    errors = eval_cases.validate_structure(valid_raw(expected_doc="frete.md"))

    assert errors == []


def test_it_should_reject_an_unknown_key_on_a_case():
    errors = eval_cases.validate_structure(valid_raw(note="written by hand"))

    assert errors == ["unknown_key"]


def test_it_should_reject_an_unknown_key_on_a_check():
    errors = eval_cases.validate_structure(
        with_checks({"type": "must_include", "value": ["x"], "trun": 1})
    )

    assert errors == ["unknown_key"]


def test_it_should_reject_an_unknown_check_type():
    errors = eval_cases.validate_structure(
        with_checks({"type": "sounds_nice", "value": "x"})
    )

    assert errors == ["unknown_check_type"]


def test_it_should_reject_a_case_without_turns():
    errors = eval_cases.validate_structure(valid_raw(turns=[]))

    assert errors == ["empty_turns"]


@pytest.mark.parametrize("today", ["2026-13-45", "tomorrow", None])
def test_it_should_reject_a_malformed_today(today):
    errors = eval_cases.validate_structure(valid_raw(today=today))

    assert errors == ["bad_today"]


def test_it_should_reject_a_state_check_on_a_key_outside_the_public_list():
    errors = eval_cases.validate_structure(
        with_checks({"type": "state", "key": "messages", "value": "x"})
    )

    assert errors == ["state_key_not_public"]


@pytest.mark.parametrize(
    "check",
    [
        {"type": "skus_exist"},
        {"type": "no_other_customer_data", "value": ["Zuleica"]},
        {"type": "tool_called", "value": "get_order"},
        {"type": "tool_not_called", "value": "escalate_to_human"},
        {"type": "escalated", "value": "repeated_failure"},
        {"type": "state", "key": "session_cpf", "value": "00000000191"},
    ],
)
def test_it_should_reject_a_turn_on_a_check_that_takes_none(check):
    errors = eval_cases.validate_structure(with_checks({**check, "turn": 1}))

    assert errors == ["turn_not_allowed"]


def test_it_should_reject_an_arg_that_is_not_a_parameter_of_the_tool():
    errors = eval_cases.validate_structure(
        with_checks(
            {
                "type": "tool_called",
                "value": "get_order",
                "args": {"cpf": "00000000191"},
            }
        )
    )

    assert errors == ["unknown_tool_arg"]


@pytest.mark.parametrize("type", ["tool_called", "tool_not_called"])
def test_it_should_reject_a_tool_check_naming_a_tool_that_is_not_registered(type):
    errors = eval_cases.validate_structure(
        with_checks({"type": type, "value": "get_ordr"})
    )

    assert errors == ["unknown_tool"]


@pytest.mark.parametrize("type", ["tool_called", "tool_not_called"])
def test_it_should_accept_a_tool_check_naming_a_registered_tool(type):
    errors = eval_cases.validate_structure(
        with_checks({"type": type, "value": "get_order"})
    )

    assert errors == []


def test_it_should_not_import_inspect_ai_from_the_evals_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "inspect_ai", None)
    for name in [n for n in sys.modules if n.startswith("pet_helper.evals")]:
        monkeypatch.delitem(sys.modules, name)

    module = importlib.import_module("pet_helper.evals")

    assert module is not None


@pytest.mark.parametrize("name", evals.__all__)
def test_it_should_give_access_to_every_name_the_evals_package_exports(name):
    assert hasattr(evals, name)


def test_it_should_import_everything_the_evals_package_exports_with_a_star_import():
    namespace: dict = {}

    exec("from pet_helper.evals import *", namespace)  # noqa: S102

    assert set(evals.__all__) <= set(namespace)


def test_it_should_not_import_inspect_ai(monkeypatch):
    monkeypatch.setitem(sys.modules, "inspect_ai", None)
    monkeypatch.delitem(sys.modules, "pet_helper.evals.cases", raising=False)

    module = importlib.import_module("pet_helper.evals.cases")

    assert module.load_cases is not None


# the judge and value pre-flight


@pytest.mark.parametrize(
    "check_type",
    [
        "cites_source",
        "mentions_order",
        "no_other_customer_data",
        "must_include",
        "must_not_include",
        "expected_value",
    ],
)
def test_it_should_reject_a_check_that_compares_terms_without_a_value(check_type):
    errors = eval_cases.validate_structure(with_checks({"type": check_type}))

    assert "missing_value" in errors


def test_it_should_reject_a_tool_check_without_a_value():
    errors = eval_cases.validate_structure(with_checks({"type": "tool_called"}))

    assert "missing_value" in errors


def test_it_should_reject_a_judge_criterion_the_judge_does_not_know():
    errors = eval_cases.validate_structure(valid_raw(judge=["politeness"]))

    assert errors == ["unknown_judge_criterion"]


def test_it_should_reject_a_judge_turn_beyond_the_turns_of_the_case():
    raw = valid_raw(judge=[{"criterion": "tone", "turn": 3}])

    errors = eval_cases.validate_structure(raw)

    assert errors == ["judge_turn_out_of_range"]


def test_it_should_reject_a_judge_turn_of_zero():
    raw = valid_raw(judge=[{"criterion": "tone", "turn": 0}])

    errors = eval_cases.validate_structure(raw)

    assert errors == ["judge_turn_out_of_range"]


def test_it_should_require_a_turn_for_dont_know_accuracy_in_a_multi_turn_case():
    errors = eval_cases.validate_structure(valid_raw(judge=["dont_know_accuracy"]))

    assert errors == ["judge_turn_required"]


def test_it_should_accept_dont_know_accuracy_without_a_turn_in_a_single_turn_case():
    raw = valid_raw(turns=["only turn"], judge=["dont_know_accuracy"])

    errors = eval_cases.validate_structure(raw)

    assert errors == []
