import ast
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pet_helper.evals import judge as judge_module
from pet_helper.evals import labels as labels_module
from pet_helper.optimize import OptimizeRefused
from pet_helper.trace import read_jsonl

_HELD_OUT = "held_out"
_BOOTSTRAPPED_CRITERION = "escalation_judgment"
_LABELLED_CRITERION = "dont_know_accuracy"
_MAX_DEMOS = 2
# a hand-labelled item has no critique to show; the prompt still renders the field
_LABEL_CRITIQUE = "Veredito do rótulo humano."


@dataclass(frozen=True)
class DevItem:
    id: str
    criterion: str
    slice: str
    label: str
    split: str


def load_dev_items(round_dir: Path) -> list[DevItem]:
    if _HELD_OUT in round_dir.resolve().parts:
        raise OptimizeRefused(f"{round_dir}: a held-out round is never optimized")
    labels = round_dir / "labels"
    if not (labels / "pool.jsonl").is_file():
        raise OptimizeRefused(f"{round_dir}: not a labelled round, run labels pool")
    pool = read_jsonl(labels / "pool.jsonl")
    key = labels_module.read_key(labels)
    human_path = labels / "human.jsonl"
    human = (
        {row["id"]: row["label"] for row in read_jsonl(human_path)}
        if human_path.is_file()
        else {}
    )
    if missing := [item["id"] for item in pool if item["id"] not in human]:
        raise OptimizeRefused(f"human.jsonl lacks a label for: {', '.join(missing)}")
    return [
        DevItem(item["id"], item["criterion"], item["slice"], human[item["id"]], "dev")
        for item in pool
        if key[item["id"]]["split"] == "dev"
    ]


def verdict_metric(item: DevItem, verdict: str) -> float | None:
    if item.split != "dev":
        raise OptimizeRefused(f"{item.id}: the test split is never scored here")
    if verdict == "unable":
        return None
    return float(verdict == item.label)


def estimate_usd(items: Sequence[DevItem]) -> Decimal:
    # only the bootstrap calls the model, at most once per item; the labelled criterion costs nothing
    calls = sum(item.criterion == _BOOTSTRAPPED_CRITERION for item in items)
    return calls * judge_module.JUDGE_CALL_RESERVE_USD


def select_examples(
    criterion: str, items: Sequence[DevItem], *, lm: Any = None
) -> list[judge_module.FewShotExample]:
    if criterion == _LABELLED_CRITERION:
        return [
            judge_module.FewShotExample(
                criterion, item.slice, _LABEL_CRITIQUE, item.label
            )
            for item in items
        ]
    if criterion == _BOOTSTRAPPED_CRITERION and items:
        return _bootstrap(items, lm)
    return []


def _bootstrap(items: Sequence[DevItem], lm: Any) -> list[judge_module.FewShotExample]:
    import dspy
    from dspy.teleprompt import BootstrapFewShot

    class Judge(dspy.Signature):
        slice: str = dspy.InputField()
        critique: str = dspy.OutputField()
        verdict: Literal["pass", "fail", "unable"] = dspy.OutputField()

    # the rubric of the real judge, so a demo is reasoned under the rule the judge applies
    instructed = Judge.with_instructions(
        judge_module.CRITERION_PROMPTS[_BOOTSTRAPPED_CRITERION]
    )
    by_slice = {item.slice: item for item in items}

    def metric(example: Any, prediction: Any, trace: Any = None) -> bool:
        return bool(verdict_metric(by_slice[example.slice], prediction.verdict))

    trainset = [
        dspy.Example(slice=item.slice, verdict=item.label).with_inputs("slice")
        for item in items
    ]
    optimizer = BootstrapFewShot(
        metric=metric, max_bootstrapped_demos=_MAX_DEMOS, max_labeled_demos=_MAX_DEMOS
    )
    with dspy.context(lm=lm):
        compiled = optimizer.compile(dspy.Predict(instructed), trainset=trainset)
    return [
        judge_module.FewShotExample(
            _BOOTSTRAPPED_CRITERION,
            demo["slice"],
            demo.get("critique") or _LABEL_CRITIQUE,
            by_slice[demo["slice"]].label,
        )
        for demo in compiled.demos
    ]


def _render(examples: Sequence[judge_module.FewShotExample]) -> str:
    # json strings are valid python literals, and one field per line keeps the ruff format stable
    shots = "".join(
        "    FewShotExample(\n"
        + "".join(
            f"        {json.dumps(field, ensure_ascii=False)},\n"
            for field in (e.criterion, e.slice, e.critique, e.verdict)
        )
        + "    ),\n"
        for e in examples
    )
    return f"FEW_SHOT_EXAMPLES: list[FewShotExample] = [\n{shots}]\n"


def materialize_few_shots(
    examples: Sequence[judge_module.FewShotExample], judge_path: Path
) -> None:
    if not examples:
        return
    source = judge_path.read_text(encoding="utf-8")
    assignment = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FEW_SHOT_EXAMPLES"
    )
    lines = source.splitlines(keepends=True)
    start, end = assignment.lineno - 1, assignment.end_lineno or assignment.lineno
    judge_path.write_text(
        "".join(lines[:start]) + _render(examples) + "".join(lines[end:]),
        encoding="utf-8",
    )
