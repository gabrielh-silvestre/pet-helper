"""Runs the PreToolUse hook commands of .claude/settings.json against sample payloads."""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PRE_TOOL_USE = json.loads((ROOT / ".claude/settings.json").read_text())["hooks"][
    "PreToolUse"
]
EXECUTOR_TESTS, HELD_OUT = (PRE_TOOL_USE[i]["hooks"][0]["command"] for i in (0, 1))
HELD_OUT_MATCHER = PRE_TOOL_USE[1]["matcher"]
CTX = "mcp__plugin_context-mode_context-mode__"
OMC = "mcp__plugin_oh-my-claudecode_t__"


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


def _tool(name, **tool_input):
    return {"tool_name": name, "tool_input": tool_input}


def _ids(*cases):
    return [pytest.param(payload, id=name) for name, payload in cases]


@pytest.mark.parametrize(
    "tool_name",
    [
        "Bash",
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
        "Read",
        "Grep",
        "Glob",
        f"{CTX}ctx_execute",
        f"{CTX}ctx_execute_file",
        f"{CTX}ctx_batch_execute",
        f"{CTX}ctx_index",
        f"{OMC}python_repl",
        f"{OMC}ast_grep_search",
        f"{OMC}ast_grep_replace",
        f"{OMC}lsp_diagnostics",
        f"{OMC}lsp_find_references",
    ],
)
def test_it_should_apply_the_held_out_hook_to_every_file_and_shell_tool(tool_name):
    assert re.fullmatch(HELD_OUT_MATCHER, tool_name)


BLOCKED = _ids(
    ("cd-then-relative-cat", _bash("cd evals && cat held_out/cases/x.yaml")),
    (
        "grep-path-with-trailing-slash",
        _tool("Grep", pattern="x", path="evals/held_out/"),
    ),
    (
        "grep-glob-with-path-form",
        _tool("Grep", pattern="x", path="src", glob="held_out/**"),
    ),
    (
        "grep-pattern-with-path-form",
        _tool("Grep", pattern="evals/held_out/", path="src"),
    ),
    ("grep-pattern-with-slash-prefix", _tool("Grep", pattern="/held_out", path="src")),
    ("glob-pattern", _tool("Glob", pattern="**/held_out/**")),
    ("glob-path", _tool("Glob", pattern="*.yaml", path="evals/held_out")),
    ("read-file-path", _tool("Read", file_path="/repo/evals/held_out/cases/x.yaml")),
    ("read-bare-folder", _tool("Read", file_path="/repo/evals/held_out")),
    ("edit-file-path", _edit("/repo/evals/held_out/cases/x.yaml")),
    ("write-file-path", _edit("/repo/evals/held_out/cases/x.yaml", tool="Write")),
    ("write-bare-file-name", _edit("/repo/held_out.yaml", tool="Write")),
    (
        "notebook-path",
        _tool("NotebookEdit", notebook_path="/repo/evals/held_out/n.ipynb"),
    ),
    ("grep-without-path", _tool("Grep", pattern="x")),
    ("grep-on-dot", _tool("Grep", pattern="x", path=".")),
    (
        "grep-on-dot-with-plain-glob",
        _tool("Grep", pattern="x", path=".", glob="*.yaml"),
    ),
    ("grep-on-evals", _tool("Grep", pattern="x", path="evals")),
    ("grep-on-repository-root", _tool("Grep", pattern="x", path=str(ROOT))),
    ("grep-on-absolute-evals", _tool("Grep", pattern="x", path=str(ROOT / "evals"))),
    (
        "ctx-execute-file-path",
        _tool(
            f"{CTX}ctx_execute_file",
            path="evals/held_out/cases/x.yaml",
            language="python",
            code="print(1)",
        ),
    ),
    (
        "ctx-batch-execute-command",
        _tool(
            f"{CTX}ctx_batch_execute",
            commands=[
                {"label": "ok", "command": "ls src"},
                {"label": "cases", "command": "cat evals/held_out/cases/x.yaml"},
            ],
            queries=["cases"],
        ),
    ),
    (
        "ctx-execute-code",
        _tool(f"{CTX}ctx_execute", language="python", code="open('evals/held_out/x')"),
    ),
    (
        "python-repl-code",
        _tool(f"{OMC}python_repl", code="open('evals/held_out/cases/x.yaml').read()"),
    ),
    (
        "ast-grep-path",
        _tool(f"{OMC}ast_grep_search", pattern="x", path="evals/held_out"),
    ),
    ("grep-recursive-on-dot", _bash("grep -r x .")),
    ("grep-recursive-capital-r", _bash("grep -R x .")),
    ("grep-recursive-without-path", _bash("grep -rn x")),
    ("grep-recursive-on-evals", _bash("grep -r x evals")),
    ("find-on-dot", _bash("find . -name '*.yaml'")),
    ("find-on-evals", _bash("find evals -name '*.yaml'")),
    (
        "ctx-batch-execute-recursive-grep",
        _tool(
            f"{CTX}ctx_batch_execute",
            commands=[{"label": "all", "command": "grep -r x ."}],
        ),
    ),
    (
        "ctx-execute-find",
        _tool(f"{CTX}ctx_execute", language="shell", code="find . -name '*.yaml'"),
    ),
)

ALLOWED = _ids(
    (
        "pytest-node-id-with-the-word",
        _bash(
            "uv run pytest tests/harness/test_hooks.py"
            "::test_it_should_allow_paths_outside_the_held_out_cases"
        ),
    ),
    ("pytest-k-expression", _bash("uv run pytest -k held_out")),
    (
        "commit-message-with-the-word",
        _bash('git commit -m "fix: close the held_out hook gap"'),
    ),
    ("grep-pattern-bare-word-on-src", _tool("Grep", pattern="held_out", path="src")),
    ("kebab-flag", _bash("uv run pet-helper eval --held-out")),
    ("upper-case-variable", _bash("PET_HELPER_HELD_OUT=1 uv run pet-helper eval")),
    ("grep-on-src", _tool("Grep", pattern="x", path="src")),
    ("grep-on-tests", _tool("Grep", pattern="x", path="tests", glob="*.py")),
    ("read-source-file", _tool("Read", file_path="/repo/src/pet_helper/graph.py")),
    (
        "read-file-with-the-word-inside-an-identifier",
        _tool("Read", file_path="/repo/tests/unit/test_held_out_cases.py"),
    ),
    (
        "write-content-naming-the-path",
        _tool(
            "Write",
            file_path="/repo/tests/harness/test_x.py",
            content="open('evals/held_out/cases/x.yaml')",
        ),
    ),
    (
        "edit-content-naming-the-path",
        _tool(
            "Edit",
            file_path="/repo/docs/x.md",
            old_string="a",
            new_string="see evals/held_out/",
        ),
    ),
    (
        "multi-edit-content-naming-the-path",
        _tool(
            "MultiEdit",
            file_path="/repo/docs/x.md",
            edits=[{"old_string": "a", "new_string": "see evals/held_out/"}],
        ),
    ),
    (
        "notebook-edit-content-naming-the-path",
        _tool(
            "NotebookEdit",
            notebook_path="/repo/docs/n.ipynb",
            new_source="open('evals/held_out/x')",
        ),
    ),
    (
        "grep-on-dot-excluding-the-folder",
        _tool("Grep", pattern="x", path=".", glob="!evals/held_out/**"),
    ),
    (
        "grep-without-path-excluding-the-folder",
        _tool("Grep", pattern="x", glob="!**/held_out/**"),
    ),
    (
        "grep-recursive-excluding-the-folder",
        _bash("grep -r --exclude-dir=held_out x ."),
    ),
    (
        "find-excluding-the-folder",
        _bash("find . -name '*.yaml' -not -path '*/held_out/*'"),
    ),
    ("grep-recursive-on-src", _bash("grep -r x src")),
    ("find-on-src", _bash("find src -name '*.py'")),
    (
        "ctx-execute-file-outside-the-folder",
        _tool(
            f"{CTX}ctx_execute_file",
            path="src/pet_helper/graph.py",
            language="python",
            code="print(1)",
        ),
    ),
    (
        "ctx-batch-execute-excluding-the-folder",
        _tool(
            f"{CTX}ctx_batch_execute",
            commands=[
                {"label": "all", "command": "grep -r --exclude-dir=held_out x ."}
            ],
        ),
    ),
    ("python-repl-code-without-the-path", _tool(f"{OMC}python_repl", code="print(1)")),
)


@pytest.mark.parametrize("payload", BLOCKED)
def test_it_should_block_every_spelling_of_the_reserved_folder(payload):
    result = _run(HELD_OUT, payload)

    assert result.returncode == 2
    assert "Held-out" in result.stderr


@pytest.mark.parametrize("payload", ALLOWED)
def test_it_should_not_block_legitimate_uses_of_the_word(payload):
    assert _run(HELD_OUT, payload).returncode == 0


@pytest.mark.parametrize("payload", BLOCKED)
def test_it_should_allow_everything_in_a_session_started_for_the_held_out_cases(
    payload,
):
    assert _run(HELD_OUT, payload, held_out=True).returncode == 0


@pytest.mark.parametrize(
    ("payload", "hint"),
    [
        (_bash("grep -r x ."), "--exclude-dir=held_out"),
        (_bash("find . -name '*.yaml'"), "-not -path"),
        (_tool("Grep", pattern="x", path="."), "glob"),
    ],
    ids=["grep-recursive", "find", "grep-tool"],
)
def test_it_should_say_how_to_exclude_the_folder_when_blocking_an_ancestor_search(
    payload, hint
):
    result = _run(HELD_OUT, payload)

    assert hint in result.stderr
