"""Reject commit messages that are not a single Conventional Commits subject line."""

import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "chore", "docs", "test", "refactor")
SUBJECT = re.compile(rf"^({'|'.join(TYPES)})(\([a-z0-9-]+\))?!?: \S.*$")
MAX_LENGTH = 80
SCISSORS = "# ------------------------ >8 ------------------------"


def check(message: str) -> str | None:
    """Return why the message is invalid, or None when it is valid."""
    lines = []
    for line in message.splitlines():
        if line == SCISSORS:
            break
        if not line.startswith("#"):
            lines.append(line)
    lines = [line for line in lines if line.strip()]
    if not lines:
        return "empty commit message"
    subject = lines[0]
    if not SUBJECT.match(subject):
        return f"subject must be '<type>(<scope>)?: <description>' with type in {', '.join(TYPES)}"
    if len(subject) > MAX_LENGTH:
        return f"subject has {len(subject)} characters; the limit is {MAX_LENGTH}"
    if len(lines) > 1:
        return "commit message must be the subject line only, without a body"
    return None


def main() -> int:
    error = check(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if error:
        print(f"commit-msg: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
