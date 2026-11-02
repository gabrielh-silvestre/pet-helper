"""Characterization of the Inspect AI behaviors the eval runner depends on (no real model)."""

import asyncio
import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest
from inspect_ai import Epochs, Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog, read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import Generate, TaskState, solver

SLEEP = 0.4
AT_LEAST_3 = Epochs(3, "at_least_3")
USAGE = {"input_tokens": 11, "output_tokens": 7}


class FakeJudge:
    """Sync judge client that blocks like a network call, at a barrier when given one."""

    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        self.spans: list[tuple[float, float]] = []
        self.barrier = barrier

    def grade(self, answer: str) -> str:
        start = time.monotonic()
        if self.barrier is not None:
            self.barrier.wait()
        else:
            time.sleep(SLEEP)
        self.spans.append((start, time.monotonic()))
        return CORRECT


def slow_sync() -> None:
    time.sleep(SLEEP)


@solver
def scripted(calls: list[tuple[str, int]]):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        calls.append((str(state.sample_id), state.epoch))
        if (
            state.metadata.get("raises")
            or state.metadata.get("raises_epoch") == state.epoch
        ):
            raise RuntimeError("sample exploded")
        state.metadata["epoch_passes"] = state.epoch <= state.metadata["pass_epochs"]
        return state

    return solve


@solver
def timed(
    spans: list[tuple[float, float]],
    mode: str,
    barrier: threading.Barrier | None = None,
):
    blocking = slow_sync if barrier is None else barrier.wait

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        start = time.monotonic()
        if mode == "thread":
            await asyncio.to_thread(blocking)
        else:
            blocking()
        spans.append((start, time.monotonic()))
        return state

    return solve


@scorer(metrics=[accuracy()])
def verdict(judge: FakeJudge | None = None):
    async def score(state: TaskState, target: Target) -> Score:
        if state.metadata.get("unscored"):
            return Score.unscored()

        if judge is not None:
            value = await asyncio.to_thread(judge.grade, "answer")
        else:
            value = CORRECT if state.metadata.get("epoch_passes", True) else INCORRECT
        return Score(value=value, metadata={"usage": USAGE})

    return score


def make_sample(sample_id: str, **metadata) -> Sample:
    return Sample(id=sample_id, input="question", target="answer", metadata=metadata)


def run_task(
    log_dir: Path,
    samples: list[Sample],
    *,
    solver,
    scorer=None,
    epochs: int | Epochs = AT_LEAST_3,
) -> EvalLog:
    task = Task(
        dataset=samples,
        solver=solver,
        scorer=scorer or verdict(),
        epochs=epochs,
    )

    [log] = eval(
        task,
        model="mockllm/model",
        log_dir=str(log_dir),
        fail_on_error=False,
        retry_on_error=0,
        max_samples=8,
        display="none",
    )

    return read_eval_log(log.location)


def accuracy_of(log: EvalLog) -> float:
    return log.results.scores[0].metrics["accuracy"].value


def overlapping(spans: list[tuple[float, float]]) -> bool:
    ordered = sorted(spans)
    return any(
        later_start < earlier_end
        for (_, earlier_end), (later_start, _) in pairwise(ordered)
    )


def test_it_should_exclude_an_errored_sample_from_the_reduced_score_denominator(
    tmp_path,
):
    samples = [
        make_sample("steady", pass_epochs=3),
        make_sample("wobbly", pass_epochs=2),
        make_sample("boom", raises=True),
    ]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert accuracy_of(log) == pytest.approx(0.5)
    assert log.results.scores[0].scored_samples == 2


def test_it_should_keep_an_errored_sample_in_the_log_with_its_error(tmp_path):
    samples = [make_sample("steady", pass_epochs=3), make_sample("boom", raises=True)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    boom = [s for s in log.samples if s.id == "boom"]
    assert len(boom) == 3
    assert all(
        s.error is not None and "sample exploded" in s.error.message for s in boom
    )


def test_it_should_expose_the_raw_score_of_each_epoch_in_the_log(tmp_path):
    samples = [make_sample("wobbly", pass_epochs=2)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    epochs = sorted(log.samples, key=lambda s: s.epoch)
    values = [s.scores["verdict"].value for s in epochs]
    assert values == [CORRECT, CORRECT, INCORRECT]


def test_it_should_exclude_an_unscored_sample_from_accuracy(tmp_path):
    samples = [
        make_sample("steady", pass_epochs=3),
        make_sample("wobbly", pass_epochs=2),
        make_sample("unable", pass_epochs=3, unscored=True),
    ]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert accuracy_of(log) == pytest.approx(0.5)


def test_it_should_not_count_an_unscored_sample_among_the_scored_samples(tmp_path):
    samples = [
        make_sample("steady", pass_epochs=3),
        make_sample("unable", pass_epochs=3, unscored=True),
    ]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert log.results.scores[0].scored_samples == 1
    assert log.results.scores[0].unscored_samples == 1


def test_it_should_pass_a_sample_under_at_least_3_only_when_all_three_epochs_pass(
    tmp_path,
):
    samples = [
        make_sample("steady", pass_epochs=3),
        make_sample("wobbly", pass_epochs=2),
    ]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert accuracy_of(log) == pytest.approx(0.5)


def test_it_should_still_score_a_sample_that_errors_in_only_one_epoch(tmp_path):
    samples = [make_sample("partial", pass_epochs=3, raises_epoch=2)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert log.results.scores[0].scored_samples == 1


def test_it_should_not_pass_under_at_least_3_a_sample_with_an_errored_epoch(tmp_path):
    samples = [make_sample("partial", pass_epochs=3, raises_epoch=2)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert accuracy_of(log) == pytest.approx(0.0)


def test_it_should_finish_the_other_samples_when_one_errors(tmp_path):
    samples = [make_sample("steady", pass_epochs=3), make_sample("boom", raises=True)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    steady = [s for s in log.samples if s.id == "steady"]
    assert log.status == "success"
    assert [s.error for s in steady] == [None, None, None]


def test_it_should_not_retry_an_errored_sample_when_retry_on_error_is_zero(tmp_path):
    calls: list[tuple[str, int]] = []
    samples = [make_sample("boom", raises=True)]

    run_task(tmp_path, samples, solver=scripted(calls))

    assert sorted(calls) == [("boom", 1), ("boom", 2), ("boom", 3)]


def test_it_should_write_no_logs_directory_to_the_working_directory_with_an_explicit_log_dir(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    run_task(
        tmp_path / "out", [make_sample("steady", pass_epochs=3)], solver=scripted([])
    )

    assert not (workdir / "logs").exists()


def test_it_should_run_samples_in_parallel_when_the_solver_awaits_to_thread(tmp_path):
    spans: list[tuple[float, float]] = []
    # every sample waits for the other seven: serialized samples would break the barrier
    barrier = threading.Barrier(8, timeout=5)
    samples = [make_sample(f"s{i}") for i in range(8)]

    run_task(tmp_path, samples, solver=timed(spans, "thread", barrier), epochs=1)

    assert len(spans) == 8 and not barrier.broken


def test_it_should_serialize_samples_when_the_solver_calls_the_sync_function_directly(
    tmp_path,
):
    spans: list[tuple[float, float]] = []
    samples = [make_sample(f"s{i}") for i in range(8)]

    run_task(tmp_path, samples, solver=timed(spans, "direct"), epochs=1)

    assert len(spans) == 8
    assert not overlapping(spans)


def test_it_should_not_serialize_samples_when_the_scorer_awaits_to_thread(tmp_path):
    barrier = threading.Barrier(8, timeout=5)
    judge = FakeJudge(barrier)
    samples = [make_sample(f"s{i}", pass_epochs=3) for i in range(8)]

    run_task(tmp_path, samples, solver=scripted([]), scorer=verdict(judge), epochs=1)

    assert len(judge.spans) == 8 and not barrier.broken


def test_it_should_expose_the_usage_a_scorer_writes_into_score_metadata(tmp_path):
    samples = [make_sample("steady", pass_epochs=3)]

    log = run_task(tmp_path, samples, solver=scripted([]))

    assert [s.scores["verdict"].metadata["usage"] for s in log.samples] == [USAGE] * 3
