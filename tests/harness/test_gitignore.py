"""Checks the ignore files keep run output out of git and the reserved folder out of searches."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _is_ignored(path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"}
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "traces/2026-11-03/s.jsonl",
        "logs/x.eval",
        "evals/held_out/results/r1/round.json",
    ],
)
def test_it_should_ignore_run_output_that_must_not_be_committed(path):
    assert _is_ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        "evals/results/r1/traces/s.jsonl",
        "evals/results/r1/round.json",
        "src/pet_helper/traces/sink.py",
        "src/pet_helper/logs/format.py",
    ],
)
def test_it_should_keep_committed_results_and_source_folders_tracked(path):
    assert not _is_ignored(path)


def test_it_should_hide_the_reserved_folder_from_ripgrep_searches():
    lines = (ROOT / ".rgignore").read_text().splitlines()

    assert "evals/held_out/" in lines
