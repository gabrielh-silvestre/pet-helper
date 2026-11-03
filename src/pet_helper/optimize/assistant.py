import inspect
import json
import tempfile
import threading
from collections.abc import Callable, Collection, Sequence
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import dspy
from dspy.utils.callback import BaseCallback

from pet_helper.conversation import DEFAULT_KIT_DIR, build_assistant
from pet_helper.evals import EvalCase, checks, fixtures, load_cases, task
from pet_helper.evals import budget as round_budget
from pet_helper.gateway import ModelGateway
from pet_helper.optimize import OptimizeRefused
from pet_helper.trace import BudgetExceeded, TraceCallbackHandler

_HELD_OUT = "held_out"
_CLASSIFICATIONS = ("prompt", "checker")
_REFLECTION_MINIBATCH = 3
# the plan counts about one reflection call per ten metric calls
_METRIC_CALLS_PER_REFLECTION = 10

Runner = Callable[[EvalCase, str], checks.RunRecord]


class CapStopped(BudgetExceeded):
    """The cap or a refused rollout ended the run; `candidate` is the best prompt found until then."""

    def __init__(self, message: str, candidate: str):
        super().__init__(message)
        self.candidate = candidate


def load_dev_cases(cases_dir: Path) -> list[EvalCase]:
    if _HELD_OUT in cases_dir.resolve().parts:
        raise OptimizeRefused(f"{cases_dir}: held-out cases are never optimized")
    return sorted(load_cases(cases_dir), key=lambda case: case.id)


def load_prompt_targets(targets_path: Path, cases: Sequence[EvalCase]) -> list[str]:
    classification = json.loads(targets_path.read_text(encoding="utf-8"))
    if not isinstance(classification, dict):
        raise OptimizeRefused(f"{targets_path}: expected an object of case id to class")
    visible = {case.id for case in cases}
    if unknown := sorted(set(classification) - visible):
        raise OptimizeRefused(f"not a visible dev case: {', '.join(unknown)}")
    if invalid := sorted(
        {str(v) for v in classification.values()} - set(_CLASSIFICATIONS)
    ):
        raise OptimizeRefused(f"unknown classification: {', '.join(invalid)}")
    targets = sorted(id_ for id_, kind in classification.items() if kind == "prompt")
    if not targets:
        raise OptimizeRefused("no prompt targets in the classification")
    return targets


def split_cases(
    cases: Sequence[EvalCase], targets: Collection[str]
) -> tuple[list[EvalCase], list[EvalCase]]:
    ordered = sorted(cases, key=lambda case: case.id)
    train: list[EvalCase] = []
    val: list[EvalCase] = []
    # alternating inside each group gives both sides half of the targets and half of the rest
    for group in (
        [case for case in ordered if case.id in targets],
        [case for case in ordered if case.id not in targets],
    ):
        train += group[0::2]
        val += group[1::2]
    return sorted(train, key=lambda case: case.id), sorted(
        val, key=lambda case: case.id
    )


def score_case(
    case: EvalCase, record: checks.RunRecord, known_skus: Collection[str] = ()
) -> dspy.Prediction:
    # the judge is never called: its critique would be customer text and its cost per rollout is real
    failed = checks.evaluate_case(case, record, known_skus).failed_checks
    if not failed:
        return dspy.Prediction(score=1.0, feedback="Every check passed")
    return dspy.Prediction(score=0.0, feedback=f"Failed checks: {', '.join(failed)}")


def run_case(
    case: EvalCase,
    system_prompt: str,
    *,
    gateway: ModelGateway,
    kit_dir: Path | None = None,
    budget: round_budget.RoundBudget | None = None,
) -> checks.RunRecord:
    handler_factory = (
        TraceCallbackHandler
        if budget is None
        else partial(
            TraceCallbackHandler,
            reserve=round_budget.ASSISTANT_CALL_RESERVE_USD,
            before_call=budget.before_call,
            on_cost=budget.on_cost,
            on_error=budget.on_error,
            strict=True,
        )
    )
    with tempfile.TemporaryDirectory() as scratch:
        kit = Path(kit_dir or DEFAULT_KIT_DIR)
        if case.fixture:
            fixtures.overlay_kit(kit, case.fixture, Path(scratch) / "kit")
            kit = Path(scratch) / "kit"
        assistant = build_assistant(
            gateway=gateway,
            kit_dir=kit,
            today=case.today,
            system_prompt=system_prompt,
            handler_factory=handler_factory,
        )
        session_id = f"optimize-{case.id}"
        try:
            replies = [assistant.turn(session_id, text) for text in case.turns]
            return task.run_record(replies, assistant.snapshot(session_id))
        finally:
            assistant.close()


class AssistantProgram(dspy.Module):
    def __init__(self, system_prompt: str, runner: Runner):
        super().__init__()
        self._seed = system_prompt
        self.predict = dspy.Predict(dspy.Signature("case -> record", system_prompt))
        # a function is copied by reference: GEPA deep-copies the program, a copied runner would lose its state
        self._runner = lambda case, prompt: runner(case, prompt)

    def forward(self, case: EvalCase) -> dspy.Prediction:
        instructions = self.predict.signature.instructions
        # dspy cleans the instructions it hands back; the untouched seed keeps its exact bytes
        prompt = (
            self._seed if instructions == inspect.cleandoc(self._seed) else instructions
        )
        prediction = dspy.Prediction(record=self._runner(case, prompt))
        # the LangGraph run is no dspy predictor call, so GEPA only sees it through this trace entry
        if dspy.settings.trace is not None:
            dspy.settings.trace.append((self.predict, {"case": case.id}, prediction))
        return prediction


def _examples(cases: Sequence[EvalCase]) -> list[dspy.Example]:
    return [dspy.Example(case=case).with_inputs("case") for case in cases]


class _ReflectionCost(BaseCallback):
    # shortcut: reads lm.history[-1], racy if GEPA is parallelized, and the cost comes from the litellm
    # price table instead of genai-prices; upgrade when GEPA runs in parallel
    """Charges the cost of each reflection request to the budget (dspy callbacks cannot raise)."""

    def __init__(self, lm: Any, budget: round_budget.RoundBudget):
        self._lm = lm
        self._budget = budget

    def on_lm_end(self, call_id: str, outputs: Any, exception: Any = None) -> None:
        if exception is None and self._lm.history:
            self._budget.on_cost(Decimal(str(self._lm.history[-1].get("cost") or 0)))


def optimize_prompt(
    seed_prompt: str,
    train: Sequence[EvalCase],
    val: Sequence[EvalCase],
    *,
    runner: Runner,
    reflection_lm: Any,
    max_metric_calls: int,
    known_skus: Collection[str] = (),
    budget: round_budget.RoundBudget | None = None,
) -> str:
    def metric(
        example: Any, prediction: Any, trace: Any = None, *_: Any
    ) -> dspy.Prediction:
        return score_case(example.case, prediction.record, known_skus)

    # without a budget nothing caps the spend here
    spend = budget or round_budget.RoundBudget(Decimal("Infinity"))
    refused = threading.Event()
    capped = threading.Event()
    rollouts = 0

    def capped_runner(case: EvalCase, system_prompt: str) -> checks.RunRecord:
        nonlocal rollouts
        rollouts += 1
        # dspy scores a raising rollout as a failure, so the refusal is remembered here
        try:
            return runner(case, system_prompt)
        except BudgetExceeded:
            refused.set()
            raise

    def must_stop(_state: Any) -> bool:
        # room for one more reflection call, the dearest model of the run
        reflection_reserve = round_budget.JUDGE_CALL_RESERVE_USD
        if refused.is_set():
            return True
        # GEPA runs this check once more at its natural end, which is no cap stop
        if rollouts < max_metric_calls and spend.spent + reflection_reserve > spend.cap:
            capped.set()
        return capped.is_set()

    gepa = dspy.GEPA(
        metric=metric,
        max_metric_calls=max_metric_calls,
        reflection_minibatch_size=_REFLECTION_MINIBATCH,
        reflection_lm=reflection_lm,
        gepa_kwargs={"stop_callbacks": [must_stop]},
    )
    with dspy.context(callbacks=[_ReflectionCost(reflection_lm, spend)]):
        best = gepa.compile(
            AssistantProgram(seed_prompt, capped_runner),
            trainset=_examples(train),
            valset=_examples(val),
        )
    instructions = best.predict.signature.instructions
    candidate = (
        seed_prompt if instructions == inspect.cleandoc(seed_prompt) else instructions
    )
    if refused.is_set() or capped.is_set():
        raise CapStopped(
            f"The cap of US$ {spend.cap} stopped the run "
            f"(spent US$ {spend.spent}, {rollouts} metric calls)",
            candidate,
        )
    return candidate


def estimate_usd(max_metric_calls: int) -> Decimal:
    reflections = -(-max_metric_calls // _METRIC_CALLS_PER_REFLECTION)
    return (
        max_metric_calls * round_budget.ASSISTANT_CALL_RESERVE_USD
        + reflections * round_budget.JUDGE_CALL_RESERVE_USD
    )
