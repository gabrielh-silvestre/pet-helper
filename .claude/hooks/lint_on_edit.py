"""PostToolUse: autofix the edited .py file and report only what ruff could not fix."""

import json
import subprocess
import sys

MAX_ERRORS = 20


def main() -> int:
    payload = json.load(sys.stdin)
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".py"):
        return 0
    subprocess.run(
        ["uv", "run", "--quiet", "ruff", "format", "--quiet", path], check=False
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "ruff",
            "check",
            "--fix",
            "--quiet",
            "--output-format",
            "concise",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    errors = [line for line in result.stdout.splitlines() if line.strip()]
    if not errors:
        return 0
    shown = errors[:MAX_ERRORS]
    if len(errors) > MAX_ERRORS:
        shown.append(f"... and {len(errors) - MAX_ERRORS} more")
    print("\n".join(shown), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
