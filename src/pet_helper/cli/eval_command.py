import argparse
import csv
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pet_helper.catalog import Catalog
from pet_helper.conversation import DEFAULT_KIT_DIR, KitError
from pet_helper.evals import (
    EvalCase,
    ask_confirmation,
    check_reference,
    load_cases,
    validate_structure,
)
from pet_helper.gateway import (
    ModelConfigError,
    ModelGateway,
    build_gateway,
    resolve_provider,
)
from pet_helper.trace import find_raw_pii

if TYPE_CHECKING:
    # the runner and the judge import inspect_ai, which loading the CLI must not do
    from pet_helper.evals import (
        BatchClient,
        Estimate,
        GitState,
        JudgeClient,
        RoundResult,
    )

HELD_OUT_ENV = "PET_HELPER_HELD_OUT"
DEFAULT_RUNS = 3
# a decisive rerun is more runs of one case, decided against the latest round of its suite
_DECISIVE_ABOVE_RUNS = DEFAULT_RUNS
_EXIT_USAGE = 2
_EXIT_PII = 3


@dataclass(frozen=True)
class EvalEnvironment:
    cases_dir: Path
    held_out_cases_dir: Path
    results_root: Path
    held_out_results_root: Path
    gateway_factory: Callable[[EvalCase, int], ModelGateway]
    kit_dir: Path
    git_state: "Callable[[], GitState]"
    clock: Callable[[], datetime]
    judge_client: "JudgeClient | None" = None
    # None means the Batches API is unusable, so the judge stays sync-only
    batch_client: "BatchClient | None" = None
    batch_wait_seconds: float = 600.0
    batch_poll_seconds: float = 30.0
    budget_usd: Decimal = Decimal(5)


def default_environment() -> EvalEnvironment:
    from pet_helper.evals import (
        AnthropicBatchClient,
        AnthropicJudgeClient,
        current_git_state,
        utc_now,
    )

    return EvalEnvironment(
        cases_dir=Path("evals/cases"),
        held_out_cases_dir=Path("evals/held_out/cases"),
        results_root=Path("evals/results"),
        held_out_results_root=Path("evals/held_out/results"),
        gateway_factory=lambda case, epoch: build_gateway(),
        kit_dir=Path(DEFAULT_KIT_DIR),
        git_state=current_git_state,
        clock=utc_now,
        judge_client=AnthropicJudgeClient(),
        batch_client=AnthropicBatchClient(),
    )


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite")
    parser.add_argument("--case")
    parser.add_argument("--judge", choices=["sync", "batch", "none"], default="sync")
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--runs", type=_positive_int, default=DEFAULT_RUNS)
    saved = parser.add_mutually_exclusive_group()
    saved.add_argument("--collect", metavar="ROUND")
    saved.add_argument("--rejudge", metavar="ROUND")


def run(args: argparse.Namespace, environment: EvalEnvironment | None) -> int:
    held_out = args.held_out
    if held_out and os.environ.get(HELD_OUT_ENV) != "1":
        print(f"--held-out needs {HELD_OUT_ENV}=1", file=sys.stderr)
        return _EXIT_USAGE
    try:
        return _dispatch(args, environment or default_environment())
    except (KitError, ModelConfigError) as exc:
        print(type(exc).__name__ if held_out else exc, file=sys.stderr)
        return 1
    except Exception as exc:
        if not held_out:
            raise
        # the message of an exception could carry the content of a reserved case
        print(type(exc).__name__, file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, env: EvalEnvironment) -> int:
    if (args.judge == "batch" or args.collect) and env.batch_client is None:
        print("The Batches API is unusable, the judge stays sync-only", file=sys.stderr)
        return _EXIT_USAGE
    held_out = args.held_out
    cases_dir = env.held_out_cases_dir if held_out else env.cases_dir
    root = env.held_out_results_root if held_out else env.results_root
    if args.collect or args.rejudge:
        return _saved_round(args, env, cases_dir, root)
    return _new_round(args, env, cases_dir, root)


def _confirm_estimate(estimate: "Estimate") -> bool:
    basis = f", from round {estimate.basis_round}" if estimate.basis_round else ""
    print(f"Estimate: US$ {estimate.total_usd:.4f} ({estimate.source}{basis})")
    return ask_confirmation("Run? [y/N] ")


def _problem(case: EvalCase, kit_dir: Path, held_out: bool) -> str | None:
    # a reserved case reports only its id and an error code, never its content
    if errors := validate_structure(case.model_dump(exclude_none=True)):
        return f"{case.id}: {', '.join(errors)}"
    if case.reference is None or held_out:
        return None
    result = check_reference(case, Catalog(kit_dir / "catalog.csv").skus)
    if result.outcome == "pass":
        return None
    failed = ", ".join(result.failed_checks) or result.outcome
    return f"{case.id}: the reference fails {failed}"


def _reference_round(root: Path, suite: str | None) -> str | None:
    if not root.is_dir():
        return None
    for directory in sorted(root.iterdir(), reverse=True):
        path = directory / "cases.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as file:
            if any(row["suite"] == (suite or "") for row in csv.DictReader(file)):
                return directory.name
    return None


def _new_round(
    args: argparse.Namespace, env: EvalEnvironment, cases_dir: Path, root: Path
) -> int:
    from pet_helper.evals import run_round

    selection = [
        case
        for case in load_cases(cases_dir)
        if args.suite in (None, case.suite) and args.case in (None, case.id)
    ]
    if not selection:
        print(f"Unknown case: {args.case}" if args.case else f"No case in {args.suite}")
        return 1
    held_out = args.held_out
    problems = [_problem(case, env.kit_dir, held_out) for case in selection]
    if any(problems):
        print("\n".join(filter(None, problems)), file=sys.stderr)
        return 1

    smoke = args.runs == 1
    # a smoke run keeps its traces out of the results root; a reserved one must stay under it
    results_root = (
        Path(tempfile.mkdtemp(prefix="pet-helper-smoke-"))
        if smoke and not held_out
        else root
    )
    decisive = args.case is not None and args.runs > _DECISIVE_ABOVE_RUNS
    reference = _reference_round(root, selection[0].suite) if decisive else None
    if reference and not held_out:
        print(f"Decisive rerun against round {reference}")
    result = run_round(
        selection,
        runs=args.runs,
        judge=args.judge,
        budget_usd=env.budget_usd,
        results_root=results_root,
        gateway_factory=env.gateway_factory,
        confirm=_confirm_estimate,
        judge_client=env.judge_client,
        batch_client=env.batch_client,
        kit_dir=env.kit_dir,
        provider=resolve_provider(),
        reference_round=reference,
        smoke=smoke,
        git_state=env.git_state,
        clock=env.clock,
        batch_wait_seconds=env.batch_wait_seconds,
        batch_poll_seconds=env.batch_poll_seconds,
    )
    if result.aborted or result.round_id is None:
        return 1
    return _finish(result, results_root / result.round_id, held_out)


def _saved_round(
    args: argparse.Namespace, env: EvalEnvironment, cases_dir: Path, root: Path
) -> int:
    from pet_helper.evals import collect_round, rejudge_round

    name = args.collect or args.rejudge
    directory = root / name
    if not (directory / "round.json").is_file():
        print(f"Unknown round: {name}", file=sys.stderr)
        return 1
    cases = load_cases(cases_dir)
    if args.collect:
        # _dispatch already rejected the missing client
        batch = cast("BatchClient", env.batch_client)
        try:
            result = collect_round(directory, cases, batch)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
    else:
        judge = env.judge_client
        if judge is None:
            from pet_helper.evals import AnthropicJudgeClient

            judge = AnthropicJudgeClient()
        result = rejudge_round(
            directory,
            cases,
            judge,
            confirm=_confirm_estimate,
            budget_usd=env.budget_usd,
        )
    if result.aborted:
        return 1
    return _finish(result, directory, args.held_out)


def _finish(result: "RoundResult", directory: Path, held_out: bool) -> int:
    saved = json.loads((directory / "round.json").read_text(encoding="utf-8"))
    smoke = saved["smoke"] and not held_out
    if smoke:
        print(json.dumps({"smoke": True, "runs": saved["runs"]}))
    for case in result.cases:
        print(f"{case.case_id} {case.k if held_out else case.k_display}")
    if not held_out:
        if saved.get("batch_id"):
            print(
                f"Judge batch {saved['batch_id']} is pending, "
                f"run --collect {saved['round_id']} to complete the round"
            )
        if result.decision and saved["reference_round"]:
            decision = result.decision
            print(
                f"Decision against round {saved['reference_round']}: "
                f"{decision.verdict}, {decision.passes}/{decision.valid_runs} valid runs passed"
            )
    if result.stop_line:
        print(result.stop_line, file=sys.stderr)
    findings = find_raw_pii(directory)
    if findings:
        names = ", ".join(sorted({finding.path.name for finding in findings}))
        print(f"Raw PII found in {names}, pii_check is fail", file=sys.stderr)
    if held_out:
        print(saved["git_sha"])
    elif smoke:
        print(directory)
    if findings:
        return _EXIT_PII
    return 1 if result.stop_reason else 0
