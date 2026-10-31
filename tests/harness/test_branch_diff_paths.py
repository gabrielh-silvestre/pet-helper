"""Checks a branch never carries the reserved eval cases in its PR diff."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RESERVED = "evals/held_out/"


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def _base():
    # CI checks out a detached head, so only origin/main may exist there.
    for ref in ("main", "origin/main"):
        if _git("rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


@pytest.mark.skipif(
    _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main",
    reason="the branch diff is empty on main",
)
def test_it_should_list_no_reserved_path_in_the_branch_diff():
    base = _base()
    if base is None:
        pytest.skip("no main ref to diff against")

    diff = _git("diff", "--name-only", f"{base}...HEAD")

    assert diff.returncode == 0
    assert [p for p in diff.stdout.splitlines() if p.startswith(RESERVED)] == []
