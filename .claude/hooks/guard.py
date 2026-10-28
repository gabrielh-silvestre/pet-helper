"""PreToolUse guard: blocks check bypasses and edits or reads the flow reserves to someone else."""

import json
import os
import re
import sys

STRATEGY = "docs/directives/estrategia.md"
TESTS = "tests/"
HELD_OUT = "evals/held_out/"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}
BYPASS = re.compile(
    r"--no-verify|\bgit\s+commit\b[^|;&]*\s-[a-zA-Z]*n\b|(^|\s)(PREK_)?SKIP=|PRE_COMMIT_ALLOW_NO_CONFIG=|core\.hooksPath"
)
# shortcut: shell writes are spotted by keyword, not parsed; a determined agent can still evade it
# agent_type is the name given at spawn ({name}-{type}-{slug}) or, unnamed, the type
# (oh-my-claudecode:executor); the first known type in it is the role, so a slug word never wins
OMC_TYPES = [
    "executor",
    "architect",
    "critic",
    "planner",
    "analyst",
    "debugger",
    "designer",
    "document-specialist",
    "code-reviewer",
    "code-simplifier",
    "git-master",
    "qa-tester",
    "scientist",
    "security-reviewer",
    "test-engineer",
    "tracer",
    "verifier",
    "writer",
    "explore",
]
ROLE = re.compile(rf"(?:^|[-:])({'|'.join(OMC_TYPES)})(?=-|$)")


def agent_role(agent_type: str) -> str:
    """Return the OMC agent type behind a subagent's agent_type, or '' when none."""
    match = ROLE.search(agent_type)
    return match.group(1) if match else ""


SHELL_WRITE = re.compile(
    r"(>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bperl\s+-i)"
)


def _paths(tool_input: dict) -> list[str]:
    return [
        p
        for p in (
            tool_input.get("file_path"),
            tool_input.get("notebook_path"),
            tool_input.get("path"),
        )
        if p
    ]


def decide(payload: dict, held_out_allowed: bool = False) -> str | None:
    """Return the reason to block the tool call, or None to allow it."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    agent = payload.get("agent_type") or ""
    command = tool_input.get("command", "") if tool == "Bash" else ""
    paths = _paths(tool_input)

    if command and BYPASS.search(command):
        return "Skipping or disabling git hooks is not allowed; fix what the hook reports instead."

    if (tool in EDIT_TOOLS and any(p.endswith(STRATEGY) for p in paths)) or (
        STRATEGY in command and SHELL_WRITE.search(command)
    ):
        return (
            f"Only the owner edits {STRATEGY}; propose the change to the owner instead."
        )

    if agent_role(agent) == "executor" and (
        (tool in EDIT_TOOLS and any(TESTS in p for p in paths))
        or (TESTS in command and SHELL_WRITE.search(command))
    ):
        return "The executor does not edit tests; ask the test-engineer to change them."

    touches_held_out = (
        any(HELD_OUT in p for p in paths)
        or HELD_OUT in command
        or HELD_OUT in tool_input.get("pattern", "")
    )
    if touches_held_out and not held_out_allowed:
        return "Held-out eval cases are hidden from agents that change the assistant; run them only through the accept/reject step."

    return None


def main() -> int:
    reason = decide(
        json.load(sys.stdin),
        held_out_allowed=os.environ.get("PET_HELPER_HELD_OUT") == "1",
    )
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
