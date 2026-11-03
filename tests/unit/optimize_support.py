"""A labelled judge round on disk, shared by the optimize tests (no model, no network).

The layout is the one `pet_helper.evals.labels` writes under `<round_dir>/labels/`: `pool.jsonl`, `key.json`
and `human.jsonl`. The pool has the 18 items of the plan: escalation_judgment 8 dev + 4 test,
dont_know_accuracy 2 dev + 2 test, tone 0 dev + 2 test. Every test slice carries `TEST_MARKER`, so a test
can prove that none of them ever reached a model.

By default the round also holds the baseline of the current judge on the test split: a perfect verdict for
each test item in `key.json` and a `judge-runs.jsonl` line carrying the current judge hash, as the first
paid `judge-pool --split test` would leave them. `FakeValidation` stands for the later forced run of a
candidate (no model): it rewrites those verdicts and appends the run line, as `judge-pool` does.
"""

import importlib.util
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from pet_helper.conversation import Reply
from pet_helper.evals import judge as judge_module
from pet_helper.evals.cases import Check, EvalCase
from pet_helper.evals.checks import RunRecord

TEST_MARKER = "TESTSPLIT"
JUDGE_SOURCE = Path(judge_module.__file__)

# (criterion, split, how many items, how many of them a human labelled pass)
_LAYOUT = (
    ("escalation_judgment", "dev", 8, 4),
    ("escalation_judgment", "test", 4, 2),
    ("dont_know_accuracy", "dev", 2, 1),
    ("dont_know_accuracy", "test", 2, 1),
    ("tone", "test", 2, 1),
)


@dataclass(frozen=True)
class LabelledRound:
    directory: Path
    # slice -> human label, for the dev items only
    dev: dict[str, str]
    dev_by_criterion: dict[str, dict[str, str]]
    test_slices: list[str]
    ids: dict[str, str]
    unlabelled_ids: list[str]
    # item id -> human label, for the test items only
    test_labels: dict[str, str]


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


def record_test_run(
    directory: Path, verdicts: dict[str, str], sha256: str, *, forced: bool
) -> None:
    labels = directory / "labels"
    key = json.loads((labels / "key.json").read_text(encoding="utf-8"))
    for item_id, verdict in verdicts.items():
        key[item_id]["verdict"] = verdict
        key[item_id]["judge_prompt_sha256"] = sha256
    (labels / "key.json").write_text(json.dumps(key), encoding="utf-8")
    run = {
        "split": "test",
        "judge_prompt_sha256": sha256,
        "forced": forced,
        "items": len(verdicts),
        "complete": True,
        "cost_usd": "0",
    }
    with (labels / "judge-runs.jsonl").open("a", encoding="utf-8") as file:
        file.write(_jsonl([run]))


def build_labelled_round(
    parent: Path,
    *,
    name: str = "round",
    unlabelled: int = 0,
    baseline: bool = True,
    baseline_sha256: str | None = None,
) -> LabelledRound:
    directory = parent / name
    labels = directory / "labels"
    labels.mkdir(parents=True)
    (directory / "round.json").write_text("{}", encoding="utf-8")
    pool, key, human, ids = [], {}, [], {}
    dev: dict[str, str] = {}
    by_criterion: dict[str, dict[str, str]] = {}
    test_slices = []
    test_labels: dict[str, str] = {}
    for criterion, split, count, passes in _LAYOUT:
        for number in range(count):
            item_id = f"{criterion[:3]}-{split}-{number}"
            marker = TEST_MARKER if split == "test" else "DEVSPLIT"
            text = f"{marker} {criterion} item {number}: conversa do cliente"
            label = "pass" if number < passes else "fail"
            pool.append({"id": item_id, "criterion": criterion, "slice": text})
            key[item_id] = {
                "criterion": criterion,
                "split": split,
                "seeded": False,
                "group": "natural",
                "case_id": f"{criterion}-{split}-case-{number}",
            }
            human.append({"id": item_id, "label": label})
            ids[text] = item_id
            if split == "dev":
                dev[text] = label
                by_criterion.setdefault(criterion, {})[text] = label
            else:
                test_slices.append(text)
                test_labels[item_id] = label
    skipped = (
        [row["id"] for row in human[len(human) - unlabelled :]] if unlabelled else []
    )
    human = human[: len(human) - unlabelled]
    (labels / "pool.jsonl").write_text(_jsonl(pool), encoding="utf-8")
    (labels / "key.json").write_text(json.dumps(key), encoding="utf-8")
    (labels / "human.jsonl").write_text(_jsonl(human), encoding="utf-8")
    if baseline:
        sha256 = baseline_sha256 or judge_module.judge_prompt_sha256()
        record_test_run(directory, dict(test_labels), sha256, forced=False)
    return LabelledRound(
        directory, dev, by_criterion, test_slices, ids, skipped, test_labels
    )


def copy_judge_source(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / "judge_copy.py"
    shutil.copy(JUDGE_SOURCE, path)
    return path


def load_judge_copy(path: Path, monkeypatch: pytest.MonkeyPatch):
    # a unique name per file: the dataclasses of the copy look their module up in sys.modules
    name = f"judge_copy_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class FakeValidation:
    """The test-split run that `pet-helper optimize judge` hands to the labels code, with no model.

    Called as `validation(round_dir, *, force_new_validation)`; returns `code`. Besides the arguments it
    records the judge source as it was at the call, so a test can see which candidate was being validated.
    """

    def __init__(
        self,
        labelled: LabelledRound,
        judge_path: Path,
        *,
        mistakes: tuple[str, ...] = (),
        unable: bool = False,
        code: int = 0,
    ):
        self.labelled = labelled
        self.judge_path = judge_path
        self.mistakes = mistakes
        self.unable = unable
        self.code = code
        self.calls: list[dict] = []

    def _verdict(self, item_id: str, label: str) -> str:
        if self.unable:
            return "unable"
        if item_id in self.mistakes:
            return "fail" if label == "pass" else "pass"
        return label

    def __call__(self, round_dir: Path, *, force_new_validation: bool) -> int:
        self.calls.append(
            {
                "round_dir": round_dir,
                "force_new_validation": force_new_validation,
                "judge_source": self.judge_path.read_bytes(),
            }
        )
        if self.code == 0:
            verdicts = {
                i: self._verdict(i, label)
                for i, label in self.labelled.test_labels.items()
            }
            record_test_run(
                round_dir, verdicts, "candidate", forced=force_new_validation
            )
        return self.code


# the assistant optimizer: cases on disk, a runner and a reflection model that need no network

GOOD_PROMPT = "NEWPROMPT v1"
REQUIRED_TERM = "garantia"


def make_cases(count: int) -> list[EvalCase]:
    return [
        EvalCase(
            id=f"case-{number:02d}",
            today=date(2026, 11, 3),
            turns=[f"Pergunta {number} sobre a política de garantia"],
            checks=[Check(type="must_include", value=REQUIRED_TERM)],
        )
        for number in range(count)
    ]


def write_cases(directory: Path, cases: list[EvalCase]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for case in cases:
        dump = case.model_dump(mode="json", exclude_none=True)
        (directory / f"{case.id}.yaml").write_text(
            yaml.safe_dump(dump), encoding="utf-8"
        )


class PromptSensitiveRunner:
    """Answers the required term only under a prompt that carries `GOOD_PROMPT`; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, case: EvalCase, system_prompt: str) -> RunRecord:
        self.calls.append((case.id, system_prompt))
        text = REQUIRED_TERM if GOOD_PROMPT in system_prompt else "nada a declarar"
        return RunRecord(replies=[Reply(text=text)])


def reflection_lm(instruction: str = GOOD_PROMPT):
    """A dspy reflection model that always proposes `instruction` (GEPA asks for JSON)."""
    import dspy
    from dspy.utils import DummyLM

    return DummyLM([{"new_instruction": instruction}] * 200, adapter=dspy.JSONAdapter())


class ChargingRunner(PromptSensitiveRunner):
    """Charges the budget the way a round does: the reserve before the call, the real cost after it."""

    def __init__(
        self, budget, cost: Decimal, reserve: Decimal = Decimal("0.1")
    ) -> None:
        super().__init__()
        self.budget = budget
        self.cost = cost
        self.reserve = reserve

    def __call__(self, case: EvalCase, system_prompt: str) -> RunRecord:
        self.budget.before_call(self.reserve)
        record = super().__call__(case, system_prompt)
        self.budget.on_cost(self.cost)
        return record


def priced_reflection_lm(cost: Decimal, instruction: str = GOOD_PROMPT):
    """A reflection model whose every call reports `cost` in its history, as a billed dspy LM does.

    `calls` counts the requests it received (dspy may send two for one predictor call).
    """
    import dspy
    from dspy.utils import DummyLM

    class Priced(DummyLM):
        calls = 0

        def forward(self, *args, **kwargs):
            self.calls += 1
            return super().forward(*args, **kwargs)

        def update_history(self, entry):
            entry["cost"] = float(cost)
            super().update_history(entry)

    return Priced([{"new_instruction": instruction}] * 200, adapter=dspy.JSONAdapter())
