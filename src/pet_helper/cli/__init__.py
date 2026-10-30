import argparse
import sys
from collections.abc import Sequence
from uuid import uuid4

from pet_helper.conversation import KitError, build_assistant


def _chat() -> int:
    try:
        assistant = build_assistant()
    except KitError as exc:
        print(exc, file=sys.stderr)
        return 1

    session_id = uuid4().hex
    while True:
        try:
            line = input("> ").strip()
            if line:
                print(assistant.turn(session_id, line).text)
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
