import argparse
from collections.abc import Sequence

from pet_helper.conversation import Assistant
from pet_helper.gateway import default_gateway


def _chat() -> int:
    assistant = Assistant(default_gateway())
    while True:
        try:
            line = input("> ").strip()
            if line:
                print(assistant.turn("cli", line).text)
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
