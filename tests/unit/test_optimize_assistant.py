"""The assistant prompt optimizer (no real model: fakes, a `dspy` DummyLM, no network).

Assumed public surface of `pet_helper.optimize.assistant` (needs the `optimize` extra):

- `load_dev_cases(cases_dir) -> list[EvalCase]`: the visible dev cases of a directory, sorted by id. A path
  with a `held_out` part is refused with `OptimizeRefused` (message names `held-out`), before any read.
- `load_prompt_targets(targets_path, cases) -> list[str]`: the targets file is JSON, an object that maps a
  case id to the owner's classification, `"prompt"` or `"checker"`. Returns the ids classified `prompt`,
  sorted. Refused with `OptimizeRefused`: an id that is not among `cases` (held-out ids land here; the
  message names the id), a classification that is neither value (the message names it), no `prompt` id
  (the message says `no prompt targets`).
- `split_cases(cases, targets) -> (train, val)`: deterministic and independent of the input order (sorted
  by id first), disjoint, half and half (20 cases -> 10 and 10), the targets divided equally between sides.
- `score_case(case, record, known_skus=()) -> dspy.Prediction` with `score` (1.0 when every deterministic
  check passes, else 0.0) and `feedback` (the failed check ids of `evaluate_case`; never customer text).
  It never calls the judge: `case.judge` is ignored.
- `run_case(case, system_prompt, *, gateway, kit_dir=None) -> RunRecord`: runs the turns of the case on an
  assistant built with that prompt (`build_assistant(system_prompt=...)`).
- `AssistantProgram(system_prompt, runner)`: a thin `dspy.Module` with one predictor whose instructions are
  the prompt; `forward(case)` calls `runner(case, current_instructions)` and returns
  `dspy.Prediction(record=...)`. `runner` is `Callable[[EvalCase, str], RunRecord]`.
- `optimize_prompt(seed_prompt, train, val, *, runner, reflection_lm, max_metric_calls) -> str`: GEPA
  (reflection minibatch of 3) over `AssistantProgram`; returns the best prompt, the seed when nothing beats it.
- `estimate_usd(max_metric_calls) -> Decimal`: positive, grows with the number of metric calls.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("dspy")

from langchain_core.messages import SystemMessage

from pet_helper.conversation import Reply
from pet_helper.evals.budget import JUDGE_CALL_RESERVE_USD, RoundBudget
from pet_helper.evals.cases import Check
from pet_helper.evals.checks import RunRecord, ToolCall
from pet_helper.gateway import FakeGateway
from pet_helper.optimize import OptimizeRefused
from pet_helper.optimize.assistant import (
    AssistantProgram,
    estimate_usd,
    load_dev_cases,
    load_prompt_targets,
    optimize_prompt,
    run_case,
    score_case,
    split_cases,
)
from pet_helper.trace import BudgetExceeded
from tests.unit.optimize_support import (
    GOOD_PROMPT,
    ChargingRunner,
    PromptSensitiveRunner,
    make_cases,
    reflection_lm,
    write_cases,
)
from tests.unit.otel_support import ScriptedGateway

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SECRET_TURN = "Meu CPF é 123.456.789-09 e o endereço é Rua dos Segredos, 42"  # noqa: S105
SECRET_REPLY = "Resposta com SEGREDO-DO-CLIENTE"  # noqa: S105


def _ids(cases) -> list[str]:
    return [case.id for case in cases]


# the dev-only loader


def test_it_should_load_the_dev_cases_sorted_by_id(tmp_path):
    write_cases(tmp_path / "cases", make_cases(3)[::-1])

    cases = load_dev_cases(tmp_path / "cases")

    assert _ids(cases) == ["case-00", "case-01", "case-02"]


def test_it_should_refuse_a_cases_directory_inside_a_held_out_folder(tmp_path):
    write_cases(tmp_path / "held_out" / "cases", make_cases(3))

    with pytest.raises(OptimizeRefused, match="held-out"):
        load_dev_cases(tmp_path / "held_out" / "cases")


# the owner's target list


def _targets_file(tmp_path, classification: dict[str, str]) -> Path:
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(classification), encoding="utf-8")
    return path


def test_it_should_return_only_the_ids_the_owner_classified_as_prompt(tmp_path):
    path = _targets_file(
        tmp_path, {"case-02": "prompt", "case-01": "checker", "case-00": "prompt"}
    )

    targets = load_prompt_targets(path, make_cases(3))

    assert targets == ["case-00", "case-02"]


def test_it_should_refuse_a_target_that_is_not_a_visible_dev_case(tmp_path):
    path = _targets_file(tmp_path, {"case-00": "prompt", "held-case-x": "prompt"})

    with pytest.raises(OptimizeRefused, match="held-case-x"):
        load_prompt_targets(path, make_cases(3))


def test_it_should_refuse_a_classification_that_is_neither_prompt_nor_checker(tmp_path):
    path = _targets_file(tmp_path, {"case-00": "prompt", "case-01": "promt"})

    with pytest.raises(OptimizeRefused, match="promt"):
        load_prompt_targets(path, make_cases(3))


def test_it_should_refuse_a_target_list_with_no_prompt_target(tmp_path):
    path = _targets_file(tmp_path, {"case-00": "checker"})

    with pytest.raises(OptimizeRefused, match="no prompt targets"):
        load_prompt_targets(path, make_cases(3))


# the train / validation split


def test_it_should_split_twenty_cases_into_ten_and_ten():
    cases = make_cases(20)

    train, val = split_cases(cases, ["case-03", "case-04"])

    assert (len(train), len(val)) == (10, 10)


def test_it_should_split_without_sharing_a_case_between_sides():
    cases = make_cases(20)

    train, val = split_cases(cases, ["case-03", "case-04"])

    assert sorted(_ids(train) + _ids(val)) == _ids(cases)


def test_it_should_give_each_side_half_of_the_targets():
    cases = make_cases(20)
    targets = [f"case-{n:02d}" for n in (0, 1, 2, 3, 4, 5)]

    train, val = split_cases(cases, targets)

    assert (
        len(set(_ids(train)) & set(targets)),
        len(set(_ids(val)) & set(targets)),
    ) == (3, 3)


def test_it_should_split_the_same_way_whatever_the_input_order():
    cases = make_cases(20)
    targets = ["case-03", "case-04"]

    first = split_cases(cases, targets)
    second = split_cases(cases[::-1], targets)

    assert tuple(map(_ids, second)) == tuple(map(_ids, first))


# the metric


def _record(text: str, *, tool_calls=()) -> RunRecord:
    return RunRecord(replies=[Reply(text=text)], tool_calls=tool_calls)


def test_it_should_score_one_when_every_check_passes():
    case = make_cases(1)[0]

    prediction = score_case(case, _record("Cobrimos a garantia por 90 dias"))

    assert prediction.score == 1.0


def test_it_should_score_zero_when_a_check_fails():
    case = make_cases(1)[0]

    prediction = score_case(case, _record("nada a declarar"))

    assert prediction.score == 0.0


def test_it_should_list_every_failed_check_in_the_feedback():
    case = make_cases(1)[0].model_copy(
        update={
            "checks": [
                Check(type="must_include", value="garantia"),
                Check(type="tool_called", value="get_order"),
            ]
        }
    )

    prediction = score_case(case, _record("nada a declarar"))

    assert "must_include" in prediction.feedback
    assert "tool_called" in prediction.feedback


def test_it_should_leave_a_passing_check_out_of_the_feedback():
    case = make_cases(1)[0].model_copy(
        update={
            "checks": [
                Check(type="must_include", value="garantia"),
                Check(type="tool_called", value="get_order"),
            ]
        }
    )

    prediction = score_case(case, _record("garantia"))

    assert "must_include" not in prediction.feedback
    assert "tool_called" in prediction.feedback


def test_it_should_keep_customer_text_out_of_the_feedback():
    case = make_cases(1)[0].model_copy(update={"turns": [SECRET_TURN]})

    prediction = score_case(case, _record(SECRET_REPLY))

    assert "123.456.789-09" not in prediction.feedback
    assert "SEGREDO-DO-CLIENTE" not in prediction.feedback
    assert "Segredos" not in prediction.feedback


def test_it_should_score_from_the_checks_alone_when_the_case_lists_judge_criteria():
    case = make_cases(1)[0].model_copy(
        update={"judge": ["tone", "escalation_judgment"]}
    )

    prediction = score_case(case, _record("garantia"))

    assert prediction.score == 1.0


def test_it_should_use_the_tool_calls_of_the_record_in_the_checks():
    case = make_cases(1)[0].model_copy(
        update={"checks": [Check(type="tool_called", value="get_order")]}
    )
    record = _record(
        "ok", tool_calls=(ToolCall("get_order", {"order_id": "PED-00001"}),)
    )

    prediction = score_case(case, record)

    assert prediction.score == 1.0


# running a case with a given prompt


def test_it_should_run_the_turns_of_a_case_and_return_the_replies():
    case = make_cases(1)[0]
    gateway = FakeGateway(["Cobrimos a garantia por 90 dias"])

    record = run_case(case, "PROMPT-X", gateway=gateway, kit_dir=REPO_KIT_DIR)

    assert [reply.text for reply in record.replies] == [
        "Cobrimos a garantia por 90 dias"
    ]


def test_it_should_run_a_case_with_the_given_prompt():
    case = make_cases(1)[0]
    gateway = FakeGateway(["Olá"])

    run_case(case, "PROMPT-X", gateway=gateway, kit_dir=REPO_KIT_DIR)

    first = gateway.calls[0][0]
    assert isinstance(first, SystemMessage)
    assert first.content == "PROMPT-X"


# the rollout cost goes through the round budget


def test_it_should_refuse_a_rollout_whose_reserve_exceeds_the_budget():
    case = make_cases(1)[0]
    gateway = ScriptedGateway(["Cobrimos a garantia por 90 dias"])

    with pytest.raises(BudgetExceeded):
        run_case(
            case,
            "PROMPT-X",
            gateway=gateway,
            kit_dir=REPO_KIT_DIR,
            budget=RoundBudget(Decimal("0.000001")),
        )


def test_it_should_return_the_replies_of_a_rollout_the_budget_has_room_for():
    case = make_cases(1)[0]
    gateway = ScriptedGateway(["Cobrimos a garantia por 90 dias"])

    record = run_case(
        case,
        "PROMPT-X",
        gateway=gateway,
        kit_dir=REPO_KIT_DIR,
        budget=RoundBudget(Decimal(5)),
    )

    assert [reply.text for reply in record.replies] == [
        "Cobrimos a garantia por 90 dias"
    ]


# the thin module


def test_it_should_expose_the_prompt_as_the_only_predictor():
    program = AssistantProgram("Prompt de partida", PromptSensitiveRunner())

    predictors = program.predictors()

    assert [p.signature.instructions for p in predictors] == ["Prompt de partida"]


def test_it_should_run_the_case_with_the_current_instructions_of_the_predictor():
    runner = PromptSensitiveRunner()
    program = AssistantProgram("Prompt de partida", runner)
    predictor = program.predictors()[0]
    predictor.signature = predictor.signature.with_instructions("Prompt trocado")
    case = make_cases(1)[0]

    program(case=case)

    assert runner.calls == [(case.id, "Prompt trocado")]


def test_it_should_return_the_run_record_of_the_runner():
    program = AssistantProgram(GOOD_PROMPT, PromptSensitiveRunner())

    prediction = program(case=make_cases(1)[0])

    assert [reply.text for reply in prediction.record.replies] == ["garantia"]


# GEPA, offline


def _optimize(seed="Prompt de partida", *, runner=None, lm=None, max_calls=12):
    cases = make_cases(8)
    return optimize_prompt(
        seed,
        cases[:4],
        cases[4:],
        runner=runner or PromptSensitiveRunner(),
        reflection_lm=lm or reflection_lm(),
        max_metric_calls=max_calls,
    )


def test_it_should_return_the_candidate_the_reflection_proposed_when_it_scores_higher():
    candidate = _optimize()

    assert candidate == GOOD_PROMPT


def test_it_should_return_the_seed_when_no_candidate_scores_higher():
    candidate = _optimize(lm=reflection_lm("segue sem a palavra certa"))

    assert candidate == "Prompt de partida"


def test_it_should_overshoot_the_metric_call_budget_by_at_most_one_validation_pass():
    runner = PromptSensitiveRunner()

    _optimize(runner=runner, max_calls=12)

    assert len(runner.calls) <= 12 + 4


def test_it_should_run_the_runner_only_on_the_cases_it_was_given():
    runner = PromptSensitiveRunner()

    _optimize(runner=runner)

    assert {case_id for case_id, _ in runner.calls} <= set(_ids(make_cases(8)))


# the estimate


def test_it_should_estimate_a_positive_cost():
    assert estimate_usd(300) > Decimal(0)


def test_it_should_estimate_more_for_more_metric_calls():
    assert estimate_usd(300) > estimate_usd(30)


# the cap and the natural end of a run

NATURAL_END_CALLS = 12
LATE_ROLLOUT_COST = Decimal("0.03")


class _LateSpendRunner(ChargingRunner):
    """Spends little until the metric-call budget is used up, then a lot on each later rollout.

    The spend only reaches the last reserve of the cap after the run used all its metric calls, so no stop
    check can see it while GEPA is still working.
    """

    def __call__(self, case, system_prompt):
        record = super().__call__(case, system_prompt)
        if len(self.calls) >= NATURAL_END_CALLS:
            self.budget.on_cost(LATE_ROLLOUT_COST)
        return record


def test_it_should_return_the_candidate_when_a_run_that_used_all_its_metric_calls_ends_near_the_cap():
    budget = RoundBudget(Decimal("0.12"))
    runner = _LateSpendRunner(budget, Decimal("0.001"), reserve=Decimal("0.001"))
    cases = make_cases(8)

    candidate = optimize_prompt(
        "Prompt de partida",
        cases[:4],
        cases[4:],
        runner=runner,
        reflection_lm=reflection_lm(),
        max_metric_calls=NATURAL_END_CALLS,
        budget=budget,
    )

    assert candidate == GOOD_PROMPT
    assert budget.cap - JUDGE_CALL_RESERVE_USD < budget.spent < budget.cap
