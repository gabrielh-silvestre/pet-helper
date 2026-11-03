import os
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import dspy

from pet_helper.evals import ask_confirmation, budget
from pet_helper.evals import judge as judge_module
from pet_helper.evals import labels as labels_module
from pet_helper.optimize import OptimizeRefused, accept
from pet_helper.optimize.judge import (
    estimate_usd,
    load_dev_items,
    materialize_few_shots,
    select_examples,
)
from pet_helper.trace import BudgetExceeded, read_jsonl

Validation = Callable[..., int]


@dataclass(frozen=True)
class OptimizeEnvironment:
    lm: Any
    judge_path: Path
    cap_usd: Decimal = Decimal(5)
    # validation(round_dir, *, force_new_validation) -> exit code; None runs the labels judge-pool code
    validation: Validation | None = None


def default_environment() -> OptimizeEnvironment:
    model = (
        os.environ.get(judge_module.JUDGE_MODEL_ENV) or judge_module.DEFAULT_JUDGE_MODEL
    )
    return OptimizeEnvironment(
        lm=dspy.LM(f"anthropic/{model}", max_tokens=budget.JUDGE_MAX_TOKENS),
        judge_path=Path(judge_module.__file__),
    )


def _judge_pool_on_disk(round_dir: Path, *, force_new_validation: bool) -> int:
    # a fresh process, so the candidate written to disk is the judge that reads the test split;
    # the second confirmation of the command already covers the paid calls it asks about
    command = [sys.executable, "-m", "pet_helper.evals.labels", "judge-pool"]
    command += [str(round_dir), "--split", "test"]
    if force_new_validation:
        command.append("--force-new-validation")
    return subprocess.run(command, input="yes\n", text=True, check=False).returncode  # noqa: S603


def _require_baseline(round_dir: Path) -> None:
    runs_path = round_dir / "labels" / "judge-runs.jsonl"
    runs = read_jsonl(runs_path) if runs_path.is_file() else []
    current = judge_module.judge_prompt_sha256()
    if not any(
        run["split"] == "test" and run["judge_prompt_sha256"] == current for run in runs
    ):
        raise OptimizeRefused(
            "no baseline: the current judge has not read the test split of this round, "
            "run labels judge-pool --split test first"
        )


def _test_pairs(round_dir: Path) -> dict[str, list[tuple[str, str]]]:
    labels = round_dir / "labels"
    key = labels_module.read_key(labels)
    human = {row["id"]: row["label"] for row in read_jsonl(labels / "human.jsonl")}
    pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item_id, entry in key.items():
        if entry["split"] == "test" and "verdict" in entry:
            pairs[entry["criterion"]].append((human[item_id], entry["verdict"]))
    return pairs


def _decide(
    baseline: dict[str, list[tuple[str, str]]],
    candidate: dict[str, list[tuple[str, str]]],
) -> bool:
    decisions = []
    for criterion in judge_module.CRITERIA:
        current = accept.rates_from_pairs(baseline[criterion])
        proposed = accept.rates_from_pairs(candidate[criterion])
        decision = accept.compare(current, proposed)
        print(accept.format_report(criterion, current, proposed, decision))
        decisions.append(decision)
    return all(d.accepted or d.reason == "no_measure" for d in decisions) and any(
        d.accepted for d in decisions
    )


def _choose_candidate(round_dir: Path, environment: OptimizeEnvironment) -> None:
    items = load_dev_items(round_dir)
    estimate = estimate_usd(items)
    print(f"Estimate: US$ {estimate:.4f}")
    try:
        budget.RoundBudget(environment.cap_usd).before_call(estimate)
    except BudgetExceeded as exceeded:
        raise OptimizeRefused(str(exceeded)) from exceeded
    if not ask_confirmation("Run? [y/N] "):
        raise OptimizeRefused("Aborted, nothing was called")
    examples = [
        example
        for criterion in judge_module.CRITERIA
        for example in select_examples(
            criterion,
            [i for i in items if i.criterion == criterion],
            lm=environment.lm,
        )
    ]
    materialize_few_shots(examples, environment.judge_path)


def run_judge(round_dir: Path, environment: OptimizeEnvironment) -> int:
    original = environment.judge_path.read_bytes()
    kept = False
    try:
        _require_baseline(round_dir)
        _choose_candidate(round_dir, environment)
        if not ask_confirmation("Run? [y/N] "):
            raise OptimizeRefused("Aborted, the test split was not read")
        baseline = _test_pairs(round_dir)
        validation = environment.validation or _judge_pool_on_disk
        if accept.validate_candidate(validation, round_dir) != 0:
            raise OptimizeRefused("The test run did not finish, the judge was restored")
        kept = _decide(baseline, _test_pairs(round_dir))
    except OptimizeRefused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    finally:
        if not kept:
            environment.judge_path.write_bytes(original)
    print("Candidate kept" if kept else "Candidate rejected, the current judge stays")
    return 0
