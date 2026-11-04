"""The `pet-helper optimize judge` command (no real model: a `dspy` DummyLM, no network).

Assumed public surface:

- `pet-helper optimize judge <round_dir>`: reads the labelled dev items of a saved round
  (`pet_helper.optimize.judge.load_dev_items`), prints `Estimate: US$ <amount>` before any spend, stops with
  a non-zero return when the estimate exceeds the cap of the command (one line on stderr that says `cap`),
  asks `Run? [y/N] ` (the same `ask_confirmation` as `pet-helper eval`), then chooses the few-shot examples
  of each criterion from the dev items only and materializes them in the judge file. A refusal is one or
  more lines on stderr, a non-zero return, no model call and no file written. A declined confirmation calls
  no model and writes no file. The test split is never read nor sent to the model.
- `pet_helper.cli.main(argv, *, eval_environment=None, optimize_environment=None)`.
- `pet_helper.optimize.command.OptimizeEnvironment(lm, judge_path, cap_usd=Decimal(5), validation=None)`:
  `lm` is the `dspy` LM the command runs on, `judge_path` the judge source it materializes into, `cap_usd`
  the per-command cap, `validation` the test-split run `validation(round_dir, *, force_new_validation) -> int`
  (default: the existing `labels judge-pool --split test` code on the candidate; a non-zero return is a
  refusal or a stop of that code).
- The accept flow after the first confirmation: (a) the round must already hold the baseline of the current
  judge, i.e. a test-split line of `labels/judge-runs.jsonl` with the current `judge_prompt_sha256()`, else
  the command refuses (stderr names the `baseline`) before any estimate, model call or file write; (b) the
  candidate is chosen on dev and materialized, then a second `Run? [y/N] ` guards the test-split run, which
  is made once with `force_new_validation=True` (that literal appears only in `optimize/accept.py`); (c) the
  baseline verdicts are read from `labels/key.json` before that run, the candidate ones after it; per
  criterion the rates (`accept.rates_from_pairs` over the test items, human label vs verdict) go through
  `accept.compare` and `accept.format_report` (printed on stdout, one per criterion); no Wilson floor is
  read or required (waived by the owner, 2026-11-03); (d) the candidate stays in `judge_path` only when no
  criterion is `worse` and at least one is accepted; otherwise the original bytes are restored. They are
  restored too when the second confirmation is declined or the test run returns non-zero.
- Review fixes (2026-11-03): `labels/key.json` is restored byte for byte whenever the candidate does not
  stay; the command refuses before any spend when a test item of `key.json` carries another judge hash
  than the current one, and when `judge-runs.jsonl` already holds a forced test read (one candidate per
  unit); before the second `Run? [y/N] ` it prints the estimate of the test read (`<n> items`, the
  typical and the worst case as `labels.estimate_judge_calls(n)` gives them, 4 decimals) and refuses with
  a message that says `cap` when the worst case passes `cap_usd`; a candidate whose bytes equal the
  current judge ends the command before the second confirmation; a `BudgetExceeded` or `dspy` error while
  choosing the candidate is one message on stderr and a non-zero return, the judge restored.
- Without the `optimize` extra (`import dspy` fails) the command prints one clear message on stderr that
  names the install command `uv sync --extra optimize`, prints nothing on stdout and returns non-zero.
  The `optimize` modules are imported lazily, only by this command, so `pet-helper chat` and `eval` still load.
"""

import json
import subprocess
import sys
from decimal import Decimal

import pytest

from pet_helper.cli import main
from pet_helper.evals import judge as judge_module
from pet_helper.evals import labels as labels_module
from tests.unit.optimize_support import (
    TEST_MARKER,
    FakeValidation,
    build_labelled_round,
    copy_judge_source,
    load_judge_copy,
    record_test_run,
)
from tests.unit.test_cli import _set_input_lines

TINY_CAP = Decimal("0.000001")


@pytest.fixture(autouse=True)
def _optimize_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PET_HELPER_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("PET_HELPER_HELD_OUT", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def labelled(tmp_path):
    return build_labelled_round(tmp_path / "rounds")


@pytest.fixture
def judge_path(tmp_path):
    return copy_judge_source(tmp_path / "judge")


@pytest.fixture
def lm(labelled):
    pytest.importorskip("dspy")
    from dspy.utils import DummyLM

    return DummyLM(
        {
            text: {"critique": "análise curta", "verdict": label}
            for text, label in labelled.dev.items()
        }
    )


@pytest.fixture
def validation(labelled, judge_path):
    return FakeValidation(labelled, judge_path)


def optimize_judge(
    monkeypatch,
    round_dir,
    lm,
    judge_path,
    *,
    cap=Decimal(5),
    answers=("y", "y"),
    validation=None,
):
    from pet_helper.optimize.command import OptimizeEnvironment

    _set_input_lines(monkeypatch, answers)
    environment = OptimizeEnvironment(
        lm=lm, judge_path=judge_path, cap_usd=cap, validation=validation
    )
    return main(["optimize", "judge", str(round_dir)], optimize_environment=environment)


# without the extra


def test_it_should_tell_how_to_install_the_extra_when_dspy_is_missing(
    monkeypatch, capsys, labelled
):
    monkeypatch.setitem(sys.modules, "dspy", None)
    for name in [n for n in sys.modules if n.startswith("pet_helper.optimize")]:
        monkeypatch.delitem(sys.modules, name)

    code = main(["optimize", "judge", str(labelled.directory)])

    assert code != 0
    assert "uv sync --extra optimize" in capsys.readouterr().err


def test_it_should_print_nothing_on_stdout_when_dspy_is_missing(
    monkeypatch, capsys, labelled
):
    monkeypatch.setitem(sys.modules, "dspy", None)
    for name in [n for n in sys.modules if n.startswith("pet_helper.optimize")]:
        monkeypatch.delitem(sys.modules, name)

    main(["optimize", "judge", str(labelled.directory)])

    assert capsys.readouterr().out == ""


# refusals


def test_it_should_refuse_a_pool_with_unlabelled_items_and_name_them(
    monkeypatch, capsys, tmp_path, lm, judge_path
):
    partial = build_labelled_round(tmp_path / "partial", unlabelled=2)

    code = optimize_judge(monkeypatch, partial.directory, lm, judge_path)

    stderr = capsys.readouterr().err
    assert code != 0
    assert all(i in stderr for i in partial.unlabelled_ids)


def test_it_should_call_no_model_for_a_pool_with_unlabelled_items(
    monkeypatch, tmp_path, lm, judge_path
):
    partial = build_labelled_round(tmp_path / "partial", unlabelled=2)

    optimize_judge(monkeypatch, partial.directory, lm, judge_path)

    assert lm.history == []


def test_it_should_refuse_a_held_out_round(monkeypatch, tmp_path, lm, judge_path):
    held_out = build_labelled_round(tmp_path / "held_out")

    code = optimize_judge(monkeypatch, held_out.directory, lm, judge_path)

    assert code != 0
    assert lm.history == []


def test_it_should_stop_before_spending_when_the_estimate_exceeds_the_cap(
    monkeypatch, capsys, labelled, lm, judge_path
):
    code = optimize_judge(monkeypatch, labelled.directory, lm, judge_path, cap=TINY_CAP)

    assert code != 0
    assert "cap" in capsys.readouterr().err
    assert lm.history == []


def test_it_should_show_the_estimate_even_when_it_exceeds_the_cap(
    monkeypatch, capsys, labelled, lm, judge_path
):
    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, cap=TINY_CAP)

    assert "Estimate: US$" in capsys.readouterr().out


# the confirmation


def test_it_should_show_the_estimate_before_asking_to_run(
    monkeypatch, capsys, labelled, lm, judge_path
):
    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, answers=("n",))

    assert "Estimate: US$" in capsys.readouterr().out


def test_it_should_call_no_model_when_the_confirmation_is_declined(
    monkeypatch, labelled, lm, judge_path
):
    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, answers=("n",))

    assert lm.history == []


def test_it_should_leave_the_judge_file_untouched_when_the_confirmation_is_declined(
    monkeypatch, labelled, lm, judge_path
):
    before = judge_path.read_bytes()

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, answers=("n",))

    assert judge_path.read_bytes() == before


# the confirmed run


def test_it_should_succeed_on_a_confirmed_run(
    monkeypatch, labelled, lm, judge_path, validation
):
    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code == 0


def test_it_should_run_the_bootstrap_on_the_model_once_confirmed(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert lm.history


def test_it_should_never_send_a_test_split_slice_to_the_model(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert TEST_MARKER not in json.dumps(lm.history, default=str)


def test_it_should_materialize_both_dont_know_dev_items_as_examples(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    shots = load_judge_copy(judge_path, monkeypatch).FEW_SHOT_EXAMPLES
    assert {
        (e.slice, e.verdict) for e in shots if e.criterion == "dont_know_accuracy"
    } == set(labelled.dev_by_criterion["dont_know_accuracy"].items())


def test_it_should_materialize_escalation_examples_from_the_dev_items_only(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    shots = load_judge_copy(judge_path, monkeypatch).FEW_SHOT_EXAMPLES
    escalation = {e.slice for e in shots if e.criterion == "escalation_judgment"}
    assert escalation
    assert escalation <= set(labelled.dev_by_criterion["escalation_judgment"])


def test_it_should_materialize_no_tone_example(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    shots = load_judge_copy(judge_path, monkeypatch).FEW_SHOT_EXAMPLES
    assert not [e for e in shots if e.criterion == "tone"]


# the baseline of the current judge


@pytest.fixture
def bare(tmp_path, judge_path):
    return build_labelled_round(tmp_path / "bare", baseline=False)


def test_it_should_refuse_a_round_without_the_baseline_of_the_current_judge(
    monkeypatch, capsys, bare, lm, judge_path
):
    validation = FakeValidation(bare, judge_path)

    code = optimize_judge(
        monkeypatch, bare.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert "baseline" in capsys.readouterr().err


def test_it_should_spend_and_write_nothing_when_the_baseline_is_missing(
    monkeypatch, bare, lm, judge_path
):
    validation = FakeValidation(bare, judge_path)
    before = judge_path.read_bytes()

    optimize_judge(monkeypatch, bare.directory, lm, judge_path, validation=validation)

    assert (lm.history, validation.calls, judge_path.read_bytes()) == ([], [], before)


def test_it_should_refuse_a_baseline_made_by_another_judge(
    monkeypatch, capsys, tmp_path, lm, judge_path
):
    stale = build_labelled_round(tmp_path / "stale", baseline_sha256="another-judge")
    validation = FakeValidation(stale, judge_path)

    code = optimize_judge(
        monkeypatch, stale.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert "baseline" in capsys.readouterr().err


# the test-split run


def test_it_should_ask_again_before_reading_the_test_split(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch,
        labelled.directory,
        lm,
        judge_path,
        validation=validation,
        answers=("y", "n"),
    )

    assert validation.calls == []


def test_it_should_restore_the_judge_file_when_the_second_confirmation_is_declined(
    monkeypatch, labelled, lm, judge_path, validation
):
    before = judge_path.read_bytes()

    optimize_judge(
        monkeypatch,
        labelled.directory,
        lm,
        judge_path,
        validation=validation,
        answers=("y", "n"),
    )

    assert judge_path.read_bytes() == before


def test_it_should_run_the_test_split_once_with_the_force_flag(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert [c["force_new_validation"] for c in validation.calls] == [True]


def test_it_should_run_the_test_split_on_the_materialized_candidate(
    monkeypatch, labelled, lm, judge_path, validation
):
    before = judge_path.read_bytes()

    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert validation.calls[0]["judge_source"] != before


def test_it_should_not_need_a_wilson_floor_file(
    monkeypatch, labelled, lm, judge_path, validation
):
    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code == 0
    assert not (labelled.directory / "labels" / "floor.json").exists()


def test_it_should_restore_the_judge_file_and_fail_when_the_test_run_is_refused(
    monkeypatch, labelled, lm, judge_path
):
    refused = FakeValidation(labelled, judge_path, code=1)
    before = judge_path.read_bytes()

    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=refused
    )

    assert code != 0
    assert judge_path.read_bytes() == before


# the decision


def test_it_should_keep_a_candidate_as_good_as_the_current_judge(
    monkeypatch, labelled, lm, judge_path, validation
):
    before = judge_path.read_bytes()

    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert judge_path.read_bytes() != before


def test_it_should_restore_the_original_bytes_of_a_candidate_that_is_worse(
    monkeypatch, labelled, lm, judge_path
):
    worse = FakeValidation(labelled, judge_path, mistakes=("esc-test-0",))
    before = judge_path.read_bytes()

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=worse)

    assert judge_path.read_bytes() == before


def test_it_should_say_reject_for_a_candidate_that_is_worse(
    monkeypatch, capsys, labelled, lm, judge_path
):
    worse = FakeValidation(labelled, judge_path, mistakes=("esc-test-0",))

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=worse)

    assert "reject" in capsys.readouterr().out


def test_it_should_restore_the_original_bytes_when_no_metric_was_measured(
    monkeypatch, labelled, lm, judge_path
):
    unable = FakeValidation(labelled, judge_path, unable=True)
    before = judge_path.read_bytes()

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=unable)

    assert judge_path.read_bytes() == before


def test_it_should_say_keep_when_no_metric_was_measured(
    monkeypatch, capsys, labelled, lm, judge_path
):
    unable = FakeValidation(labelled, judge_path, unable=True)

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=unable)

    assert "keep" in capsys.readouterr().out


def test_it_should_report_counts_and_wilson_for_each_criterion(
    monkeypatch, capsys, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    out = capsys.readouterr().out
    assert all(c in out for c in ("tone", "escalation_judgment", "dont_know_accuracy"))
    assert "wilson95" in out


# the key.json of the round after a candidate


def _key_bytes(labelled) -> bytes:
    return (labelled.directory / "labels" / "key.json").read_bytes()


def test_it_should_restore_key_json_when_the_candidate_is_rejected(
    monkeypatch, labelled, lm, judge_path
):
    worse = FakeValidation(labelled, judge_path, mistakes=("esc-test-0",))
    before = _key_bytes(labelled)

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=worse)

    assert _key_bytes(labelled) == before


def test_it_should_restore_key_json_when_the_test_run_fails_after_a_partial_write(
    monkeypatch, labelled, lm, judge_path
):
    def partial_then_fail(round_dir, *, force_new_validation):
        first = next(iter(labelled.test_labels))
        record_test_run(round_dir, {first: "fail"}, "candidate", forced=True)
        return 1

    before = _key_bytes(labelled)

    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=partial_then_fail
    )

    assert _key_bytes(labelled) == before


def test_it_should_restore_key_json_when_the_candidate_answers_unable_everywhere(
    monkeypatch, labelled, lm, judge_path
):
    unable = FakeValidation(labelled, judge_path, unable=True)
    before = _key_bytes(labelled)

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=unable)

    assert _key_bytes(labelled) == before


def test_it_should_keep_the_candidate_verdicts_in_key_json_when_it_is_accepted(
    monkeypatch, labelled, lm, judge_path, validation
):
    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    key = json.loads(_key_bytes(labelled))
    assert {key[i]["judge_prompt_sha256"] for i in labelled.test_labels} == {
        "candidate"
    }


def test_it_should_compare_a_second_equally_worse_candidate_with_the_real_baseline(
    monkeypatch, capsys, labelled, lm, judge_path
):
    worse = FakeValidation(labelled, judge_path, mistakes=("esc-test-0",))
    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=worse)
    capsys.readouterr()
    # the forced-read refusal would stop a second run: keep only the baseline line to reach the comparison
    runs = labelled.directory / "labels" / "judge-runs.jsonl"
    runs.write_text(runs.read_text(encoding="utf-8").splitlines()[0] + "\n")

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path, validation=worse)

    out = capsys.readouterr().out
    assert "TPR current 2/2" in out
    assert "reject" in out


def test_it_should_refuse_before_spending_when_a_test_item_was_judged_by_another_judge(
    monkeypatch, labelled, lm, judge_path, validation
):
    key = json.loads(_key_bytes(labelled))
    for item_id in labelled.test_labels:
        key[item_id]["judge_prompt_sha256"] = "another-judge"
    (labelled.directory / "labels" / "key.json").write_text(json.dumps(key))

    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert (lm.history, validation.calls) == ([], [])


def test_it_should_refuse_before_spending_when_a_candidate_already_read_the_test_split(
    monkeypatch, labelled, lm, judge_path, validation
):
    record_test_run(
        labelled.directory,
        dict(labelled.test_labels),
        judge_module.judge_prompt_sha256(),
        forced=True,
    )

    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert (lm.history, validation.calls) == ([], [])


# the estimate of the test-split read


def _price_the_test_read(monkeypatch) -> tuple[str, str]:
    monkeypatch.setattr(labels_module, "JUDGE_CALL_RESERVE_USD", Decimal("0.1"))
    count = 8
    typical = labels_module.JUDGE_CALL_ESTIMATE_USD * count
    return f"{typical:.4f}", f"{Decimal('0.1') * count:.4f}"


def test_it_should_print_the_test_split_estimate_before_the_second_confirmation(
    monkeypatch, capsys, labelled, lm, judge_path, validation
):
    typical, worst = _price_the_test_read(monkeypatch)

    optimize_judge(
        monkeypatch,
        labelled.directory,
        lm,
        judge_path,
        validation=validation,
        answers=("y", "n"),
    )

    out = capsys.readouterr().out
    assert f"{len(labelled.test_labels)} items" in out
    assert typical in out
    assert worst in out


def test_it_should_refuse_a_test_read_whose_worst_case_passes_the_cap(
    monkeypatch, capsys, labelled, lm, judge_path, validation
):
    monkeypatch.setattr(labels_module, "JUDGE_CALL_RESERVE_USD", Decimal(1))

    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert "cap" in capsys.readouterr().err
    assert validation.calls == []


def test_it_should_pass_the_force_flag_to_the_judge_pool_process(
    monkeypatch, labelled, lm, judge_path
):
    from pet_helper.optimize import command

    commands: list[list[str]] = []

    def fake_run(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(command.subprocess, "run", fake_run)

    optimize_judge(monkeypatch, labelled.directory, lm, judge_path)

    assert any("--force-new-validation" in argv for argv in commands)


# a candidate that changes nothing


def test_it_should_not_read_the_test_split_when_the_candidate_equals_the_current_judge(
    monkeypatch, labelled, lm, judge_path, validation
):
    from pet_helper.optimize import command

    monkeypatch.setattr(command, "select_examples", lambda *args, **kwargs: [])

    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert validation.calls == []


# a failure while choosing the candidate


def _failure(kind: str) -> Exception:
    from dspy.utils.exceptions import DSPyError

    from pet_helper.trace import BudgetExceeded

    return {
        "budget": BudgetExceeded("The cap of US$ 5 stopped the bootstrap"),
        "dspy": DSPyError("the adapter could not parse the answer"),
    }[kind]


@pytest.mark.parametrize("kind", ["budget", "dspy"])
def test_it_should_report_a_failure_while_choosing_the_candidate_on_stderr(
    monkeypatch, capsys, labelled, lm, judge_path, validation, kind
):
    from pet_helper.optimize import command

    failure = _failure(kind)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(command, "select_examples", fail)

    code = optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert code != 0
    assert str(failure) in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["budget", "dspy"])
def test_it_should_restore_the_judge_file_after_a_failure_while_choosing_the_candidate(
    monkeypatch, labelled, lm, judge_path, validation, kind
):
    from pet_helper.optimize import command

    failure = _failure(kind)
    before = judge_path.read_bytes()

    def write_then_fail(examples, path):
        path.write_text("# half-written candidate\n", encoding="utf-8")
        raise failure

    monkeypatch.setattr(command, "materialize_few_shots", write_then_fail)

    optimize_judge(
        monkeypatch, labelled.directory, lm, judge_path, validation=validation
    )

    assert judge_path.read_bytes() == before
