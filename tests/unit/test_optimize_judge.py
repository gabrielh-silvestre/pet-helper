"""The judge side of `pet_helper.optimize`: dev loader, metric, estimate, few-shot choice and materializer.

Assumed public surface (none of it imports `dspy` at module level, so it loads and runs without the
`optimize` extra; only the BootstrapFewShot path of `select_examples` needs `dspy`):

- `pet_helper.optimize.OptimizeRefused(Exception)`: every refusal of the optimize code; the message says why.
- `pet_helper.optimize.judge.DevItem(id, criterion, slice, label, split)`: frozen; `label` is the human
  verdict (`pass` | `fail`), `split` is `dev` | `test`.
- `load_dev_items(round_dir: Path) -> list[DevItem]`: reads `<round_dir>/labels/{pool.jsonl,key.json,
  human.jsonl}` and returns the dev items with a human label, never a test item. Refuses (OptimizeRefused)
  a round whose path has a `held_out` component, and a round where a pool item has no human label (the
  message lists the ids that miss).
- `verdict_metric(item: DevItem, verdict: str) -> float | None`: 1.0 when the verdict equals the human label,
  0.0 when it does not, None for `unable` (ignored); refuses (OptimizeRefused) an item whose split is not dev.
- `estimate_usd(items: Sequence[DevItem]) -> Decimal`: the cost to show before any spend; 0 for no items.
- `select_examples(criterion: str, items: Sequence[DevItem], *, lm=None) -> list[FewShotExample]`:
  `escalation_judgment` runs `dspy.teleprompt.BootstrapFewShot(max_bootstrapped_demos=2, max_labeled_demos=2)`
  over the dev items with `lm` (a `dspy` LM; signature input `slice`, outputs `critique` and `verdict`) and
  returns at most 4 examples; `dont_know_accuracy` returns every dev item as an example, no `lm` call;
  `tone` (and any criterion with no item) returns []. An example's `verdict` is the human label.
- `materialize_few_shots(examples: Sequence[FewShotExample], judge_path: Path) -> None`: rewrites only the
  `FEW_SHOT_EXAMPLES` assignment of the file at `judge_path` (a copy of `evals/judge.py` in the tests), in a
  fixed text form: same examples, same bytes.
"""

import importlib
import re
import sys
from dataclasses import astuple
from decimal import Decimal
from pathlib import Path

import pytest

import pet_helper
from pet_helper.evals import judge as judge_module
from pet_helper.evals.judge import FewShotExample
from pet_helper.optimize import OptimizeRefused
from pet_helper.optimize.judge import (
    DevItem,
    estimate_usd,
    load_dev_items,
    materialize_few_shots,
    select_examples,
    verdict_metric,
)
from tests.unit.optimize_support import (
    TEST_MARKER,
    build_labelled_round,
    copy_judge_source,
    load_judge_copy,
)

SRC = Path(pet_helper.__file__).parent

FIRST = FewShotExample(
    "escalation_judgment", "Trecho um", "O cliente pediu um humano.", "pass"
)
SECOND = FewShotExample(
    "dont_know_accuracy", "Trecho dois", "A resposta inventou um prazo.", "fail"
)
CHANGED = FewShotExample(
    "escalation_judgment", "Trecho um", "Outro texto de crítica.", "pass"
)


@pytest.fixture(autouse=True)
def _judge_env(monkeypatch):
    monkeypatch.delenv("PET_HELPER_JUDGE_MODEL", raising=False)


def dev_item(label="pass", split="dev", criterion="escalation_judgment") -> DevItem:
    return DevItem("item-1", criterion, "Trecho de teste", label, split)


# the dev loader


def test_it_should_load_the_dev_items_with_their_human_label(tmp_path):
    labelled = build_labelled_round(tmp_path)

    items = load_dev_items(labelled.directory)

    assert {(i.slice, i.label) for i in items} == set(labelled.dev.items())


def test_it_should_load_every_item_as_a_dev_split_item(tmp_path):
    labelled = build_labelled_round(tmp_path)

    items = load_dev_items(labelled.directory)

    assert {i.split for i in items} == {"dev"}


def test_it_should_never_load_a_test_split_item(tmp_path):
    labelled = build_labelled_round(tmp_path)

    items = load_dev_items(labelled.directory)

    assert not [i for i in items if TEST_MARKER in i.slice]


def test_it_should_load_no_tone_item_because_tone_has_none_in_dev(tmp_path):
    labelled = build_labelled_round(tmp_path)

    items = load_dev_items(labelled.directory)

    assert not [i for i in items if i.criterion == "tone"]


def test_it_should_refuse_a_round_under_a_held_out_path(tmp_path):
    labelled = build_labelled_round(tmp_path / "held_out")

    with pytest.raises(OptimizeRefused):
        load_dev_items(labelled.directory)


def test_it_should_refuse_a_pool_with_unlabelled_items_and_name_them(tmp_path):
    labelled = build_labelled_round(tmp_path, unlabelled=2)

    with pytest.raises(OptimizeRefused) as refusal:
        load_dev_items(labelled.directory)

    assert all(item_id in str(refusal.value) for item_id in labelled.unlabelled_ids)


# the metric


def test_it_should_score_a_verdict_equal_to_the_human_label_as_a_hit():
    assert verdict_metric(dev_item("pass"), "pass") == 1.0


def test_it_should_score_a_verdict_different_from_the_human_label_as_a_miss():
    assert verdict_metric(dev_item("pass"), "fail") == 0.0


def test_it_should_ignore_an_unable_verdict():
    assert verdict_metric(dev_item("fail"), "unable") is None


def test_it_should_refuse_to_score_an_item_of_the_test_split():
    with pytest.raises(OptimizeRefused):
        verdict_metric(dev_item(split="test"), "pass")


# the estimate


def test_it_should_estimate_zero_without_items():
    assert estimate_usd([]) == Decimal(0)


def test_it_should_estimate_a_positive_cost_for_the_dev_items(tmp_path):
    items = load_dev_items(build_labelled_round(tmp_path).directory)

    assert estimate_usd(items) > 0


def test_it_should_estimate_more_for_more_items(tmp_path):
    items = load_dev_items(build_labelled_round(tmp_path).directory)

    assert estimate_usd(items) > estimate_usd(items[:2])


# the few-shot choice


def test_it_should_choose_no_example_for_tone():
    assert select_examples("tone", []) == []


def test_it_should_use_both_dont_know_dev_items_as_examples_without_a_model(tmp_path):
    labelled = build_labelled_round(tmp_path)
    items = [
        i
        for i in load_dev_items(labelled.directory)
        if i.criterion == "dont_know_accuracy"
    ]

    examples = select_examples("dont_know_accuracy", items)

    assert {(e.slice, e.verdict) for e in examples} == set(
        labelled.dev_by_criterion["dont_know_accuracy"].items()
    )


def test_it_should_tag_each_dont_know_example_with_its_criterion(tmp_path):
    labelled = build_labelled_round(tmp_path)
    items = [
        i
        for i in load_dev_items(labelled.directory)
        if i.criterion == "dont_know_accuracy"
    ]

    examples = select_examples("dont_know_accuracy", items)

    assert {e.criterion for e in examples} == {"dont_know_accuracy"}


@pytest.fixture
def escalation(tmp_path):
    pytest.importorskip("dspy")
    from dspy.utils import DummyLM

    labelled = build_labelled_round(tmp_path)
    items = [
        i
        for i in load_dev_items(labelled.directory)
        if i.criterion == "escalation_judgment"
    ]
    lm = DummyLM(
        {i.slice: {"critique": "análise curta", "verdict": i.label} for i in items}
    )
    return labelled, items, lm


def test_it_should_choose_escalation_examples_among_the_dev_items(escalation):
    labelled, items, lm = escalation

    examples = select_examples("escalation_judgment", items, lm=lm)

    assert examples
    assert {e.slice for e in examples} <= set(
        labelled.dev_by_criterion["escalation_judgment"]
    )


def test_it_should_give_each_escalation_example_the_human_label_as_verdict(escalation):
    labelled, items, lm = escalation

    examples = select_examples("escalation_judgment", items, lm=lm)

    human = labelled.dev_by_criterion["escalation_judgment"]
    assert all(e.verdict == human[e.slice] for e in examples)


def test_it_should_choose_at_most_four_escalation_examples(escalation):
    _, items, lm = escalation

    examples = select_examples("escalation_judgment", items, lm=lm)

    assert len(examples) <= 4


def test_it_should_ask_the_model_while_choosing_escalation_examples(escalation):
    _, items, lm = escalation

    select_examples("escalation_judgment", items, lm=lm)

    assert lm.history


# the materializer


def test_it_should_leave_the_judge_file_untouched_when_there_is_no_example(tmp_path):
    path = copy_judge_source(tmp_path)
    before = path.read_bytes()

    materialize_few_shots([], path)

    assert path.read_bytes() == before


def test_it_should_expose_the_chosen_examples_in_the_materialized_judge(
    tmp_path, monkeypatch
):
    path = copy_judge_source(tmp_path)

    materialize_few_shots([FIRST, SECOND], path)

    shots = load_judge_copy(path, monkeypatch).FEW_SHOT_EXAMPLES
    assert [astuple(e) for e in shots] == [astuple(FIRST), astuple(SECOND)]


def test_it_should_render_the_chosen_examples_in_the_prompt_of_their_criterion(
    tmp_path, monkeypatch
):
    path = copy_judge_source(tmp_path)

    materialize_few_shots([FIRST], path)

    system, _ = load_judge_copy(path, monkeypatch).build_prompt(
        "escalation_judgment", "x"
    )
    assert FIRST.slice in system
    assert FIRST.critique in system


def test_it_should_keep_the_criterion_prompts_when_materializing(tmp_path, monkeypatch):
    path = copy_judge_source(tmp_path)

    materialize_few_shots([FIRST], path)

    assert (
        load_judge_copy(path, monkeypatch).CRITERION_PROMPTS
        == judge_module.CRITERION_PROMPTS
    )


def test_it_should_change_the_judge_prompt_hash_when_examples_are_materialized(
    tmp_path, monkeypatch
):
    path = copy_judge_source(tmp_path)

    materialize_few_shots([FIRST], path)

    assert (
        load_judge_copy(path, monkeypatch).judge_prompt_sha256()
        != judge_module.judge_prompt_sha256()
    )


def test_it_should_keep_the_judge_prompt_hash_when_no_example_is_materialized(
    tmp_path, monkeypatch
):
    path = copy_judge_source(tmp_path)

    materialize_few_shots([], path)

    assert (
        load_judge_copy(path, monkeypatch).judge_prompt_sha256()
        == judge_module.judge_prompt_sha256()
    )


def test_it_should_write_the_same_bytes_for_the_same_examples(tmp_path):
    first = copy_judge_source(tmp_path / "a")
    second = copy_judge_source(tmp_path / "b")

    materialize_few_shots([FIRST, SECOND], first)
    materialize_few_shots([FIRST, SECOND], second)

    assert first.read_bytes() == second.read_bytes()


def test_it_should_write_the_same_bytes_when_materializing_the_same_examples_twice(
    tmp_path,
):
    path = copy_judge_source(tmp_path)
    materialize_few_shots([FIRST, SECOND], path)
    once = path.read_bytes()

    materialize_few_shots([FIRST, SECOND], path)

    assert path.read_bytes() == once


def test_it_should_change_the_judge_prompt_hash_when_an_example_text_changes(
    tmp_path, monkeypatch
):
    path = copy_judge_source(tmp_path)
    materialize_few_shots([FIRST], path)
    before = load_judge_copy(path, monkeypatch).judge_prompt_sha256()

    materialize_few_shots([CHANGED], path)

    assert load_judge_copy(path, monkeypatch).judge_prompt_sha256() != before


# where dspy may be imported


def test_it_should_load_the_dspy_free_modules_without_the_optimize_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "dspy", None)
    for name in [n for n in sys.modules if n.startswith("pet_helper.optimize")]:
        monkeypatch.delitem(sys.modules, name)

    loaded = [
        importlib.import_module(n)
        for n in ("pet_helper.optimize.accept", "pet_helper.optimize.judge")
    ]

    assert all(module is not None for module in loaded)


def test_it_should_import_dspy_only_inside_the_optimize_package():
    importers = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "optimize" not in path.relative_to(SRC).parts
        and re.search(
            r"^\s*(import dspy|from dspy)", path.read_text(encoding="utf-8"), re.M
        )
    ]

    assert importers == []


def test_it_should_force_a_new_validation_only_from_the_accept_module():
    callers = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "force_new_validation=True" in path.read_text(encoding="utf-8")
    ]

    assert callers == ["optimize/accept.py"]
