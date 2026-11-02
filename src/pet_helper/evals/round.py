import csv
import json
import os
import subprocess
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import anthropic
from inspect_ai.log import EvalSample, read_eval_log
from inspect_ai.model import ChatMessage

from pet_helper.conversation import DEFAULT_KIT_DIR, system_prompt_sha256
from pet_helper.evals import judge as judge_module
from pet_helper.evals.budget import (
    BATCH_PRICE_FACTOR,
    JUDGE_INPUT_LIMIT_TOKENS,
    JUDGE_MAX_TOKENS,
    BudgetExceeded,
    RoundBudget,
    batch_reserve_usd,
    worst_case_usd,
)
from pet_helper.evals.cases import EvalCase, JudgeEntry
from pet_helper.evals.judge import (
    AnthropicBatchClient,
    AnthropicJudgeClient,
    BatchClient,
    BatchItem,
    BatchNotEnded,
    JudgeClient,
    Judged,
    JudgeReplyError,
    Transcript,
    TranscriptTurn,
    Verdict,
    append_verdict,
    build_prompt,
    build_slice,
    current_verdicts,
    verdict_from_reply,
    wait_for_batch,
)
from pet_helper.evals.judge import _judge_charged as judge_charged
from pet_helper.evals.task import (
    JUDGE_UNPARSABLE,
    GatewayFactory,
    _with_judge_verdicts,
    eval_task,
    run_eval,
)
from pet_helper.gateway import (
    FALLBACK_ENV,
    resolve_anthropic_model,
    resolve_ollama_model,
)
from pet_helper.orders import FAILURE_ENV
from pet_helper.trace import CONTENT_ENV, UnknownPriceError, find_raw_pii, read_jsonl

KIT_DIR = Path(DEFAULT_KIT_DIR)
DEFAULT_RUNS = 3
# the architecture premises: about US$0.4 for 108 Haiku turns of the baseline round
DEFAULT_TURN_COST_USD = Decimal("0.004")
# a judge call costs about US$0.007 in sync (premises of the plan)
JUDGE_CALL_ESTIMATE_USD = Decimal("0.007")
ANTHROPIC_CONCURRENCY = 8
OLLAMA_CONCURRENCY = 1
JUDGE_SLOTS = 4

# the rounds run with the fallback off, the trace content on and no simulated order API failure
# (None: unset), whatever the caller has set
_ROUND_ENVIRONMENT = {FALLBACK_ENV: "none", CONTENT_ENV: "1", FAILURE_ENV: None}
# what a round runs: a change to any of these makes its results unreproducible from the commit
_WATCHED_PATHS = ("src", "evals/cases", "evals/fixtures", "kit", "pyproject.toml")
_COLUMNS = (
    "case_id",
    "suite",
    "k_of_{runs}",
    "first_failure_note",
    "category",
    "human_label",
    "cost_usd",
    "failed_checks",
    "unable_runs",
    "unable_reasons",
    "error_runs",
)
# the analysis writes these cells; a recomputation of the round keeps them
_ANALYSIS_COLUMNS = ("first_failure_note", "category", "human_label")
# only a run that ended pass or fail has a reply for the judge to read
_JUDGEABLE = ("pass", "fail")
_VALID = ("pass", "fail")


@dataclass(frozen=True)
class GitState:
    sha: str
    dirty: bool


@dataclass(frozen=True)
class Estimate:
    total_usd: Decimal
    source: str
    basis_round: str | None = None


@dataclass(frozen=True)
class Decision:
    valid_runs: int
    passes: int
    verdict: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    suite: str | None
    runs: int
    outcomes: list[str]
    failed_checks: list[str]
    unable_reasons: list[str]
    error_runs: list[str]
    cost_usd: Decimal
    judge_pending: bool

    @property
    def passes(self) -> int:
        return self.outcomes.count("pass")

    @property
    def errors(self) -> int:
        return self.outcomes.count("error")

    @property
    def unable_runs(self) -> int:
        return self.outcomes.count("unable")

    @property
    def k(self) -> str:
        made = len(self.outcomes) - self.outcomes.count("not_run")
        return f"{self.passes}/{made}"

    @property
    def k_display(self) -> str:
        notes = []
        if self.unable_runs:
            reasons = ", ".join(self.unable_reasons)
            notes.append(f"{self.unable_runs} unable: {reasons}")
        if self.errors:
            notes.append(f"{self.errors} error")
        return f"{self.k} ({'; '.join(notes)})" if notes else self.k

    @property
    def passed(self) -> bool:
        return self.passes == self.runs


@dataclass(frozen=True)
class RoundResult:
    round_id: str | None
    estimate: Estimate
    aborted: bool = False
    cases: list[CaseResult] = field(default_factory=list)
    stop_reason: str | None = None
    stop_line: str | None = None
    decision: Decision | None = None


@dataclass(frozen=True)
class _Run:
    case_id: str
    suite: str | None
    epoch: int
    # what the checks left, before any judge verdict
    outcome: str
    failed_checks: list[str]
    reason: str | None
    entries: list[JudgeEntry]
    transcript: Transcript | None
    # the inline judge answered something unreadable: an error until a rejudge reads it again
    unparsed: bool = False

    @property
    def session_id(self) -> str:
        return f"{self.case_id}-e{self.epoch}"


@dataclass(frozen=True)
class _Collected:
    cases: list[CaseResult]
    total_cost: Decimal
    judge_hash: str | None
    judge_pending: bool
    price_unknown: bool

    @property
    def not_run(self) -> int:
        return sum(case.outcomes.count("not_run") for case in self.cases)


def _git(*args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def current_git_state() -> GitState:
    # the paths are relative to the repository root, whatever the working directory
    root = _git("rev-parse", "--show-toplevel")
    changed = _git("-C", root, "status", "--porcelain", "--", *_WATCHED_PATHS)
    return GitState(_git("rev-parse", "HEAD"), bool(changed))


@contextmanager
def _round_environment() -> Iterator[None]:
    saved = {name: os.environ.get(name) for name in _ROUND_ENVIRONMENT}
    for name, value in _ROUND_ENVIRONMENT.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# estimate


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _estimate(
    results_root: Path, selection: Sequence[EvalCase], runs: int, judge: str
) -> Estimate:
    criteria = sum(len(case.judge) for case in selection)
    judge_usd = JUDGE_CALL_ESTIMATE_USD * criteria * runs
    if judge == "none":
        judge_usd = Decimal(0)
    elif judge == "batch":
        judge_usd *= BATCH_PRICE_FACTOR
    saved = (
        [path for path in results_root.iterdir() if (path / "cases.csv").is_file()]
        if results_root.is_dir()
        else []
    )
    if saved:
        last = max(saved, key=lambda path: path.name)
        costs = [
            Decimal(row["cost_usd"])
            for row in _read_rows(last / "cases.csv")
            if row["cost_usd"]
        ]
        if costs:
            info = last / "round.json"
            saved_info = (
                json.loads(info.read_text(encoding="utf-8")) if info.is_file() else {}
            )
            saved_runs = saved_info.get("runs", DEFAULT_RUNS)
            mean = sum(costs, Decimal(0)) / len(costs)
            total = mean * len(selection) * runs / saved_runs
            # a round saved without the judge has no judge cost in its mean
            if saved_info.get("judge_mode") == "none":
                total += judge_usd
            return Estimate(total, "last_round", last.name)
    turns = sum(len(case.turns) for case in selection)
    return Estimate(DEFAULT_TURN_COST_USD * turns * runs + judge_usd, "default")


# reading a round back


def _without_judge(
    outcome: str, failed_checks: list[str], reason: str | None
) -> tuple[str, list[str], str | None]:
    # the inline scorer folds the judge into the score: undo it, judge.jsonl is the source
    checks = [name for name in failed_checks if not name.startswith("judge:")]
    if outcome == "fail" and not checks:
        return "pass", checks, None
    if outcome == "unable" and (reason or "").startswith("judge:"):
        return "pass", checks, None
    return outcome, checks, reason


def _transcript_of(sample: EvalSample) -> Transcript:
    record = sample.store["record"]
    user_turns = cast(list[ChatMessage], sample.input)
    return Transcript(
        turns=[
            TranscriptTurn(user.text, reply["text"], tuple(titles))
            for user, reply, titles in zip(
                user_turns, record["replies"], record["policy_titles"], strict=True
            )
        ],
        escalation=record["replies"][-1]["escalation"],
    )


def _run_of(sample: EvalSample, epoch_offset: int) -> _Run:
    outcome, failed_checks, reason = "error", [], None
    unparsed = False
    if sample.error is None:
        score = cast(
            Mapping[str, Any], next(iter((sample.scores or {}).values())).metadata
        )
        unparsed = score["reason"] == JUDGE_UNPARSABLE
        outcome, failed_checks, reason = _without_judge(
            score["outcome"],
            score["failed_checks"],
            None if unparsed else score["reason"],
        )
    metadata = sample.metadata
    return _Run(
        case_id=str(sample.id),
        suite=metadata["suite"],
        epoch=sample.epoch + epoch_offset,
        outcome=outcome,
        failed_checks=failed_checks,
        reason=reason,
        entries=[
            JudgeEntry.model_validate(
                {"criterion": entry} if isinstance(entry, str) else entry
            )
            for entry in metadata["judge"]
        ],
        transcript=_transcript_of(sample) if outcome in _JUDGEABLE else None,
        unparsed=unparsed,
    )


def _read_runs(directory: Path) -> list[_Run]:
    runs = []
    for path in sorted(directory.glob("*.eval")):
        log = read_eval_log(str(path))
        offset = (log.eval.metadata or {}).get("epoch_offset", 0)
        runs.extend(_run_of(sample, offset) for sample in log.samples or [])
    return sorted(runs, key=lambda run: (run.case_id, run.epoch))


def _model_calls(directory: Path, session_id: str) -> list[dict[str, Any]]:
    path = directory / "traces" / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    return [event for event in read_jsonl(path) if event["event"] == "model.call"]


def _judge_costs(path: Path) -> dict[str, Decimal]:
    costs: dict[str, Decimal] = defaultdict(Decimal)
    for record in read_jsonl(path):
        costs[record["run_id"]] += Decimal(record["cost_usd"])
    return costs


def _unjudged(run: _Run, verdicts: Mapping[tuple[str, str], Any]) -> bool:
    return run.outcome in _JUDGEABLE and any(
        (run.session_id, entry.criterion) not in verdicts for entry in run.entries
    )


def _collect(directory: Path, runs: int) -> _Collected:
    log_path = directory / "judge.jsonl"
    verdicts = current_verdicts(log_path)
    judge_costs = _judge_costs(log_path)
    by_case: dict[str, list[_Run]] = defaultdict(list)
    for run in _read_runs(directory):
        by_case[run.case_id].append(run)
    cases = []
    price_unknown = False
    for case_id, case_runs in by_case.items():
        finals = []
        cost = Decimal(0)
        for run in case_runs:
            judged = [
                (
                    entry.criterion,
                    Verdict(
                        saved["critique"],
                        saved["verdict"],
                        saved["usage"],
                        Decimal(saved["cost_usd"]),
                    ),
                )
                for entry in run.entries
                if (saved := verdicts.get((run.session_id, entry.criterion)))
            ]
            final = _with_judge_verdicts(
                run.outcome, run.failed_checks, run.reason, judged
            )
            # a reply the judge made unreadable counts as an error until a verdict is in
            finals.append(
                ("error", [], None)
                if run.unparsed and _unjudged(run, verdicts)
                else final
            )
            calls = _model_calls(directory, run.session_id)
            price_unknown |= any(call.get("pet_helper.price_unknown") for call in calls)
            cost += judge_costs[run.session_id] + sum(
                (
                    Decimal(repr(call["pet_helper.cost_usd"]))
                    for call in calls
                    if call.get("pet_helper.cost_usd") is not None
                ),
                Decimal(0),
            )
        cases.append(
            CaseResult(
                case_id=case_id,
                suite=case_runs[0].suite,
                runs=runs,
                outcomes=[outcome for outcome, _, _ in finals],
                failed_checks=list(
                    dict.fromkeys(
                        name
                        for outcome, names, _ in finals
                        if outcome == "fail"
                        for name in names
                    )
                ),
                unable_reasons=[
                    str(reason) for outcome, _, reason in finals if outcome == "unable"
                ],
                error_runs=[
                    run.session_id
                    for run, (outcome, _, _) in zip(case_runs, finals, strict=True)
                    if outcome == "error"
                ],
                cost_usd=cost,
                judge_pending=any(_unjudged(run, verdicts) for run in case_runs),
            )
        )
    hashes = {record["judge_prompt_sha256"] for record in verdicts.values()}
    return _Collected(
        cases=sorted(cases, key=lambda case: case.case_id),
        total_cost=sum((case.cost_usd for case in cases), Decimal(0)),
        judge_hash=next(iter(hashes)) if len(hashes) == 1 else None,
        judge_pending=len(hashes) > 1 or any(case.judge_pending for case in cases),
        price_unknown=price_unknown,
    )


# writing a round


def _write_cases(directory: Path, runs: int, cases: Sequence[CaseResult]) -> None:
    columns = [name.format(runs=runs) for name in _COLUMNS]
    path = directory / "cases.csv"
    written = (
        {row["case_id"]: row for row in _read_rows(path)} if path.is_file() else {}
    )
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for case in cases:
            kept = written.get(case.case_id, {})
            writer.writerow(
                {
                    **dict.fromkeys(columns, ""),
                    **{name: kept.get(name, "") for name in _ANALYSIS_COLUMNS},
                    "case_id": case.case_id,
                    "suite": case.suite or "",
                    columns[2]: case.k,
                    "cost_usd": str(case.cost_usd),
                    "failed_checks": ";".join(case.failed_checks),
                    "unable_runs": str(case.unable_runs),
                    "unable_reasons": ";".join(case.unable_reasons),
                    "error_runs": str(case.errors),
                }
            )


def _finalize(directory: Path, runs: int, fields: Mapping[str, Any]) -> _Collected:
    collected = _collect(directory, runs)
    _write_cases(directory, runs, collected.cases)
    saved = {
        **fields,
        "judge_prompt_sha256": collected.judge_hash,
        "judge_pending": collected.judge_pending,
        "total_cost_usd": str(collected.total_cost),
        "pii_check": "fail" if find_raw_pii(directory) else "pass",
    }
    (directory / "round.json").write_text(
        json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return collected


def _judge_model(
    judge: str, judge_client: JudgeClient | None, batch_client: BatchClient | None
) -> str | None:
    if judge == "none":
        return None
    client = judge_client if judge == "sync" else batch_client
    return client.model if client is not None else judge_module._resolve_model()


def _stop(
    budget: RoundBudget,
    collected: _Collected,
    capped: bool,
    directory: Path,
    judge_error: anthropic.APIError | None = None,
) -> tuple[str | None, str | None]:
    if judge_error is not None:
        # the class and the status only: the body of the error may quote the held-out cases
        status = getattr(judge_error, "status_code", None)
        reason, line = (
            "judge_error",
            f"Round stopped: the judge failed with {type(judge_error).__name__}"
            + (f" (status {status})" if status else ""),
        )
    elif budget.halted:
        if collected.price_unknown or budget.price_unknown:
            reason, line = (
                "unknown_price",
                "Round stopped: a model call has no known price",
            )
        else:
            reason, line = "no_usage", "Round stopped: a call ended without usage"
    elif capped or collected.not_run:
        reason, line = "cap", f"Round stopped at the cap of US$ {budget.cap}"
    else:
        return None, None
    return (
        reason,
        f"{line}; {collected.not_run} runs not run, results saved in {directory}",
    )


# judge


def _judge_items(
    runs: Sequence[_Run], cases_by_id: Mapping[str, EvalCase]
) -> list[tuple[Judged, JudgeEntry]]:
    return [
        (Judged(run.session_id, cases_by_id[run.case_id], run.transcript), entry)
        for run in runs
        if run.case_id in cases_by_id
        and run.outcome in _JUDGEABLE
        and run.transcript is not None
        for entry in run.entries
    ]


def _missing_verdicts(
    items: Sequence[tuple[Judged, JudgeEntry]], log_path: Path
) -> list[tuple[Judged, JudgeEntry]]:
    current = current_verdicts(log_path)
    prompt_hash = judge_module.judge_prompt_sha256()
    return [
        (judged, entry)
        for judged, entry in items
        if current.get((judged.run_id, entry.criterion), {}).get("judge_prompt_sha256")
        != prompt_hash
    ]


def _judge_sync(
    items: Sequence[tuple[Judged, JudgeEntry]],
    client: JudgeClient,
    log_path: Path,
    budget: RoundBudget,
) -> tuple[bool, anthropic.APIError | None]:
    failures: list[anthropic.APIError] = []
    failed = threading.Event()

    def judge_one(item: tuple[Judged, JudgeEntry]) -> bool:
        if failed.is_set():
            return True
        judged, entry = item
        try:
            judge_charged(entry, judged, client, log_path, budget)
        except BudgetExceeded:
            return False
        except anthropic.APIError as error:
            # the provider is failing: the next calls would only fail and bill the reserve
            failures.append(error)
            failed.set()
            return True
        except UnknownPriceError:
            budget.halted = budget.price_unknown = True
            return False
        except JudgeReplyError:
            # the run stays without a verdict, so it stays pending
            return True
        return True

    with ThreadPoolExecutor(JUDGE_SLOTS, thread_name_prefix="rejudge") as pool:
        within_budget = all(list(pool.map(judge_one, items)))
    return within_budget, failures[0] if failures else None


def _batch_ids(
    items: Sequence[tuple[Judged, JudgeEntry]],
) -> dict[str, tuple[Judged, JudgeEntry]]:
    return {f"item-{index}": item for index, item in enumerate(items)}


def _settle_batch(
    client: BatchClient,
    batch_id: str,
    ids: Mapping[str, tuple[Judged, JudgeEntry]],
    log_path: Path,
    budget: RoundBudget,
    wait_seconds: float,
    poll_seconds: float,
) -> bool:
    collection = wait_for_batch(
        client,
        batch_id,
        list(ids),
        wait_seconds,
        poll_seconds,
        retry_on=(BatchNotEnded, anthropic.APIError),
    )
    if collection is None:
        return False
    cost = Decimal(0)
    for item_id, (text, usage) in collection.completions.items():
        judged, entry = ids[item_id]
        try:
            verdict = verdict_from_reply(text, usage, client.model)
        except JudgeReplyError as error:
            # the reply was billed even though it cannot be read
            cost += (error.cost or Decimal(0)) * BATCH_PRICE_FACTOR
            continue
        except UnknownPriceError:
            budget.halted = budget.price_unknown = True
            break
        verdict = replace(verdict, cost_usd=verdict.cost_usd * BATCH_PRICE_FACTOR)
        append_verdict(log_path, judged.run_id, entry.criterion, verdict)
        cost += verdict.cost_usd
    budget.on_cost(cost)
    return not budget.price_unknown


def _judge_batch(
    items: Sequence[tuple[Judged, JudgeEntry]],
    client: BatchClient,
    log_path: Path,
    budget: RoundBudget,
    wait_seconds: float,
    poll_seconds: float,
) -> str | None:
    # the worst case is held from the submission until the collection settles the real cost
    budget.before_call(
        batch_reserve_usd(len(items), JUDGE_MAX_TOKENS, model=client.model)
    )
    ids = _batch_ids(items)
    batch_id = client.submit(
        [
            BatchItem(
                item_id,
                *build_prompt(
                    entry.criterion,
                    build_slice(entry.criterion, judged.transcript, entry.turn),
                ),
            )
            for item_id, (judged, entry) in ids.items()
        ]
    )
    ended = _settle_batch(
        client, batch_id, ids, log_path, budget, wait_seconds, poll_seconds
    )
    return None if ended else batch_id


# the rounds


def run_round(
    selection: Sequence[EvalCase],
    runs: int = DEFAULT_RUNS,
    judge: str = "sync",
    budget_usd: Decimal = Decimal(5),
    *,
    results_root: Path,
    gateway_factory: GatewayFactory,
    confirm: Callable[[Estimate], bool],
    judge_client: JudgeClient | None = None,
    batch_client: BatchClient | None = None,
    kit_dir: Path = KIT_DIR,
    provider: str = "anthropic",
    assistant_model: str | None = None,
    max_samples: int | None = None,
    reference_round: str | None = None,
    smoke: bool = False,
    git_state: Callable[[], GitState] = current_git_state,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    round_id: str | None = None,
    batch_wait_seconds: float = 0.0,
    batch_poll_seconds: float = 0.0,
) -> RoundResult:
    if reference_round is not None and len(selection) != 1:
        raise ValueError("A decisive rerun takes exactly one case")
    judge_model = _judge_model(judge, judge_client, batch_client)
    if judge_model is not None:
        # an unpriced judge must fail here, before the conversations are paid for
        worst_case_usd(judge_model, JUDGE_INPUT_LIMIT_TOKENS, JUDGE_MAX_TOKENS)
    estimate = _estimate(results_root, selection, runs, judge)
    if not confirm(estimate):
        return RoundResult(round_id, estimate, aborted=True)

    git = git_state()
    started = clock()
    if round_id is None:
        stamp = started.strftime("%Y-%m-%dT%H%M%S")
        round_id, suffix = stamp, 1
        while (results_root / round_id).exists():
            suffix += 1
            round_id = f"{stamp}-{suffix}"
    directory = results_root / round_id
    directory.mkdir(parents=True)
    log_path = directory / "judge.jsonl"
    log_path.touch()
    inline_judge = judge_client or AnthropicJudgeClient() if judge == "sync" else None
    batch = batch_client or AnthropicBatchClient() if judge == "batch" else None
    if max_samples is None:
        max_samples = (
            ANTHROPIC_CONCURRENCY if provider == "anthropic" else OLLAMA_CONCURRENCY
        )
    budget = RoundBudget(budget_usd)

    capped = False
    judge_error = None
    batch_id = None
    with (
        _round_environment(),
        ThreadPoolExecutor(
            max_samples + JUDGE_SLOTS, thread_name_prefix="eval"
        ) as pool,
    ):
        made = 0
        count = runs
        while True:
            task = eval_task(
                selection,
                round_dir=directory,
                kit_dir=kit_dir,
                gateway_factory=gateway_factory,
                executor=pool,
                runs=count,
                judge_client=inline_judge,
                budget=budget,
                epoch_offset=made,
            )
            run_eval(task, round_dir=directory, max_samples=max_samples)
            made += count
            if reference_round is None:
                break
            collected = _collect(directory, runs)
            valid = sum(o in _VALID for case in collected.cases for o in case.outcomes)
            # an unable or errored run is retried, up to as many extra runs as the round asked for
            if valid >= runs or budget.halted or collected.not_run or made >= 2 * runs:
                break
            count = min(runs - valid, 2 * runs - made)
    if batch is not None:
        items = _judge_items(
            _read_runs(directory), {case.id: case for case in selection}
        )
        try:
            batch_id = (
                _judge_batch(
                    items,
                    batch,
                    log_path,
                    budget,
                    batch_wait_seconds,
                    batch_poll_seconds,
                )
                if items
                else None
            )
        except BudgetExceeded:
            capped = True
        except anthropic.APIError as error:
            # the submission failed: the conversations are paid for, so the round is saved pending
            judge_error = error

    fields = {
        "round_id": round_id,
        "git_sha": git.sha,
        "git_dirty": git.dirty,
        "models": {
            "assistant": assistant_model
            or (
                resolve_anthropic_model()
                if provider == "anthropic"
                else resolve_ollama_model()
            ),
            "judge": judge_model,
        },
        "assistant_prompt_sha256": system_prompt_sha256(),
        "started_at": started.isoformat(),
        "finished_at": clock().isoformat(),
        "runs": runs,
        "smoke": smoke,
        "judge_mode": judge,
        "reference_round": reference_round,
        **({"batch_id": batch_id} if batch_id else {}),
    }
    collected = _finalize(directory, runs, fields)
    stop_reason, stop_line = _stop(budget, collected, capped, directory, judge_error)
    decision = None
    if reference_round is not None:
        [case] = collected.cases
        valid = sum(o in _VALID for o in case.outcomes)
        decision = Decision(
            valid, case.passes, "decided" if valid >= runs else "no_verdict"
        )
    return RoundResult(
        round_id, estimate, False, collected.cases, stop_reason, stop_line, decision
    )


def rejudge_round(
    round_dir: Path,
    selection: Sequence[EvalCase],
    judge_client: JudgeClient,
    *,
    confirm: Callable[[Estimate], bool],
    budget_usd: Decimal = Decimal(5),
) -> RoundResult:
    saved = json.loads((round_dir / "round.json").read_text(encoding="utf-8"))
    log_path = round_dir / "judge.jsonl"
    items = _missing_verdicts(
        _judge_items(_read_runs(round_dir), {case.id: case for case in selection}),
        log_path,
    )
    estimate = Estimate(JUDGE_CALL_ESTIMATE_USD * len(items), "default")
    if not confirm(estimate):
        return RoundResult(saved["round_id"], estimate, aborted=True)

    budget = RoundBudget(budget_usd)
    within_budget, judge_error = _judge_sync(items, judge_client, log_path, budget)
    fields = {
        **saved,
        "models": {**saved["models"], "judge": judge_client.model},
        "judge_mode": "sync",
    }
    collected = _finalize(round_dir, saved["runs"], fields)
    stop_reason, stop_line = _stop(
        budget, collected, not within_budget, round_dir, judge_error
    )
    return RoundResult(
        saved["round_id"], estimate, False, collected.cases, stop_reason, stop_line
    )


def collect_round(
    round_dir: Path,
    selection: Sequence[EvalCase],
    batch_client: BatchClient,
    *,
    budget_usd: Decimal = Decimal(5),
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.0,
) -> RoundResult:
    saved = json.loads((round_dir / "round.json").read_text(encoding="utf-8"))
    batch_id = saved.get("batch_id")
    if batch_id is None:
        raise ValueError(f"Round {saved['round_id']} has no pending batch")
    # the same runs in the same order as at submission, so the item ids match
    items = _judge_items(_read_runs(round_dir), {case.id: case for case in selection})
    budget = RoundBudget(budget_usd)
    ended = _settle_batch(
        batch_client,
        batch_id,
        _batch_ids(items),
        round_dir / "judge.jsonl",
        budget,
        wait_seconds,
        poll_seconds,
    )
    fields = {k: v for k, v in saved.items() if not (ended and k == "batch_id")}
    collected = _finalize(round_dir, saved["runs"], fields)
    stop_reason, stop_line = _stop(budget, collected, False, round_dir)
    return RoundResult(
        saved["round_id"],
        Estimate(Decimal(0), "collect"),
        False,
        collected.cases,
        stop_reason,
        stop_line,
    )
