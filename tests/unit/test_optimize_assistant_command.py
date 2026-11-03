"""The `pet-helper optimize assistant <targets>` command (no real model: a fake runner, a DummyLM).

Assumed public surface:

- `pet-helper optimize assistant <targets>`: `<targets>` is the owner's JSON file, case id -> `"prompt"` or
  `"checker"` (see `pet_helper.optimize.assistant.load_prompt_targets`). The command loads the dev cases of
  `environment.cases_dir`, keeps the `prompt` targets, prints `Estimate: US$ <amount>` before any spend,
  stops with a non-zero return when the estimate exceeds the cap (one line on stderr that says `cap`), asks
  `Run? [y/N] `, then runs GEPA from the current `SYSTEM_PROMPT`. A refusal is a line on stderr, a non-zero
  return, no model call and no file written.
- It refuses, before the estimate, when `PET_HELPER_HELD_OUT=1` is in the environment (stderr names the
  variable), when the cases directory is a held-out one, when a target is not a visible dev case, and when
  no case is classified `prompt` (stderr says `no prompt targets`).
- Where the candidate goes: the command writes the candidate prompt as plain text to
  `environment.candidate_path` and never touches `conversation/prompt.py`, so `system_prompt_sha256()` stays
  and the loop-melhoria step (P4) can run the current prompt and the candidate side by side (the
  `system_prompt` seam of `build_assistant`, reached by the eval round flag in `test_eval_cli.py`) before
  the owner decides. Applying an accepted candidate to `prompt.py` is a one-time text edit with no code
  behind it. When GEPA finds nothing better than the current prompt the command returns 0, writes no file
  and says `No improvement` on stdout.
- `pet_helper.optimize.command.AssistantEnvironment(cases_dir, candidate_path, reflection_lm, runner,
  cap_usd=Decimal(5), max_metric_calls=300)`; `runner(case, system_prompt) -> RunRecord`.
- `pet_helper.cli.main(argv, *, assistant_environment=None)`.
"""

import re
import sys
from decimal import Decimal

import pytest

from pet_helper.cli import main
from pet_helper.conversation import system_prompt_sha256
from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.evals.budget import RoundBudget
from tests.unit.optimize_support import (
    GOOD_PROMPT,
    ChargingRunner,
    PromptSensitiveRunner,
    make_cases,
    priced_reflection_lm,
    reflection_lm,
    write_cases,
)
from tests.unit.test_cli import _set_input_lines

TINY_CAP = Decimal("0.000001")
TARGETS = '{"case-00": "prompt", "case-01": "prompt", "case-02": "checker"}'


@pytest.fixture(autouse=True)
def _optimize_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PET_HELPER_HELD_OUT", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def cases_dir(tmp_path):
    write_cases(tmp_path / "cases", make_cases(20))
    return tmp_path / "cases"


@pytest.fixture
def targets(tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(TARGETS, encoding="utf-8")
    return path


@pytest.fixture
def runner():
    return PromptSensitiveRunner()


@pytest.fixture
def candidate_path(tmp_path):
    return tmp_path / "candidate.txt"


def optimize_assistant(
    monkeypatch,
    targets,
    *,
    cases_dir,
    candidate_path,
    runner,
    lm=None,
    cap=Decimal(5),
    answers=("y",),
    max_metric_calls=20,
    budget=None,
):
    pytest.importorskip("dspy")
    from pet_helper.optimize.command import AssistantEnvironment

    _set_input_lines(monkeypatch, answers)
    environment = AssistantEnvironment(
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        reflection_lm=lm or reflection_lm(),
        runner=runner,
        cap_usd=cap,
        max_metric_calls=max_metric_calls,
        **({"budget": budget} if budget else {}),
    )
    return main(
        ["optimize", "assistant", str(targets)], assistant_environment=environment
    )


# without the extra


def test_it_should_tell_how_to_install_the_extra_when_dspy_is_missing(
    monkeypatch, capsys, targets
):
    monkeypatch.setitem(sys.modules, "dspy", None)
    for name in [n for n in sys.modules if n.startswith("pet_helper.optimize")]:
        monkeypatch.delitem(sys.modules, name)

    code = main(["optimize", "assistant", str(targets)])

    assert code != 0
    assert "uv sync --extra optimize" in capsys.readouterr().err


# the defaults


def test_it_should_default_to_the_five_dollar_cap_and_three_hundred_metric_calls(
    tmp_path,
):
    pytest.importorskip("dspy")
    from pet_helper.optimize.command import AssistantEnvironment

    environment = AssistantEnvironment(
        cases_dir=tmp_path,
        candidate_path=tmp_path / "c.txt",
        reflection_lm=None,
        runner=PromptSensitiveRunner(),
    )

    assert (environment.cap_usd, environment.max_metric_calls) == (Decimal(5), 300)


# refusals before any spend


def test_it_should_refuse_when_no_case_is_classified_as_prompt(
    monkeypatch, capsys, tmp_path, cases_dir, candidate_path, runner
):
    only_checker = tmp_path / "only_checker.json"
    only_checker.write_text('{"case-00": "checker"}', encoding="utf-8")

    code = optimize_assistant(
        monkeypatch,
        only_checker,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert code != 0
    assert "no prompt targets" in capsys.readouterr().err


def test_it_should_call_no_model_and_write_nothing_when_there_is_no_prompt_target(
    monkeypatch, tmp_path, cases_dir, candidate_path, runner
):
    only_checker = tmp_path / "only_checker.json"
    only_checker.write_text('{"case-00": "checker"}', encoding="utf-8")

    optimize_assistant(
        monkeypatch,
        only_checker,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert runner.calls == []
    assert not candidate_path.exists()


def test_it_should_refuse_a_target_that_is_not_a_visible_dev_case(
    monkeypatch, capsys, tmp_path, cases_dir, candidate_path, runner
):
    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"held-case-x": "prompt"}', encoding="utf-8")

    code = optimize_assistant(
        monkeypatch,
        unknown,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert code != 0
    assert "held-case-x" in capsys.readouterr().err
    assert runner.calls == []


def test_it_should_refuse_when_the_held_out_variable_is_set(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    monkeypatch.setenv("PET_HELPER_HELD_OUT", "1")

    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert code != 0
    assert "PET_HELPER_HELD_OUT" in capsys.readouterr().err
    assert runner.calls == []


def test_it_should_refuse_a_held_out_cases_directory(
    monkeypatch, tmp_path, targets, candidate_path, runner
):
    write_cases(tmp_path / "held_out" / "cases", make_cases(20))

    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=tmp_path / "held_out" / "cases",
        candidate_path=candidate_path,
        runner=runner,
    )

    assert code != 0
    assert runner.calls == []


# the estimate and the confirmation


def test_it_should_stop_before_spending_when_the_estimate_exceeds_the_cap(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        cap=TINY_CAP,
    )

    assert code != 0
    assert "cap" in capsys.readouterr().err
    assert runner.calls == []


def test_it_should_show_the_estimate_even_when_it_exceeds_the_cap(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        cap=TINY_CAP,
    )

    assert "Estimate: US$" in capsys.readouterr().out


def test_it_should_show_the_estimate_before_asking_to_run(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        answers=("n",),
    )

    assert "Estimate: US$" in capsys.readouterr().out


def test_it_should_call_no_model_and_write_nothing_when_the_confirmation_is_declined(
    monkeypatch, targets, cases_dir, candidate_path, runner
):
    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        answers=("n",),
    )

    assert runner.calls == []
    assert not candidate_path.exists()


# the run


def test_it_should_write_the_candidate_prompt_as_text_when_it_beats_the_current_one(
    monkeypatch, targets, cases_dir, candidate_path, runner
):
    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert code == 0
    assert candidate_path.read_text(encoding="utf-8") == GOOD_PROMPT


def test_it_should_start_from_the_current_system_prompt(
    monkeypatch, targets, cases_dir, candidate_path, runner
):
    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert runner.calls[0][1] == SYSTEM_PROMPT


def test_it_should_leave_the_production_prompt_unchanged(
    monkeypatch, targets, cases_dir, candidate_path, runner
):
    before = system_prompt_sha256()

    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
    )

    assert system_prompt_sha256() == before
    assert SYSTEM_PROMPT != GOOD_PROMPT


def test_it_should_write_no_file_and_return_zero_when_nothing_beats_the_current_prompt(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        lm=reflection_lm("segue sem a palavra certa"),
    )

    assert code == 0
    assert not candidate_path.exists()
    assert "No improvement" in capsys.readouterr().out


# the cap at run time: rollouts and reflection charge one budget, and the command stops at the cap

RUNTIME_CALLS = 30
ROLLOUT_COST = Decimal("0.5")
REFLECTION_COST_ABOVE_CAP = Decimal(6)
# a proposal that never scores higher, so without the cap the reflection would go on for many rounds
NO_GAIN = "segue sem a palavra certa"


def test_it_should_stop_the_rollouts_once_their_cost_reaches_the_cap(
    monkeypatch, targets, cases_dir, candidate_path
):
    budget = RoundBudget(Decimal(5))
    runner = ChargingRunner(budget, ROLLOUT_COST)

    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        max_metric_calls=RUNTIME_CALLS,
        budget=budget,
    )

    assert 1 <= len(runner.calls) <= 10


def test_it_should_exit_non_zero_when_the_rollouts_reach_the_cap(
    monkeypatch, targets, cases_dir, candidate_path
):
    budget = RoundBudget(Decimal(5))

    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=ChargingRunner(budget, ROLLOUT_COST),
        max_metric_calls=RUNTIME_CALLS,
        budget=budget,
    )

    assert code != 0


def test_it_should_write_no_candidate_when_the_cap_stops_a_run_that_found_nothing_better(
    monkeypatch, targets, cases_dir, candidate_path
):
    budget = RoundBudget(Decimal(5))

    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=ChargingRunner(budget, ROLLOUT_COST),
        max_metric_calls=RUNTIME_CALLS,
        budget=budget,
    )

    assert not candidate_path.exists()


def test_it_should_report_the_cap_and_the_spend_so_far_on_stderr(
    monkeypatch, capsys, targets, cases_dir, candidate_path
):
    budget = RoundBudget(Decimal(5))

    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=ChargingRunner(budget, ROLLOUT_COST),
        max_metric_calls=RUNTIME_CALLS,
        budget=budget,
    )

    stderr = capsys.readouterr().err
    assert "cap" in stderr
    assert re.search(r"spent US\$ 5(\.0+)?\b", stderr)


def test_it_should_stop_calling_the_reflection_model_once_its_cost_passes_the_cap(
    monkeypatch, targets, cases_dir, candidate_path, runner
):
    lm = priced_reflection_lm(REFLECTION_COST_ABOVE_CAP, NO_GAIN)

    optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        lm=lm,
        max_metric_calls=RUNTIME_CALLS,
    )

    assert 1 <= lm.calls <= 2


def test_it_should_exit_non_zero_and_write_no_candidate_when_the_reflection_passes_the_cap_with_nothing_better(
    monkeypatch, capsys, targets, cases_dir, candidate_path, runner
):
    code = optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        lm=priced_reflection_lm(REFLECTION_COST_ABOVE_CAP, NO_GAIN),
        max_metric_calls=RUNTIME_CALLS,
    )

    assert code != 0
    assert not candidate_path.exists()
    assert "spent US$" in capsys.readouterr().err


# a cap that stops a run which already found a better prompt

# GEPA scores the proposal on a minibatch of 3 and then on the 10 validation cases
GOOD_CALLS_TO_VALIDATE = 3 + 10


class _CapAfterImprovementRunner(ChargingRunner):
    """Charges the whole cap on the rollout that finishes validating the improved prompt."""

    def __call__(self, case, system_prompt):
        record = super().__call__(case, system_prompt)
        good_calls = sum(GOOD_PROMPT in prompt for _, prompt in self.calls)
        if good_calls == GOOD_CALLS_TO_VALIDATE and GOOD_PROMPT in system_prompt:
            self.budget.on_cost(self.budget.cap)
        return record


def _stopped_by_the_cap_after_an_improvement(
    monkeypatch, targets, cases_dir, candidate_path
) -> int:
    budget = RoundBudget(Decimal(5))
    runner = _CapAfterImprovementRunner(
        budget, Decimal("0.001"), reserve=Decimal("0.001")
    )
    return optimize_assistant(
        monkeypatch,
        targets,
        cases_dir=cases_dir,
        candidate_path=candidate_path,
        runner=runner,
        max_metric_calls=RUNTIME_CALLS,
        budget=budget,
    )


def _partial_candidates(directory) -> list:
    return [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and GOOD_PROMPT in (text := path.read_text(encoding="utf-8", errors="ignore"))
        and "partial" in text.lower()
    ]


def test_it_should_exit_non_zero_when_the_cap_stops_a_run_that_found_a_better_prompt(
    monkeypatch, targets, cases_dir, candidate_path
):
    code = _stopped_by_the_cap_after_an_improvement(
        monkeypatch, targets, cases_dir, candidate_path
    )

    assert code != 0


def test_it_should_write_the_best_prompt_so_far_marked_partial_when_the_cap_stops_the_run(
    monkeypatch, targets, cases_dir, candidate_path
):
    _stopped_by_the_cap_after_an_improvement(
        monkeypatch, targets, cases_dir, candidate_path
    )

    assert _partial_candidates(candidate_path.parent) != []


def test_it_should_warn_on_stderr_that_the_written_prompt_is_partial_when_the_cap_stops_the_run(
    monkeypatch, capsys, targets, cases_dir, candidate_path
):
    _stopped_by_the_cap_after_an_improvement(
        monkeypatch, targets, cases_dir, candidate_path
    )

    stderr = capsys.readouterr().err.lower()
    assert "partial" in stderr
    assert "cap" in stderr
