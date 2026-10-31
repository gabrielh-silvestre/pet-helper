import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from pet_helper.evals.cases import load_cases
from pet_helper.evals.judge import current_verdicts
from pet_helper.evals.round import _ANALYSIS_COLUMNS as ANNOTATION_COLUMNS
from pet_helper.evals.round import DEFAULT_RUNS, CaseResult
from pet_helper.evals.round import _read_runs as read_runs
from pet_helper.evals.stats import wilson_interval

DEFAULT_CASES_DIR = Path("evals/cases")
_HELD_OUT = "held_out"
_EXIT_REFUSED = 1


# reading a round


def _runs_of(directory: Path) -> int:
    if _HELD_OUT in directory.resolve().parts:
        raise ValueError(f"{directory}: a held-out round is never analysed")
    saved = json.loads((directory / "round.json").read_text(encoding="utf-8"))
    if saved["judge_pending"]:
        raise ValueError(f"{directory}: the judge is pending, collect it first")
    return saved["runs"]


def _read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def _case_result(row: dict[str, str], runs: int) -> CaseResult:
    passes, made = (int(part) for part in row[f"k_of_{runs}"].split("/"))
    unable = int(row["unable_runs"])
    outcomes = [
        *["pass"] * passes,
        *["unable"] * unable,
        *["fail"] * max(0, made - passes - unable),
        *["not_run"] * (runs - made),
    ]
    return CaseResult(
        case_id=row["case_id"],
        suite=row["suite"] or None,
        runs=runs,
        outcomes=outcomes,
        failed_checks=[name for name in row["failed_checks"].split(";") if name],
        unable_reasons=[name for name in row["unable_reasons"].split(";") if name],
        error_runs=[],
        cost_usd=Decimal(row["cost_usd"] or 0),
        judge_pending=False,
    )


def _results(directory: Path, runs: int) -> list[CaseResult]:
    _, rows = _read_table(directory / "cases.csv")
    return [_case_result(row, runs) for row in rows]


# categories


def _categories(cases_csv: Path) -> list[str]:
    runs = _runs_of(cases_csv.parent)
    if runs != DEFAULT_RUNS:
        return [f"(a rerun of {runs} runs has no categories)"]
    _, rows = _read_table(cases_csv)
    failed: Counter[str] = Counter()
    unable = 0
    for row in rows:
        result = _case_result(row, runs)
        if result.passed:
            continue
        # a case that only lacks runs because they were unable is not a failure to categorise
        if not result.failed_checks and result.unable_runs:
            unable += 1
        else:
            failed[row["category"] or "(none)"] += 1
    return [
        *(f"{name}: {count}" for name, count in sorted(failed.items())),
        f"unable: {unable}",
        f"total: {sum(failed.values())}",
    ]


# annotate


def _annotate(cases_csv: Path, notes_json: Path) -> list[str]:
    _runs_of(cases_csv.parent)
    notes = json.loads(notes_json.read_text(encoding="utf-8"))
    fieldnames, rows = _read_table(cases_csv)
    known = {row["case_id"] for row in rows}
    problems = [
        f"unknown case id {case_id}" for case_id in notes if case_id not in known
    ]
    problems += [
        f"{column} is not an annotation column"
        for cells in notes.values()
        for column in cells
        if column not in ANNOTATION_COLUMNS
    ]
    if problems:
        raise ValueError("; ".join(problems))
    with cases_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({**row, **notes.get(row["case_id"], {})} for row in rows)
    return [f"annotated {len(notes)} cases"]


# report


def _block(header: str, items: Sequence[str]) -> list[str]:
    return [header, *items, ""] if items else []


def _interval(passes: int, made: int) -> str:
    low, high = wilson_interval(passes, made)
    return f"{low:.2f} to {high:.2f}"


def _summary(results: list[CaseResult], runs: int) -> list[str]:
    note = f"note: the {runs} runs of a case are correlated, so per-case intervals are optimistic"
    if runs != DEFAULT_RUNS:
        cases = []
        for result in results:
            passes, made = (int(part) for part in result.k.split("/"))
            cases.append(
                f"{result.case_id} {result.k_display} ({_interval(passes, made)})"
            )
        return [note, *cases]
    passing = sum(result.passed for result in results)
    lines = [
        f"pass^3: {passing}/{len(results)} cases (95% Wilson interval {_interval(passing, len(results))})",
        note,
    ]
    if results and passing == len(results):
        lines.append(
            "note: saturated suite, every case passes 3/3: regression evidence only"
        )
    return [*lines, *(f"{result.case_id} {result.k_display}" for result in results)]


def _check_pass_rates(directory: Path, cases_dir: Path) -> list[str]:
    runs = read_runs(directory)
    if not runs:
        return []
    cases = {case.id: case for case in load_cases(cases_dir)}
    trials: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    for run in runs:
        case = cases.get(run.case_id)
        if case is None or run.outcome not in ("pass", "fail"):
            continue
        labels = Counter(
            f"{check.type}@{check.turn}" if check.turn else check.type
            for check in case.checks
        )
        for name, count in labels.items():
            trials[name] += count
            passed[name] += count - run.failed_checks.count(name)
    return _block(
        "pass rate per check:",
        [f"{name} {passed[name]}/{trials[name]}" for name in sorted(trials)],
    )


def _unable_runs(results: list[CaseResult]) -> list[str]:
    reasons = [reason for result in results for reason in result.unable_reasons]
    return [
        f"off_script unable runs: {sum(r.startswith('off_script:') for r in reasons)}",
        f"judge unable runs: {sum(r.startswith('judge:') for r in reasons)}",
        "",
    ]


def _criterion_unable_rates(directory: Path) -> list[str]:
    verdicts = list(current_verdicts(directory / "judge.jsonl").values())
    totals = Counter(record["criterion"] for record in verdicts)
    unable = Counter(r["criterion"] for r in verdicts if r["verdict"] == "unable")
    return _block(
        "unable rate per criterion:",
        [f"{name} {unable[name]}/{totals[name]}" for name in sorted(totals)],
    )


def _off_script_runs(results: list[CaseResult]) -> list[str]:
    return _block(
        "off_script runs:",
        [
            f"{result.case_id} {reason.removeprefix('off_script:')}"
            for result in results
            for reason in result.unable_reasons
            if reason.startswith("off_script:")
        ],
    )


def _paired(previous: Path, results: list[CaseResult], runs: int) -> list[str]:
    if _runs_of(previous) != runs:
        raise ValueError(f"{previous}: the rounds ran a different number of runs")
    before = {result.case_id: result for result in _results(previous, runs)}
    pairs = [(before[r.case_id], r) for r in results if r.case_id in before]
    graduates = (
        [
            f"{new.case_id} {new.k} in both rounds"
            for old, new in pairs
            if old.passed and new.passed
        ]
        if runs == DEFAULT_RUNS
        else []
    )
    return [
        f"improved: {sum(not old.passed and new.passed for old, new in pairs)}",
        f"regressed: {sum(old.passed and not new.passed for old, new in pairs)}",
        "",
        *_block("graduation candidates:", graduates),
    ]


def _report(directory: Path, previous: Path | None, cases_dir: Path) -> list[str]:
    runs = _runs_of(directory)
    results = _results(directory, runs)
    return [
        *_summary(results, runs),
        "",
        *_check_pass_rates(directory, cases_dir),
        *_unable_runs(results),
        *_criterion_unable_rates(directory),
        *_off_script_runs(results),
        *(_paired(previous, results, runs) if previous else []),
    ]


# command line


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pet_helper.evals.analysis")
    commands = parser.add_subparsers(required=True)
    categories = commands.add_parser("categories")
    categories.add_argument("cases_csv", type=Path)
    categories.set_defaults(run=lambda args: _categories(args.cases_csv))
    annotate = commands.add_parser("annotate")
    annotate.add_argument("cases_csv", type=Path)
    annotate.add_argument("notes_json", type=Path)
    annotate.set_defaults(run=lambda args: _annotate(args.cases_csv, args.notes_json))
    report = commands.add_parser("report")
    report.add_argument("round_dir", type=Path)
    report.add_argument("--previous", type=Path)
    report.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    report.set_defaults(
        run=lambda args: _report(args.round_dir, args.previous, args.cases_dir)
    )
    args = parser.parse_args(argv)
    try:
        lines = args.run(args)
    except (KeyError, OSError, ValueError) as error:
        print(" ".join(f"{error}".split()), file=sys.stderr)
        return _EXIT_REFUSED
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
