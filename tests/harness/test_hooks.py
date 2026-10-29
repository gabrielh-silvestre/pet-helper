"""Runs the PreToolUse hook commands of .claude/settings.json against sample payloads."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PRE_TOOL_USE = json.loads((ROOT / ".claude/settings.json").read_text())["hooks"][
    "PreToolUse"
]
EXECUTOR_TESTS, HELD_OUT = (entry["hooks"][0]["command"] for entry in PRE_TOOL_USE)


def _run(command, payload, held_out=False):
    env = {"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": "/usr/bin:/bin"}
    if held_out:
        env["PET_HELPER_HELD_OUT"] = "1"
    return subprocess.run(
        ["sh", "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _bash(command, agent=""):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "agent_type": agent,
    }


def _edit(path, agent="", tool="Edit"):
    return {"tool_name": tool, "tool_input": {"file_path": path}, "agent_type": agent}


@pytest.mark.parametrize(
    "payload",
    [
        _edit("/repo/tests/unit/test_order.py", agent="oh-my-claudecode:executor"),
        _edit(
            "/repo/tests/unit/test_order.py",
            agent="naruto-executor-fix-order",
            tool="Write",
        ),
        _bash(
            "sed -i 's/1/2/' tests/unit/test_order.py",
            agent="naruto-executor-fix-order",
        ),
    ],
)
def test_it_should_block_the_executor_from_editing_tests(payload):
    result = _run(EXECUTOR_TESTS, payload)

    assert result.returncode == 2
    assert "executor does not edit tests" in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        _edit("/repo/tests/unit/test_order.py", agent="oh-my-claudecode:test-engineer"),
        _edit(
            "/repo/tests/unit/test_order.py",
            agent="pantera-negra-test-engineer-fix-executor-bug",
        ),
        _edit("/repo/tests/unit/test_order.py", agent="zeus-verifier-check-executor"),
        _edit("/repo/tests/unit/test_order.py"),
        _edit("/repo/src/order.py", agent="oh-my-claudecode:executor"),
        _bash("uv run pytest tests/unit", agent="oh-my-claudecode:executor"),
    ],
)
def test_it_should_allow_other_roles_on_tests_and_the_executor_on_code(payload):
    assert _run(EXECUTOR_TESTS, payload).returncode == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "tool_name": "Read",
            "tool_input": {"file_path": "/repo/evals/held_out/case_01.yaml"},
        },
        {
            "tool_name": "Grep",
            "tool_input": {"pattern": "x", "path": "/repo/evals/held_out/"},
        },
        {"tool_name": "Glob", "tool_input": {"pattern": "evals/held_out/**"}},
        _edit("/repo/evals/held_out/case_01.yaml"),
        _bash("cat evals/held_out/case_01.yaml"),
    ],
)
def test_it_should_hide_held_out_cases_unless_the_session_allows_them(payload):
    blocked = _run(HELD_OUT, payload)

    assert blocked.returncode == 2
    assert "Held-out" in blocked.stderr
    assert _run(HELD_OUT, payload, held_out=True).returncode == 0


def test_it_should_allow_paths_outside_the_held_out_cases():
    assert _run(HELD_OUT, _edit("/repo/evals/cases/case_01.yaml")).returncode == 0
