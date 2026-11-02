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


def _run(command, payload, held_out=False, path="/usr/bin:/bin"):
    env = {"CLAUDE_PROJECT_DIR": str(ROOT), "PATH": path}
    if held_out:
        env["PET_HELPER_HELD_OUT"] = "1"
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=payload if isinstance(payload, str) else json.dumps(payload),
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
    (
        "grep-excluding-another-folder-while-naming-the-word",
        _bash("grep -rn secret . --exclude-dir=.git -e held_out"),
    ),
    (
        "find-excluding-another-path-while-naming-the-word",
        _bash("find . -not -path '*/.git/*' -name held_out"),
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
    ("git-grep-without-pathspec", _bash("git grep -n x")),
    ("git-grep-on-evals", _bash("git grep -n x -- evals")),
    ("git-show-a-commit", _bash("git show a189cfd")),
    ("git-show-with-stat", _bash("git --no-pager show --stat HEAD~1")),
    ("git-log-with-patch", _bash("git log -p -- evals")),
    ("git-log-short-patch-flag", _bash("git log -3p")),
    ("git-diff-between-revisions", _bash("git diff main...HEAD")),
    ("git-diff-in-another-checkout", _bash("git -C /w/pet-helper.x diff main HEAD")),
    ("git-cat-file", _bash("git cat-file -p HEAD:evals")),
    ("git-archive", _bash("git archive HEAD")),
    ("rg-unrestricted", _bash("rg -uu x .")),
    ("rg-no-ignore", _bash("rg --no-ignore x evals")),
    ("rg-unrestricted-without-path", _bash("rg -n -u x")),
    ("ls-recursive-on-evals", _bash("ls -R evals")),
    ("ls-recursive-on-dot", _bash("ls -laR .")),
    ("tree-on-evals", _bash("tree evals")),
    ("tree-without-path", _bash("tree")),
    ("glob-under-evals", _bash("cat evals/h*/cases/*")),
    ("glob-star-under-evals", _bash("head evals/*/cases/x.yaml")),
    ("pwd-prefixed-path", _bash("grep -rn x $PWD/evals")),
    ("braced-pwd-prefixed-path", _bash("grep -rn x ${PWD}")),
    ("dot-dot-path", _bash("grep -rn x ./evals/../evals")),
    ("absolute-project-dir", _bash(f"grep -rn x {ROOT}")),
    ("absolute-evals-of-another-checkout", _bash("find /w/pet-helper.x/evals")),
    (
        "github-file-contents",
        _tool(
            "mcp__github-official__get_file_contents",
            owner="o",
            repo="pet-helper",
            path="evals/held_out/cases/x.yaml",
        ),
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
    (
        "grep-excluding-the-folder-while-searching-another-word",
        _bash("grep -rn secret . --exclude-dir=held_out"),
    ),
    (
        "find-excluding-the-folder-by-path-pattern",
        _bash("find . -not -path '*/held_out/*' -name '*.py'"),
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
    ("git-grep-on-src", _bash("git grep -n x -- src tests")),
    ("git-show-a-file-of-the-tree", _bash("git show HEAD:README.md")),
    ("git-show-with-pathspec", _bash("git show HEAD -- src")),
    ("git-log-without-patch", _bash("git log --oneline -5")),
    ("git-log-patch-on-src", _bash("git log -p -- src/pet_helper")),
    ("git-diff-of-the-working-tree", _bash("git diff")),
    ("git-diff-cached", _bash("git diff --cached --stat")),
    ("git-diff-with-pathspec", _bash("git diff main...HEAD -- src tests docs")),
    ("git-status", _bash("git status --short")),
    ("rg-on-src", _bash("rg -n x src")),
    ("rg-honouring-the-ignore-file", _bash("rg -n x .")),
    ("ls-on-evals", _bash("ls evals")),
    ("ls-recursive-on-src", _bash("ls -R src")),
    ("tree-on-src", _bash("tree src")),
    ("glob-of-the-visible-cases", _bash("cat evals/cases/*.yaml")),
    ("absolute-src-of-the-project", _bash(f"grep -rn x {ROOT}/src")),
    (
        "github-pull-request-metadata",
        _tool(
            "mcp__github-official__pull_request_read",
            method="get",
            owner="o",
            repo="pet-helper",
            pullNumber=7,
        ),
    ),
)


@pytest.mark.parametrize(
    "tool_name",
    [
        "WebFetch",
        "mcp__github-official__get_file_contents",
        "mcp__github-official__pull_request_read",
        "mcp__gitnexus__query",
    ],
)
def test_it_should_apply_the_held_out_hook_to_the_hosted_read_tools(tool_name):
    assert re.fullmatch(HELD_OUT_MATCHER, tool_name)


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Glob", "tool_input": {"glob": 1}},
        {"tool_name": "Bash", "tool_input": ["not", "an", "object"]},
        "not json at all",
    ],
)
def test_it_should_block_when_the_held_out_filter_cannot_read_the_payload(payload):
    result = _run(HELD_OUT, payload)

    assert result.returncode == 2
    assert "Held-out" in result.stderr


def test_it_should_block_when_jq_is_missing_from_the_path():
    result = _run(HELD_OUT, _bash("ls src"), path="/nonexistent")

    assert result.returncode == 2
    assert "Held-out" in result.stderr


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


PAID_HOOK = next(
    (
        entry
        for entry in PRE_TOOL_USE
        if any("paid-command.jq" in hook["command"] for hook in entry["hooks"])
    ),
    None,
)


def _run_paid(payload, held_out=False, path="/usr/bin:/bin"):
    assert PAID_HOOK, "settings.json wires no PreToolUse hook that runs paid-command.jq"
    return _run(PAID_HOOK["hooks"][0]["command"], payload, held_out=held_out, path=path)


@pytest.mark.parametrize("payload", ["{", ""])
def test_it_should_block_when_the_paid_filter_cannot_read_the_payload(payload):
    assert _run_paid(payload).returncode == 2


def test_it_should_block_a_shell_command_when_jq_is_missing_from_the_path():
    assert _run_paid(_bash("ls src"), path="/nonexistent").returncode == 2


@pytest.mark.parametrize(
    "tool_name",
    [
        "Bash",
        f"{CTX}ctx_execute",
        f"{CTX}ctx_execute_file",
        f"{CTX}ctx_batch_execute",
        f"{OMC}python_repl",
    ],
)
def test_it_should_apply_the_paid_command_hook_to_every_shell_tool(tool_name):
    assert PAID_HOOK, "settings.json wires no PreToolUse hook that runs paid-command.jq"
    assert re.fullmatch(PAID_HOOK["matcher"], tool_name)


PAID_BLOCKED = _ids(
    ("eval", _bash("uv run pet-helper eval")),
    ("eval-fed-by-yes", _bash("yes | uv run pet-helper eval")),
    ("eval-rejudge", _bash("uv run pet-helper eval --rejudge r1")),
    ("eval-collect", _bash("uv run pet-helper eval --collect r1")),
    ("eval-from-the-venv", _bash(".venv/bin/pet-helper eval")),
    ("eval-inside-sh-c", _bash("sh -c 'pet-helper eval'")),
    ("judge-smoke", _bash("uv run python -m pet_helper.evals.judge smoke")),
    (
        "labels-judge-pool",
        _bash("uv run python -m pet_helper.evals.labels judge-pool r1 --split dev"),
    ),
    (
        "ctx-execute-shell",
        _tool(f"{CTX}ctx_execute", language="shell", code="uv run pet-helper eval"),
    ),
    (
        "ctx-batch-execute-command",
        _tool(
            f"{CTX}ctx_batch_execute",
            commands=[
                {"label": "ok", "command": "ls src"},
                {"label": "paid", "command": "yes | uv run pet-helper eval"},
            ],
        ),
    ),
    (
        "python-repl-code",
        _tool(f"{OMC}python_repl", code="import os; os.system('pet-helper eval')"),
    ),
)

PAID_ALLOWED = _ids(
    ("pytest", _bash("uv run pytest -q --cov")),
    ("pytest-one-file", _bash("uv run pytest tests/unit/test_eval_cli.py -q")),
    (
        "analysis-report",
        _bash("uv run python -m pet_helper.evals.analysis report r1"),
    ),
    (
        "labels-present",
        _bash("uv run python -m pet_helper.evals.labels present r1 --n 10"),
    ),
    (
        "labels-record",
        _bash("uv run python -m pet_helper.evals.labels record r1 labels.json"),
    ),
    (
        "labels-tpr-tnr",
        _bash("uv run python -m pet_helper.evals.labels tpr-tnr r1 --split dev"),
    ),
    (
        "labels-seed",
        _bash("uv run python -m pet_helper.evals.labels seed r1 --seed 3"),
    ),
    ("labels-pool", _bash("uv run python -m pet_helper.evals.labels pool r1")),
    ("grep-the-word-judge", _bash("grep -n judge src/pet_helper/evals/judge.py")),
    ("chat", _bash("uv run pet-helper chat")),
    ("metrics", _bash("uv run pet-helper metrics traces/")),
    (
        "ctx-execute-unrelated-code",
        _tool(f"{CTX}ctx_execute", language="shell", code="uv run pytest -q"),
    ),
)


@pytest.mark.parametrize("payload", PAID_BLOCKED)
def test_it_should_block_every_spelling_of_a_paid_command(payload):
    result = _run_paid(payload)

    assert result.returncode == 2


@pytest.mark.parametrize("agent", ["", "oh-my-claudecode:executor", "zeus-verifier-x"])
def test_it_should_block_a_paid_command_for_the_main_session_and_every_subagent(agent):
    result = _run_paid(_bash("uv run pet-helper eval", agent=agent))

    assert result.returncode == 2


def test_it_should_block_a_paid_command_in_a_session_started_for_the_held_out_cases():
    result = _run_paid(_bash("uv run pet-helper eval"), held_out=True)

    assert result.returncode == 2


def test_it_should_tell_the_agent_that_the_owner_runs_paid_commands_in_their_own_terminal():
    result = _run_paid(_bash("uv run pet-helper eval"))

    assert "own terminal" in result.stderr


@pytest.mark.parametrize("payload", PAID_ALLOWED)
def test_it_should_not_block_free_commands_that_share_a_paid_module_or_word(payload):
    assert _run_paid(payload).returncode == 0
