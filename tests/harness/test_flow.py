"""Runs .claude/hooks/flow.sh (state commands and guards) against real temporary git repos."""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
FLOW = ROOT / ".claude/hooks/flow.sh"
SETTINGS = json.loads((ROOT / ".claude/settings.json").read_text())
ENV = {
    "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}
SLUG = "cats"
INITIAL_STATE = {
    "slug": SLUG,
    "phase": "interview",
    "approved": False,
    "worktree": "",
    "owner": "",
}
PLAN_WITH_TOOLS = "# Plan\n\n## Ferramentas existentes\n\nbm25s\n"
OMC_SPEC = ".omc/specs/deep-interview-cats.md"
OMC_PLAN = ".omc/plans/ralplan-cats.md"


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=ENV,
        check=True,
    )
    return result.stdout.strip()


def _flow(cwd, *args, payload=None):
    # sh exits 2 on a missing script, the same code as a guard denial
    assert FLOW.is_file(), f"{FLOW} does not exist"
    return subprocess.run(
        ["sh", str(FLOW), *args],
        input="" if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**ENV, "CLAUDE_PROJECT_DIR": str(cwd)},
        check=False,
    )


def _guard(name, payload, cwd):
    return _flow(cwd, "guard", name, payload={"cwd": str(cwd), **payload})


def _state_file(repo):
    return repo / ".git/pet-helper-flow.json"


def _read_state(repo):
    return json.loads(_state_file(repo).read_text())


def _write_state(repo, **overrides):
    state = {**INITIAL_STATE, "phase": "plan", **overrides}
    _state_file(repo).write_text(json.dumps(state))
    return state


def _add_worktree(repo, name):
    path = repo.parent / name
    _git(repo, "worktree", "add", "-b", name, str(path))
    (path / "src").mkdir()
    return path


def _push(worktree):
    _git(worktree, "push", "-u", "origin", worktree.name)


def _commit(worktree):
    _git(worktree, "commit", "--allow-empty", "-m", "more")


def _head(worktree):
    return _git(worktree, "rev-parse", "HEAD")


def _write_unit(repo, slug=SLUG):
    spec_dir = repo / "docs/specs" / slug
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec\n")
    (spec_dir / "plan.md").write_text(PLAN_WITH_TOOLS)
    (spec_dir / "architecture.md").write_text("# Architecture\n")
    for omc in (OMC_SPEC, OMC_PLAN):
        (repo / omc).parent.mkdir(parents=True, exist_ok=True)
        (repo / omc).write_text("original\n")


def _missing_lines(result):
    return [line for line in result.stderr.splitlines() if line.startswith("missing:")]


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "init", "-b", "main", str(root))
    _git(root, "commit", "--allow-empty", "-m", "init")
    _git(root, "remote", "add", "origin", str(remote))
    return root


@pytest.fixture
def worktree(repo):
    return _add_worktree(repo, "wt")


# start


def test_it_should_write_the_initial_state_on_start(repo):
    result = _flow(repo, "start", SLUG)

    assert result.returncode == 0
    assert _read_state(repo) == INITIAL_STATE


def test_it_should_write_the_state_in_the_main_checkout_when_started_from_a_worktree(
    repo, worktree
):
    result = _flow(worktree, "start", SLUG)

    assert result.returncode == 0
    assert _read_state(repo) == INITIAL_STATE


def test_it_should_refuse_to_start_a_different_slug_while_a_unit_is_in_flight(repo):
    state = _write_state(repo, slug="dogs")

    result = _flow(repo, "start", SLUG)

    assert result.returncode == 1
    assert result.stderr != ""
    assert _read_state(repo) == state


def test_it_should_keep_the_state_when_started_again_with_the_same_slug(repo):
    state = _write_state(repo, phase="plan", owner="sess-A")

    result = _flow(repo, "start", SLUG)

    assert result.returncode == 0
    assert _read_state(repo) == state


# set


@pytest.mark.parametrize("key", ["phase", "worktree", "owner"])
def test_it_should_set_only_the_given_key(repo, key):
    state = _write_state(repo)

    result = _flow(repo, "set", key, "some-value")

    assert result.returncode == 0
    assert _read_state(repo) == {**state, key: "some-value"}


@pytest.mark.parametrize("key", ["slug", "approved", "nope"])
def test_it_should_reject_setting_an_unsupported_key(repo, key):
    state = _write_state(repo)

    result = _flow(repo, "set", key, "x")

    assert result.returncode == 1
    assert _read_state(repo) == state


def test_it_should_fail_to_set_a_key_without_a_state_file(repo):
    result = _flow(repo, "set", "phase", "plan")

    assert result.returncode == 1
    assert not _state_file(repo).exists()


# get


def test_it_should_print_the_whole_state_as_json_on_get(repo):
    state = _write_state(repo, owner="sess-A")

    result = _flow(repo, "get")

    assert result.returncode == 0
    assert json.loads(result.stdout) == state


def test_it_should_print_the_raw_value_of_one_key_on_get(repo):
    _write_state(repo, phase="plan")

    result = _flow(repo, "get", "phase")

    assert result.returncode == 0
    assert result.stdout.strip() == "plan"


def test_it_should_fail_on_get_without_a_state_file(repo):
    assert _flow(repo, "get").returncode == 1
    assert _flow(repo, "get", "phase").returncode == 1


# approve


def test_it_should_approve_when_every_checklist_item_exists(repo):
    _write_state(repo)
    _write_unit(repo)

    result = _flow(repo, "approve")

    assert result.returncode == 0
    assert _read_state(repo)["approved"] is True


def test_it_should_find_the_repo_root_when_approving_from_a_worktree(repo, worktree):
    _write_state(repo)
    _write_unit(repo)

    result = _flow(worktree, "approve")

    assert result.returncode == 0
    assert _read_state(repo)["approved"] is True


@pytest.mark.parametrize(
    ("relative", "fragment"),
    [
        ("docs/specs/cats/spec.md", "docs/specs/cats/spec.md"),
        ("docs/specs/cats/plan.md", "docs/specs/cats/plan.md"),
        ("docs/specs/cats/architecture.md", "docs/specs/cats/architecture.md"),
        (OMC_SPEC, ".omc/specs"),
        (OMC_PLAN, ".omc/plans"),
    ],
    ids=["spec", "plan", "architecture", "omc-spec", "omc-plan"],
)
def test_it_should_refuse_to_approve_when_an_item_is_missing(repo, relative, fragment):
    _write_state(repo)
    _write_unit(repo)
    (repo / relative).unlink()

    result = _flow(repo, "approve")

    assert result.returncode == 1
    assert any(fragment in line for line in _missing_lines(result))


def test_it_should_refuse_to_approve_when_the_plan_has_no_existing_tools_heading(repo):
    _write_state(repo)
    _write_unit(repo)
    (repo / "docs/specs/cats/plan.md").write_text("# Plan\n\nFerramentas existentes\n")

    result = _flow(repo, "approve")

    assert result.returncode == 1
    assert any("Ferramentas existentes" in line for line in _missing_lines(result))


def test_it_should_not_count_omc_files_whose_name_lacks_the_slug(repo):
    _write_state(repo)
    _write_unit(repo)
    (repo / OMC_SPEC).rename(repo / ".omc/specs/deep-interview-dogs.md")

    result = _flow(repo, "approve")

    assert result.returncode == 1
    assert any(".omc/specs" in line for line in _missing_lines(result))


def test_it_should_list_every_missing_item_on_its_own_line(repo):
    _write_state(repo)
    _write_unit(repo)
    for relative in ("docs/specs/cats/spec.md", "docs/specs/cats/architecture.md"):
        (repo / relative).unlink()
    (repo / OMC_PLAN).unlink()

    result = _flow(repo, "approve")

    lines = _missing_lines(result)
    assert len(lines) == 3
    assert any("docs/specs/cats/spec.md" in line for line in lines)
    assert any("docs/specs/cats/architecture.md" in line for line in lines)
    assert any(".omc/plans" in line for line in lines)


def test_it_should_leave_approved_false_when_an_item_is_missing(repo):
    state = _write_state(repo)
    _write_unit(repo)
    (repo / "docs/specs/cats/spec.md").unlink()

    _flow(repo, "approve")

    assert _read_state(repo) == state


# clear


def test_it_should_delete_the_state_file_on_clear(repo):
    _write_state(repo)

    result = _flow(repo, "clear")

    assert result.returncode == 0
    assert not _state_file(repo).exists()


def test_it_should_succeed_on_clear_when_there_is_no_state_file(repo):
    assert _flow(repo, "clear").returncode == 0


# guard phase


def _register(*types):
    return {
        "tool_name": "mcp__hexlog__register",
        "tool_input": {
            "records": [{"type": t, "target": "cats", "data": {}} for t in types]
        },
    }


@pytest.mark.parametrize(
    "types",
    [("plan",), ("impasse",), ("plan", "impasse"), ("plan", "plan")],
    ids=["phase", "impasse", "phase-and-impasse", "repeated-phase"],
)
def test_it_should_allow_registering_records_of_the_current_phase_or_impasse(
    repo, types
):
    _write_state(repo, phase="plan")

    assert _guard("phase", _register(*types), repo).returncode == 0


@pytest.mark.parametrize(
    "types", [("review",), ("plan", "review")], ids=["only", "mixed"]
)
def test_it_should_deny_a_record_of_another_phase_naming_both_phases(repo, types):
    _write_state(repo, phase="plan")

    result = _guard("phase", _register(*types), repo)

    assert result.returncode == 2
    assert "plan" in result.stderr
    assert "review" in result.stderr


def test_it_should_allow_any_record_type_when_no_unit_is_in_flight(repo):
    assert _guard("phase", _register("review"), repo).returncode == 0


# guard owner


def _owner(session="sess-B", **tool_input):
    return {"session_id": session, "tool_input": tool_input}


OWNER_DENIED = {
    "edit of the worktree path itself": lambda wt, other: _owner(file_path=str(wt)),
    "edit of a file under the worktree": lambda wt, other: _owner(
        file_path=f"{wt}/src/app.py"
    ),
    "notebook under the worktree": lambda wt, other: _owner(
        notebook_path=f"{wt}/nb.ipynb"
    ),
    "command mentioning the worktree": lambda wt, other: _owner(command=f"ls {wt}/src"),
    "cwd at the worktree": lambda wt, other: {**_owner(), "cwd": str(wt)},
    "cwd under the worktree": lambda wt, other: {**_owner(), "cwd": f"{wt}/src"},
}
OWNER_ALLOWED = {
    "edit of the sibling that only shares the prefix": lambda wt, other: _owner(
        file_path=f"{other}/src/app.py"
    ),
    "cwd at the sibling that only shares the prefix": lambda wt, other: {
        **_owner(),
        "cwd": str(other),
    },
    "edit outside the worktree": lambda wt, other: _owner(file_path="/etc/hosts"),
    "command not mentioning the worktree": lambda wt, other: _owner(command="ls /etc"),
    "call from the owner session": lambda wt, other: _owner(
        session="sess-A", file_path=f"{wt}/src/app.py"
    ),
}


@pytest.mark.parametrize("build", OWNER_DENIED.values(), ids=OWNER_DENIED)
def test_it_should_deny_other_sessions_touching_the_owned_worktree(
    repo, worktree, build
):
    _write_state(repo, worktree=str(worktree), owner="sess-A")
    other = _add_worktree(repo, "wt2")

    result = _guard("owner", build(worktree, other), repo)

    assert result.returncode == 2
    assert str(worktree) in result.stderr
    assert "/pr-panel" in result.stderr


@pytest.mark.parametrize("build", OWNER_ALLOWED.values(), ids=OWNER_ALLOWED)
def test_it_should_allow_calls_that_do_not_touch_the_owned_worktree_or_come_from_its_owner(
    repo, worktree, build
):
    _write_state(repo, worktree=str(worktree), owner="sess-A")
    other = _add_worktree(repo, "wt2")

    assert _guard("owner", build(worktree, other), repo).returncode == 0


@pytest.mark.parametrize(
    "overrides",
    [{"owner": ""}, {"worktree": ""}],
    ids=["no-owner", "no-worktree"],
)
def test_it_should_allow_everything_until_both_owner_and_worktree_are_set(
    repo, worktree, overrides
):
    _write_state(repo, **{"worktree": str(worktree), "owner": "sess-A", **overrides})

    result = _guard("owner", _owner(file_path=f"{worktree}/src/app.py"), repo)

    assert result.returncode == 0


def test_it_should_allow_any_call_on_owner_when_no_unit_is_in_flight(repo, worktree):
    result = _guard("owner", _owner(file_path=f"{worktree}/src/app.py"), repo)

    assert result.returncode == 0


# guard verdict


def _review(method, commit_id=None):
    tool_input = {"method": method, "owner": "o", "repo": "r", "pullNumber": 1}
    if commit_id:
        tool_input["commitID"] = commit_id
    return {"tool_input": tool_input}


def test_it_should_allow_creating_a_review_pinned_to_the_pushed_head(repo, worktree):
    _write_state(repo, worktree=str(worktree))
    _push(worktree)

    result = _guard("verdict", _review("create", _head(worktree)), repo)

    assert result.returncode == 0


def test_it_should_allow_submitting_a_review_when_the_worktree_is_pushed(
    repo, worktree
):
    _write_state(repo, worktree=str(worktree))
    _push(worktree)

    assert _guard("verdict", _review("submit_pending"), repo).returncode == 0


@pytest.mark.parametrize("method", ["create", "submit_pending"])
def test_it_should_deny_a_verdict_while_the_worktree_head_is_not_pushed(
    repo, worktree, method
):
    _write_state(repo, worktree=str(worktree))
    _push(worktree)
    _commit(worktree)

    result = _guard("verdict", _review(method, _head(worktree)), repo)

    assert result.returncode == 2


@pytest.mark.parametrize("method", ["create", "submit_pending"])
def test_it_should_deny_a_verdict_while_the_worktree_branch_has_no_upstream(
    repo, worktree, method
):
    _write_state(repo, worktree=str(worktree))

    result = _guard("verdict", _review(method, _head(worktree)), repo)

    assert result.returncode == 2


def test_it_should_deny_creating_a_review_pinned_to_another_commit(repo, worktree):
    _write_state(repo, worktree=str(worktree))
    _push(worktree)
    stale = _head(worktree)
    _commit(worktree)
    _git(worktree, "push")

    result = _guard("verdict", _review("create", stale), repo)

    assert result.returncode == 2


def test_it_should_allow_other_review_methods_even_when_unpushed(repo, worktree):
    _write_state(repo, worktree=str(worktree))

    result = _guard("verdict", _review("delete_pending"), repo)

    assert result.returncode == 0


def test_it_should_allow_a_verdict_when_no_unit_is_in_flight(repo, worktree):
    result = _guard("verdict", _review("create", _head(worktree)), repo)

    assert result.returncode == 0


def test_it_should_allow_a_verdict_when_the_state_has_no_worktree(repo):
    _write_state(repo, worktree="")

    assert _guard("verdict", _review("submit_pending"), repo).returncode == 0


# guard approval


def _skill(name):
    return {"tool_name": "Skill", "tool_input": {"skill": name}}


@pytest.mark.parametrize(
    "skill",
    ["ralph", "team", "oh-my-claudecode:ralph", "oh-my-claudecode:team"],
)
def test_it_should_deny_delivery_skills_until_the_plan_is_approved(repo, skill):
    _write_state(repo, approved=False)

    assert _guard("approval", _skill(skill), repo).returncode == 2


@pytest.mark.parametrize(
    "skill",
    ["ralplan", "oh-my-claudecode:ralplan", "owner-seat", "deep-interview"],
)
def test_it_should_allow_planning_skills_before_the_plan_is_approved(repo, skill):
    _write_state(repo, approved=False)

    assert _guard("approval", _skill(skill), repo).returncode == 0


@pytest.mark.parametrize("skill", ["ralph", "oh-my-claudecode:team"])
def test_it_should_allow_delivery_skills_once_the_plan_is_approved(repo, skill):
    _write_state(repo, approved=True)

    assert _guard("approval", _skill(skill), repo).returncode == 0


def test_it_should_allow_delivery_skills_when_no_unit_is_in_flight(repo):
    assert _guard("approval", _skill("ralph"), repo).returncode == 0


# guard prd


def _prd(tmp_path, content, name=".omc/prd.json"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return {"tool_input": {"file_path": str(path)}}


def _valid_prd(**overrides):
    return {
        "userStories": [{"id": "US-1"}],
        "feedbackCommands": [
            'out=$(uv run pytest -q 2>&1) || { echo "$out"; exit 1; }'
        ],
        **overrides,
    }


@pytest.mark.parametrize(
    "feedback",
    [
        ["out=$(uv run pytest -q 2>&1) || exit 1"],
        [{"command": "out=$(uv run pytest -q 2>&1) || exit 1"}],
        ["out=$(a) || exit 1", {"command": "out=$(b) || exit 1"}],
    ],
    ids=["strings", "objects", "mixed"],
)
def test_it_should_allow_a_prd_with_stories_and_silent_feedback_commands(
    tmp_path, feedback
):
    payload = _prd(tmp_path, _valid_prd(feedbackCommands=feedback))

    assert _guard("prd", payload, tmp_path).returncode == 0


@pytest.mark.parametrize(
    "prd",
    [
        _valid_prd(userStories=[]),
        {"feedbackCommands": _valid_prd()["feedbackCommands"]},
        _valid_prd(feedbackCommands=[]),
        {"userStories": [{"id": "US-1"}]},
        _valid_prd(feedbackCommands=["uv run pytest -q"]),
        _valid_prd(feedbackCommands=[{"command": "uv run pytest -q"}]),
        _valid_prd(feedbackCommands=["out=$(a) || exit 1", "uv run pytest -q"]),
    ],
    ids=[
        "empty-stories",
        "no-stories",
        "empty-feedback",
        "no-feedback",
        "noisy-string-command",
        "noisy-object-command",
        "one-noisy-command",
    ],
)
def test_it_should_deny_a_prd_that_is_incomplete_or_has_a_noisy_feedback_command(
    tmp_path, prd
):
    result = _guard("prd", _prd(tmp_path, prd), tmp_path)

    assert result.returncode == 2


@pytest.mark.parametrize(
    "name",
    ["prd.json", "docs/prd.json", ".omc/notes.json"],
    ids=["outside-omc", "nested-outside-omc", "other-file-in-omc"],
)
def test_it_should_ignore_files_that_are_not_an_omc_prd(tmp_path, name):
    payload = _prd(tmp_path, "{}", name=name)

    assert _guard("prd", payload, tmp_path).returncode == 0


# settings.json wiring


def _handlers():
    return [
        (event, entry.get("matcher"), handler)
        for event, entries in SETTINGS["hooks"].items()
        for entry in entries
        for handler in entry["hooks"]
    ]


@pytest.mark.parametrize(
    ("event", "matcher", "guard"),
    [
        ("PreToolUse", "mcp__hexlog__register", "phase"),
        ("PreToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit", "owner"),
        ("PreToolUse", "mcp__github-official__pull_request_review_write", "verdict"),
        ("PreToolUse", "Skill", "approval"),
        ("PostToolUse", "Edit|Write|MultiEdit", "prd"),
    ],
)
def test_it_should_register_each_guard_under_its_event_and_matcher(
    event, matcher, guard
):
    pattern = rf"flow\.sh\"? guard {guard}\b"

    wired = [
        h
        for e, m, h in _handlers()
        if e == event and m == matcher and re.search(pattern, h["command"])
    ]

    assert len(wired) == 1


def test_it_should_block_the_tool_call_when_any_hook_handler_fails():
    offenders = [
        (event, handler["command"])
        for event, _, handler in _handlers()
        if handler.get("onFailure") != "block"
    ]

    assert offenders == []
