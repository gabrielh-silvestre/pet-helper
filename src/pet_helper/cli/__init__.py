import argparse
import sys
from collections.abc import Sequence
from uuid import uuid4

from pet_helper.conversation import KitError, build_assistant
from pet_helper.gateway import ModelConfigError


def _chat() -> int:
    try:
        return _converse()
    except (KitError, ModelConfigError) as exc:
        print(exc, file=sys.stderr)
        return 1


def _converse() -> int:
    assistant = build_assistant()
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
                return 0
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
