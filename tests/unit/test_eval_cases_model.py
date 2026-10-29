import importlib
import sys

import yaml

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
    }


def test_it_should_not_import_inspect_ai(monkeypatch):
    monkeypatch.setitem(sys.modules, "inspect_ai", None)
    monkeypatch.delitem(sys.modules, "pet_helper.evals.cases", raising=False)

    module = importlib.import_module("pet_helper.evals.cases")

    assert module.load_cases is not None
