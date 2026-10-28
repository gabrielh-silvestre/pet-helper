import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).parents[2] / ".claude/hooks/guard.py"
)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _bash(command, agent=""):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "agent_type": agent,
    }


def _edit(path, agent="", tool="Edit"):
    return {"tool_name": tool, "tool_input": {"file_path": path}, "agent_type": agent}


@pytest.mark.parametrize(
    "command",
    [
        "git commit --no-verify -m 'feat: x'",
        "git commit -n -m 'feat: x'",
        "git push --no-verify",
        "SKIP=ruff git commit -m 'feat: x'",
        "PREK_SKIP=pyrefly git commit -m 'fix: y'",
        "git config core.hooksPath /dev/null",
    ],
)
def test_it_should_block_hook_bypass(command):
    assert "git hooks" in guard.decide(_bash(command))


@pytest.mark.parametrize(
    "command",
    ["git commit -m 'feat: add x'", "TEST_SKIP=1 uv run pytest", "git commit --amend"],
)
def test_it_should_allow_normal_git_and_shell(command):
    assert guard.decide(_bash(command)) is None


@pytest.mark.parametrize(
    "payload",
    [
        _edit("/repo/docs/directives/estrategia.md"),
        _edit("/repo/docs/directives/estrategia.md", tool="Write"),
        _bash("sed -i 's/a/b/' docs/directives/estrategia.md"),
        _bash("echo x > docs/directives/estrategia.md"),
    ],
)
def test_it_should_block_any_agent_writing_the_strategy(payload):
    assert "Only the owner" in guard.decide(payload)


def test_it_should_allow_reading_the_strategy():
    assert guard.decide(_bash("cat docs/directives/estrategia.md")) is None


@pytest.mark.parametrize(
    "payload",
    [
        _edit("/repo/tests/unit/test_order.py", agent="oh-my-claudecode:executor"),
        _edit("/repo/tests/unit/test_order.py", agent="naruto-executor-fix-order"),
        _bash(
            "sed -i 's/1/2/' tests/unit/test_order.py",
            agent="naruto-executor-fix-order",
        ),
    ],
)
def test_it_should_block_the_executor_from_editing_tests(payload):
    assert "executor does not edit tests" in guard.decide(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _edit("/repo/tests/unit/test_order.py", agent="oh-my-claudecode:test-engineer"),
        _edit(
            "/repo/tests/unit/test_order.py",
            agent="pantera-negra-test-engineer-fix-executor-bug",
        ),
        _edit("/repo/src/order.py", agent="oh-my-claudecode:executor"),
        _bash("uv run pytest tests/unit", agent="oh-my-claudecode:executor"),
    ],
)
def test_it_should_allow_test_engineer_tests_and_executor_code(payload):
    assert guard.decide(payload) is None


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
        _bash("cat evals/held_out/case_01.yaml"),
    ],
)
def test_it_should_hide_held_out_cases_unless_allowed(payload):
    assert "Held-out" in guard.decide(payload)
    assert guard.decide(payload, held_out_allowed=True) is None


def test_it_should_exit_2_with_the_reason_when_blocking(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_bash("git commit --no-verify")))
    )
    monkeypatch.delenv("PET_HELPER_HELD_OUT", raising=False)

    assert guard.main() == 2
    assert "git hooks" in capsys.readouterr().err


def test_it_should_exit_0_when_allowing(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_bash("ls"))))

    assert guard.main() == 0


@pytest.mark.parametrize(
    ("agent_type", "role"),
    [
        ("naruto-executor-fix-order", "executor"),
        ("oh-my-claudecode:executor", "executor"),
        ("pantera-negra-test-engineer-fix-executor-bug", "test-engineer"),
        ("zeus-verifier-check-executor", "verifier"),
        ("Explore", ""),
        ("", ""),
    ],
)
def test_it_should_read_the_role_from_the_type_segment_not_the_slug(agent_type, role):
    assert guard.agent_role(agent_type) == role
