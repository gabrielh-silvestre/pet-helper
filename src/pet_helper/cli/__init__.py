import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

from pet_helper.conversation import KitError, build_assistant
from pet_helper.gateway import ModelConfigError

_TECHNICAL_CAUSES = {"model_unavailable", "refusal"}
_MODEL_UNAVAILABLE_HINT = (
    "Check PET_HELPER_PROVIDER, ANTHROPIC_API_KEY (anthropic) or OLLAMA_BASE_URL "
    "(ollama) and run the command again."
)


def _chat() -> int:
    try:
        return _converse()
    except (KitError, ModelConfigError) as exc:
        print(exc, file=sys.stderr)
        return 1


def _report_escalation(cause: str | None) -> int:
    # só as falhas técnicas vão para stderr; o escalonamento de negócio sai limpo
    if cause not in _TECHNICAL_CAUSES:
        return 0
    print(cause, file=sys.stderr)
    if cause == "model_unavailable":
        print(_MODEL_UNAVAILABLE_HINT, file=sys.stderr)
        return 1
    return 0


def _converse() -> int:
    assistant = build_assistant(trace_dir=Path("traces") / date.today().isoformat())
    session_id = uuid4().hex
    while True:
        try:
            line = input("> ").strip()
            if not line:
                continue
            reply = assistant.turn(session_id, line)
            print(reply.text)
            if reply.escalation:
                print(f"[escalonado: {reply.escalation}]")
                return _report_escalation(assistant.escalation_cause(session_id))
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pet-helper")
    parser.add_subparsers(dest="command", required=True).add_parser("chat")
    parser.parse_args(argv)
    return _chat()
