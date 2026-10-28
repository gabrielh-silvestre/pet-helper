"""SubagentStop: when the executor stops, run the full verification and send failures back to it."""

import json
import subprocess
import sys

from guard import agent_role  # same folder: sys.path[0] when run as a script

MAX_LINES = 20
# shortcut: runs every time the executor stops, not only when a spec closes; hooks cannot see spec boundaries
CHECKS = [
    ["uv", "run", "--quiet", "ruff", "format", "--check", "--quiet", "."],
    [
        "uv",
        "run",
        "--quiet",
        "ruff",
        "check",
        "--quiet",
        "--output-format",
        "concise",
        ".",
    ],
    ["uv", "run", "--quiet", "pyrefly", "check"],
    [
        "uv",
        "run",
        "--quiet",
        "pytest",
        "-q",
        "-x",
        "--no-header",
        "--cov",
        "--cov-report=term:skip-covered",
    ],
]


def main() -> int:
    payload = json.load(sys.stdin)
    if agent_role(payload.get("agent_type") or "") != "executor":
        return 0
    for check in CHECKS:
        result = subprocess.run(check, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            lines = [
                line
                for line in (result.stdout + result.stderr).splitlines()
                if line.strip()
            ]
            print(f"Verification failed: {' '.join(check[3:])}", file=sys.stderr)
            print("\n".join(lines[-MAX_LINES:]), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
