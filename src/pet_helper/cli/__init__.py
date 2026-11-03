import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

from pet_helper.cli import eval_command
from pet_helper.cli.eval_command import EvalEnvironment
from pet_helper.conversation import KitError, build_assistant
from pet_helper.gateway import ModelConfigError
from pet_helper.trace import Summary, summarize

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
    try:
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
    finally:
        assistant.close()


def _cell(value: float | None, scale: float = 1, digits: int = 0) -> str:
    return "-" if value is None else f"{value * scale:.{digits}f}"


def _print_summary(table: Summary) -> None:
    print(
        "provider/model  calls  ttft_p50_ms  ttft_p95_ms  ttft_missing  tps  in  out  "
        "cache_read  cache_write  cost_usd  cache_hit_pct"
    )
    for (provider, model), row in sorted(table.models.items()):
        print(
            f"{provider}/{model}  {row.calls}  {_cell(row.ttft_p50, 1000)}  "
            f"{_cell(row.ttft_p95, 1000)}  {row.ttft_missing}  "
            f"{_cell(row.tps_median, digits=1)}  {row.input_tokens}  "
            f"{row.output_tokens}  {row.cache_read_tokens}  {row.cache_write_tokens}  "
            f"{row.cost_usd:.4f}  {_cell(row.cache_hit_rate, 100)}"
        )
    print("tool  calls  error_rate")
    for name, tool in sorted(table.tools.items()):
        print(f"{name}  {tool.calls}  {tool.error_rate:.0%}")
    print(f"fallback  {table.fallback_count}  {table.fallback_rate:.0%}")
    for reason, count in sorted(table.escalations.items()):
        print(f"escalation  {reason}  {count}")
    print("conversation  cost_usd  latency_s")
    for session_id, row in sorted(table.conversations.items()):
        print(f"{session_id}  {row.cost_usd:.4f}  {row.latency_s:.2f}")


def _metrics(directory: Path) -> int:
    paths = sorted(directory.glob("*.jsonl"))
    if not paths:
        print(f"No trace files in {directory}", file=sys.stderr)
        return 1
    _print_summary(summarize(paths))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    eval_environment: EvalEnvironment | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="pet-helper")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("chat")
    commands.add_parser("metrics").add_argument("directory", type=Path)
    eval_command.add_arguments(commands.add_parser("eval"))
    args = parser.parse_args(argv)
    if args.command == "metrics":
        return _metrics(args.directory)
    if args.command == "eval":
        return eval_command.run(args, eval_environment)
    return _chat()
