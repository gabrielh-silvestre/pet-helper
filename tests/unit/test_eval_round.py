"""The round runner: estimate, budget, outcomes and the results of a round (no real model)."""

import csv
import json
import os
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from inspect_ai.log import read_eval_log

from pet_helper.conversation import system_prompt_sha256
from pet_helper.evals import judge as judge_module
from pet_helper.evals.budget import (
    ASSISTANT_CALL_RESERVE_USD,
    BATCH_PRICE_FACTOR,
    JUDGE_MAX_TOKENS,
    batch_reserve_usd,
)
from pet_helper.evals.cases import EvalCase
from pet_helper.evals.judge import (
    DEFAULT_JUDGE_MODEL,
    BatchCollection,
    BatchNotEnded,
    current_verdicts,
    judge_prompt_sha256,
)
from pet_helper.evals.round import (
    DEFAULT_TURN_COST_USD,
    Estimate,
    GitState,
    RoundResult,
    rejudge_round,
    run_round,
)
from pet_helper.gateway import DEFAULT_ANTHROPIC_MODEL, AnthropicGateway, FakeGateway
from pet_helper.gateway.config import FALLBACK_ENV
from pet_helper.trace import cost_usd
from pet_helper.trace.tracer import CONTENT_ENV
from tests.unit.test_eval_task import (
    JUDGE_USAGE,
    KIT,
    AnthropicStub,
    Gateways,
    JudgeStub,
    Probe,
    _anthropic_events,
    _isolated_environment,  # noqa: F401  (autouse: keeps the environment clean)
    _sse,
    make_case,
)

START = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
COLUMNS = [
    "case_id",
    "suite",
    "k_of_3",
    "first_failure_note",
    "category",
    "human_label",
    "cost_usd",
    "failed_checks",
    "unable_runs",
    "unable_reasons",
]
TURNS = ("Cadê meu pedido?", "Quero saber o prazo de devolução.")
SCRIPTS: dict[str, list[Any]] = {
    "ok": ["Claro, posso ajudar.", "O prazo é de 7 dias."],
    "fail": ["Claro, posso ajudar.", "Não sei."],
    "unable": ["Qual é o seu CPF?", "O prazo é de 7 dias."],
    "unable_failing": ["Qual é o seu CPF?", "Não é caro."],
    "error": [RuntimeError("blip")],
}
# one model call of a stub conversation: billed output tokens, priced at the default assistant model
STUB_OUTPUT_TOKENS = 8000
STUB_CALL_COST = cost_usd(
    "anthropic",
    DEFAULT_ANTHROPIC_MODEL,
    {"input_tokens": 25, "output_tokens": STUB_OUTPUT_TOKENS},
)
JUDGE_CALL_COST = cost_usd("anthropic", DEFAULT_JUDGE_MODEL, JUDGE_USAGE)


# fakes


class Plan:
    """Gateway factory that picks the script of each conversation by the order they are built."""

    def __init__(
        self,
        kinds: Sequence[str] = (),
        *,
        default: str = "ok",
        stub: AnthropicStub | None = None,
    ) -> None:
        self.kinds = list(kinds)
        self.default = default
        self.stub = stub
        self.built: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def __call__(self, case: EvalCase, epoch: int) -> Any:
        with self._lock:
            index = len(self.built)
            self.built.append((case.id, epoch))
        kind = self.kinds[index] if index < len(self.kinds) else self.default
        if kind == "stub":
            assert self.stub is not None
            return self.stub(case, epoch)
        return Probe(SCRIPTS[kind])


class StubProvider(AnthropicStub):
    """Anthropic stub with a settable model, billed output tokens and HTTP status."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        output_tokens: int = STUB_OUTPUT_TOKENS,
        status: int = 200,
        fallback: FakeGateway | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.output_tokens = output_tokens
        self.status = status
        self.fallback = fallback or FakeGateway()

    def __call__(self, case: EvalCase, epoch: int) -> AnthropicGateway:
        chat_model = super().__call__(case, epoch).chat_model
        chat_model.model = self.model
        return AnthropicGateway(chat_model, fallback=self.fallback)

    def _respond(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        if self.status != 200:
            error = {"type": "api_error", "message": "unavailable"}
            return httpx2.Response(self.status, json={"type": "error", "error": error})
        events = _anthropic_events("Certo.")
        events[0]["message"]["model"] = self.model
        events[-2]["usage"]["output_tokens"] = self.output_tokens
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(events)
        )


class GarbledJudge(JudgeStub):
    """Judge client whose reply cannot be parsed when the conversation holds the marker."""

    def __init__(self, marker: str) -> None:
        super().__init__()
        self.marker = marker

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        text, usage = super().complete(system, user)
        return ("not json at all", usage) if self.marker in user else (text, usage)


class BatchStub:
    """Batch client that records the submissions and answers a fixed verdict once ready."""

    model = DEFAULT_JUDGE_MODEL

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.submitted: list[list[Any]] = []

    def submit(self, items: Sequence[Any]) -> str:
        self.submitted.append(list(items))
        return "batch-1"

    def collect(self, batch_id: str, item_ids: Sequence[str]) -> BatchCollection:
        if not self.ready:
            raise BatchNotEnded(batch_id)
        reply = json.dumps({"critique": "Looks fine.", "verdict": "pass"})
        return BatchCollection({item_id: (reply, JUDGE_USAGE) for item_id in item_ids})


class Clock:
    """Fixed clock that advances one second per reading."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        reading = self.now
        self.now += timedelta(seconds=1)
        return reading


# helpers


def play(
    root: Path,
    selection: Sequence[EvalCase],
    gateways: Callable[[EvalCase, int], Any],
    *,
    runs: int = 3,
    judge: str | None = None,
    budget_usd: Decimal = Decimal(5),
    judge_client: JudgeStub | None = None,
    **options: Any,
) -> RoundResult:
    options = {
        "confirm": lambda estimate: True,
        "kit_dir": KIT,
        "round_id": "round-1",
        "clock": Clock(),
        "git_state": lambda: GitState("abc1234", False),
        **options,
    }
    return run_round(
        selection,
        runs=runs,
        judge=judge or ("none" if judge_client is None else "sync"),
        budget_usd=budget_usd,
        results_root=root,
        gateway_factory=gateways,
        judge_client=judge_client,
        **options,
    )


def replay(
    root: Path,
    selection: Sequence[EvalCase],
    judge_client: JudgeStub,
    **options: Any,
) -> RoundResult:
    return rejudge_round(
        root / "round-1",
        selection,
        judge_client,
        confirm=lambda estimate: True,
        budget_usd=Decimal(5),
        **options,
    )


def read_json(root: Path, name: str = "round.json") -> dict[str, Any]:
    return json.loads((root / "round-1" / name).read_text(encoding="utf-8"))


def read_rows(root: Path, round_id: str = "round-1") -> list[dict[str, str]]:
    with (root / round_id / "cases.csv").open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def read_header(root: Path) -> list[str]:
    with (root / "round-1" / "cases.csv").open(encoding="utf-8", newline="") as file:
        return next(csv.reader(file))


def save_round(root: Path, round_id: str, costs: Mapping[str, str]) -> None:
    directory = root / round_id
    directory.mkdir(parents=True)
    with (directory / "cases.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        for case_id, cost in costs.items():
            writer.writerow(
                {**dict.fromkeys(COLUMNS, ""), "case_id": case_id, "cost_usd": cost}
            )
    (directory / "round.json").write_text(json.dumps({"round_id": round_id}))


def outcomes(result: RoundResult) -> list[str]:
    return [outcome for case in result.cases for outcome in case.outcomes]


def peak_concurrency(gateways: Gateways) -> int:
    edges = [
        edge
        for probe in gateways.probes.values()
        for start, end in probe.spans
        for edge in ((start, 1), (end, -1))
    ]
    running = peak = 0
    for _, step in sorted(edges):
        running += step
        peak = max(peak, running)
    return peak


def steady(case_id: str = "steady", **fields: Any) -> EvalCase:
    fields.setdefault("checks", [{"type": "must_include", "value": "7 dias"}])
    return make_case(case_id, TURNS, **fields)


def solo(case_id: str) -> EvalCase:
    return make_case(
        case_id, ("Olá",), checks=[{"type": "must_include", "value": "certo"}]
    )


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "results"


# estimate


def test_it_should_estimate_from_the_mean_cost_per_case_of_the_last_saved_round(root):
    save_round(root, "round-0", {"a": "0.02", "b": "0.04"})
    seen: list[Estimate] = []

    play(
        root,
        [make_case(c) for c in "xyz"],
        Gateways(),
        confirm=lambda estimate: seen.append(estimate) or True,
    )

    assert (seen[0].total_usd, seen[0].source, seen[0].basis_round) == (
        Decimal("0.09"),
        "last_round",
        "round-0",
    )


def test_it_should_estimate_from_the_most_recent_of_several_saved_rounds(root):
    save_round(root, "2026-10-01", {"a": "0.01"})
    save_round(root, "2026-10-29", {"a": "0.05"})
    seen: list[Estimate] = []

    play(
        root,
        [make_case("x"), make_case("y")],
        Gateways(),
        confirm=lambda estimate: seen.append(estimate) or True,
    )

    assert (seen[0].total_usd, seen[0].basis_round) == (
        Decimal("0.10"),
        "2026-10-29",
    )


def test_it_should_estimate_a_default_per_turn_when_no_round_was_saved(root):
    cases = [make_case("a", ("Oi", "Tudo bem?")), make_case("b")]
    seen: list[Estimate] = []

    play(
        root,
        cases,
        Gateways(),
        confirm=lambda estimate: seen.append(estimate) or True,
    )

    # 3 turns in the selection, 3 runs each
    assert (seen[0].total_usd, seen[0].source) == (DEFAULT_TURN_COST_USD * 9, "default")


def test_it_should_return_the_estimate_before_any_gateway_is_built(root):
    gateways = Gateways()
    built_when_asked: list[list[tuple[str, int]]] = []

    play(
        root,
        [make_case()],
        gateways,
        confirm=lambda estimate: built_when_asked.append(list(gateways.built)) or True,
    )

    assert built_when_asked == [[]]


def test_it_should_make_no_call_when_the_confirmation_refuses(root):
    gateways = Gateways()
    judge = JudgeStub()

    result = play(
        root,
        [make_case("judged", judge=["tone"])],
        gateways,
        judge_client=judge,
        confirm=lambda estimate: False,
    )

    assert (result.aborted, gateways.built, judge.calls) == (True, [], [])


def test_it_should_expose_the_estimate_in_the_result(root):
    result = play(root, [make_case()], Gateways())

    assert result.estimate.source == "default"


# outcomes


def test_it_should_pass_a_case_whose_three_runs_pass(root):
    result = play(root, [steady()], Plan())

    [case] = result.cases
    assert (case.passed, case.k_display) == (True, "3/3")


def test_it_should_not_pass_a_case_with_two_passes_of_three(root):
    result = play(root, [steady()], Plan(["ok", "ok", "fail"]))

    [case] = result.cases
    assert (case.passed, case.k_display) == (False, "2/3")


def test_it_should_count_an_errored_run_and_never_pass_it(root):
    result = play(root, [steady()], Plan(["ok", "ok", "error"]))

    [case] = result.cases
    assert (case.passes, case.errors, case.passed) == (2, 1, False)


def test_it_should_list_the_session_of_an_errored_run(root):
    result = play(root, [steady()], Plan(["ok", "ok", "error"]))

    [case] = result.cases
    assert [bool(re.fullmatch(r"steady-e\d+", run)) for run in case.error_runs] == [
        True
    ]


def test_it_should_count_an_unable_run_apart_from_the_errors(root):
    result = play(root, [steady()], Plan(["ok", "ok", "unable"]))

    [case] = result.cases
    assert (case.unable_runs, case.errors) == (1, 0)


def test_it_should_not_count_an_unable_run_as_a_pass(root):
    result = play(root, [steady()], Plan(["ok", "ok", "unable"]))

    [case] = result.cases
    assert (case.passes, case.passed) == (2, False)


def test_it_should_print_the_unable_reasons_next_to_k(root):
    result = play(root, [steady()], Plan(["ok", "ok", "unable"]))

    [case] = result.cases
    assert case.k_display == "2/3 (1 unable: off_script:cpf)"


def test_it_should_keep_an_unable_run_out_of_the_failed_checks(root):
    case = steady(
        checks=[
            {"type": "must_include", "value": "7 dias"},
            {"type": "must_not_include", "value": "caro"},
        ]
    )

    result = play(root, [case], Plan(["ok", "fail", "unable_failing"]))

    assert result.cases[0].failed_checks == ["must_include"]


def test_it_should_list_each_failed_check_once_across_the_failed_runs(root):
    result = play(root, [steady()], Plan(["fail", "fail", "fail"]))

    assert result.cases[0].failed_checks == ["must_include"]


def test_it_should_name_the_turn_of_a_failed_check_scoped_to_a_turn(root):
    case = steady(checks=[{"type": "must_include", "value": "7 dias", "turn": 2}])

    result = play(root, [case], Plan(["fail", "fail", "fail"]))

    assert result.cases[0].failed_checks == ["must_include@2"]


def test_it_should_mark_a_run_unable_with_the_judge_criterion_as_reason(root):
    case = make_case("judged", judge=["tone"])

    result = play(root, [case], Gateways(), runs=1, judge_client=JudgeStub("unable"))

    assert result.cases[0].unable_reasons == ["judge:tone"]


# budget


@pytest.fixture
def capped(root):
    # three cases of one call each; the cap fits two of them, one at a time
    stub = StubProvider()
    cap = 2 * STUB_CALL_COST + ASSISTANT_CALL_RESERVE_USD / 2
    result = play(
        root,
        [solo(f"c{i}") for i in range(3)],
        stub,
        runs=1,
        max_samples=1,
        budget_usd=cap,
    )
    return stub, result


def test_it_should_send_only_the_conversations_that_fit_under_the_cap(capped):
    stub, _ = capped

    assert len(stub.requests) == 2


def test_it_should_mark_the_conversation_after_the_cap_as_not_run(capped):
    _, result = capped

    assert sorted(outcomes(result)) == ["not_run", "pass", "pass"]


def test_it_should_say_the_round_stopped_at_the_cap(capped):
    _, result = capped

    assert (result.stop_reason, "cap" in result.stop_line.lower()) == ("cap", True)


def test_it_should_save_the_partial_result_when_the_cap_stops_the_round(capped, root):
    assert len(read_rows(root)) == 3


def test_it_should_show_zero_of_zero_for_a_case_with_no_run_made(capped, root):
    ks = sorted(row["k_of_1"] for row in read_rows(root))

    assert ks == ["0/0", "1/1", "1/1"]


def test_it_should_send_no_request_when_the_cap_is_below_one_conversation(root):
    stub = StubProvider()

    result = play(
        root,
        [solo("c0")],
        stub,
        runs=1,
        budget_usd=ASSISTANT_CALL_RESERVE_USD / 2,
    )

    assert (stub.requests, result.stop_reason) == ([], "cap")


def test_it_should_stop_the_round_on_a_model_of_unknown_price(root):
    stub = StubProvider(model="claude-mystery-9")

    result = play(root, [solo(f"c{i}") for i in range(4)], stub, runs=1, max_samples=1)

    assert (len(stub.requests), result.stop_reason) == (1, "unknown_price")


def test_it_should_mark_the_rest_not_run_after_an_unknown_price(root):
    result = play(
        root,
        [solo(f"c{i}") for i in range(4)],
        StubProvider(model="claude-mystery-9"),
        runs=1,
        max_samples=1,
    )

    assert outcomes(result).count("not_run") == 3


def test_it_should_report_an_unknown_price_in_one_line(root):
    result = play(
        root,
        [solo(f"c{i}") for i in range(4)],
        StubProvider(model="claude-mystery-9"),
        runs=1,
        max_samples=1,
    )

    assert result.stop_line and "\n" not in result.stop_line


def test_it_should_save_what_it_has_when_the_price_is_unknown(root):
    play(
        root,
        [solo(f"c{i}") for i in range(4)],
        StubProvider(model="claude-mystery-9"),
        runs=1,
        max_samples=1,
    )

    assert len(read_rows(root)) == 4


def test_it_should_stop_the_round_when_a_call_fails_without_usage(root):
    result = play(
        root,
        [solo(f"c{i}") for i in range(4)],
        StubProvider(status=503),
        runs=1,
        max_samples=1,
    )

    assert result.stop_reason == "no_usage"


def test_it_should_mark_the_rest_not_run_after_a_call_without_usage(root):
    result = play(
        root,
        [solo(f"c{i}") for i in range(4)],
        StubProvider(status=503),
        runs=1,
        max_samples=1,
    )

    assert outcomes(result).count("not_run") == 3


def test_it_should_count_the_assistant_cost_in_the_round_total(root):
    stub = StubProvider(output_tokens=100)

    play(root, [solo("c0")], stub, runs=1)

    expected = cost_usd(
        "anthropic",
        DEFAULT_ANTHROPIC_MODEL,
        {"input_tokens": 25, "output_tokens": 100},
    )
    assert Decimal(read_json(root)["total_cost_usd"]) == expected


def test_it_should_count_the_judge_cost_in_the_round_total(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge_client=JudgeStub())

    assert Decimal(read_json(root)["total_cost_usd"]) == JUDGE_CALL_COST


def test_it_should_charge_the_case_row_with_its_assistant_and_judge_cost(root):
    case = solo("c0")
    case = case.model_copy(update={"judge": ["tone"]})
    stub = StubProvider(output_tokens=100)

    play(root, [case], stub, runs=1, judge_client=JudgeStub())

    assistant_cost = cost_usd(
        "anthropic",
        DEFAULT_ANTHROPIC_MODEL,
        {"input_tokens": 25, "output_tokens": 100},
    )
    assert Decimal(read_rows(root)[0]["cost_usd"]) == assistant_cost + JUDGE_CALL_COST


def test_it_should_reserve_the_batch_worst_case_at_submission(root):
    case = make_case("judged", judge=["tone"])
    batch = BatchStub()
    worst_case = batch_reserve_usd(1, JUDGE_MAX_TOKENS)

    result = play(
        root,
        [case],
        Gateways(),
        runs=1,
        judge="batch",
        batch_client=batch,
        budget_usd=worst_case / 2,
    )

    assert (batch.submitted, result.stop_reason) == ([], "cap")


def test_it_should_reconcile_the_batch_reserve_to_the_collected_cost(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge="batch", batch_client=BatchStub())

    expected = JUDGE_CALL_COST * BATCH_PRICE_FACTOR
    assert Decimal(read_json(root)["total_cost_usd"]) == expected


def test_it_should_write_the_collected_batch_verdicts_to_the_judge_log(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge="batch", batch_client=BatchStub())

    verdicts = current_verdicts(root / "round-1" / "judge.jsonl")
    assert set(verdicts) == {("judged-e1", "tone")}


def test_it_should_record_the_batch_id_while_the_batch_is_pending(root):
    case = make_case("judged", judge=["tone"])
    batch = BatchStub(ready=False)

    play(root, [case], Gateways(), runs=1, judge="batch", batch_client=batch)

    saved = read_json(root)
    assert (saved["batch_id"], saved["judge_pending"]) == ("batch-1", True)


@pytest.fixture
def garbled(root):
    cases = [
        make_case("bad", judge=["tone"]),
        make_case("good", judge=["tone"]),
    ]
    result = play(
        root,
        cases,
        Gateways({"bad": ["Resposta ruim."], "good": ["Resposta boa."]}),
        judge_client=GarbledJudge("Resposta ruim."),
    )
    return {case.case_id: case for case in result.cases}, result


def test_it_should_make_the_runs_of_an_unparsable_judge_reply_errors(garbled):
    cases, _ = garbled

    assert (cases["bad"].errors, cases["bad"].passed) == (3, False)


def test_it_should_list_the_runs_of_an_unparsable_judge_reply(garbled):
    cases, _ = garbled

    assert len(cases["bad"].error_runs) == 3


def test_it_should_still_score_the_other_cases_after_an_unparsable_judge_reply(garbled):
    cases, _ = garbled

    assert (cases["good"].passes, cases["good"].passed) == (3, True)


def test_it_should_not_stop_the_round_for_an_unparsable_judge_reply(garbled):
    _, result = garbled

    assert result.stop_reason is None


# layout


def test_it_should_write_the_round_files_under_the_round_directory(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge_client=JudgeStub())

    directory = root / "round-1"
    assert {path.name for path in directory.iterdir() if path.suffix != ".eval"} == {
        "round.json",
        "judge.jsonl",
        "cases.csv",
        "traces",
    }
    assert len(list(directory.glob("*.eval"))) == 1


def test_it_should_write_nothing_outside_the_round_directory(
    root, tmp_path, monkeypatch
):
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    play(root, [make_case()], Gateways(), runs=1)

    assert (list(root.iterdir()), list(workdir.iterdir())) == ([root / "round-1"], [])


def test_it_should_write_a_readable_inspect_log(root):
    play(root, [make_case()], Gateways(), runs=1)

    [log_path] = (root / "round-1").glob("*.eval")
    assert read_eval_log(str(log_path)).status == "success"


def test_it_should_write_the_fixed_cases_csv_header_in_order(root):
    play(root, [make_case()], Gateways())

    assert read_header(root) == COLUMNS


def test_it_should_leave_the_analysis_columns_empty(root):
    result = play(root, [steady()], Plan(["fail", "fail", "fail"]))

    [row] = read_rows(root)
    assert result.cases[0].failed_checks
    assert (row["category"], row["human_label"], row["first_failure_note"]) == (
        "",
        "",
        "",
    )


def test_it_should_fill_the_runner_columns_of_a_case_row(root):
    case = steady(suite="visible")

    play(root, [case], Plan(["ok", "ok", "unable"]))

    [row] = read_rows(root)
    assert Decimal(row.pop("cost_usd")) == 0
    assert row == {
        "case_id": "steady",
        "suite": "visible",
        "k_of_3": "2/3",
        "first_failure_note": "",
        "category": "",
        "human_label": "",
        "failed_checks": "",
        "unable_runs": "1",
        "unable_reasons": "off_script:cpf",
    }


def test_it_should_write_the_contract_fields_to_round_json(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge_client=JudgeStub())

    assert set(read_json(root)) >= {
        "round_id",
        "git_sha",
        "models",
        "assistant_prompt_sha256",
        "judge_prompt_sha256",
        "started_at",
        "finished_at",
        "total_cost_usd",
        "runs",
        "smoke",
        "judge_mode",
        "pii_check",
        "git_dirty",
        "judge_pending",
    }


def test_it_should_record_the_assistant_prompt_hash(root):
    play(root, [make_case()], Gateways(), runs=1)

    assert read_json(root)["assistant_prompt_sha256"] == system_prompt_sha256()


def test_it_should_record_the_assistant_and_judge_models(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge_client=JudgeStub())

    assert read_json(root)["models"] == {
        "assistant": DEFAULT_ANTHROPIC_MODEL,
        "judge": DEFAULT_JUDGE_MODEL,
    }


def test_it_should_record_the_git_sha_and_the_judge_mode(root):
    play(root, [make_case()], Gateways(), runs=1)

    saved = read_json(root)
    assert (saved["git_sha"], saved["judge_mode"], saved["smoke"]) == (
        "abc1234",
        "none",
        False,
    )


def test_it_should_stamp_the_round_with_the_clock(root):
    play(root, [make_case()], Gateways(), runs=1)

    saved = read_json(root)
    assert saved["started_at"] == START.isoformat()
    assert datetime.fromisoformat(saved["finished_at"]) > START


def test_it_should_record_a_clean_pii_check_for_a_masked_round(root):
    play(root, [make_case()], Gateways(), runs=1)

    assert read_json(root)["pii_check"] == "pass"


def test_it_should_record_the_git_dirty_flag_given_at_the_start(root):
    play(
        root,
        [make_case()],
        Gateways(),
        runs=1,
        git_state=lambda: GitState("abc1234", True),
    )

    assert read_json(root)["git_dirty"] is True


def test_it_should_read_the_git_state_before_the_first_gateway_is_built(root):
    order: list[str] = []
    gateways = Gateways()

    def factory(case: EvalCase, epoch: int) -> Any:
        order.append("gateway")
        return gateways(case, epoch)

    def git_state() -> GitState:
        order.append("git")
        return GitState("abc1234", False)

    play(root, [make_case()], factory, runs=1, git_state=git_state)

    assert order[0] == "git"


# decisive rerun


def decisive(root, gateways, *, case=None, **options):
    save_round(root, "round-0", {"solo": "0.01"})
    return play(
        root,
        [case or steady()],
        gateways,
        runs=10,
        reference_round="round-0",
        **options,
    )


def test_it_should_name_the_reference_round_in_round_json(root):
    decisive(root, Plan())

    assert read_json(root)["reference_round"] == "round-0"


def test_it_should_decide_a_case_that_has_ten_valid_runs(root):
    result = decisive(root, Plan())

    assert (result.decision.verdict, result.decision.valid_runs) == ("decided", 10)
    assert result.decision.passes == 10


def test_it_should_retry_an_unable_run_until_there_are_ten_valid_runs(root):
    gateways = Plan(["unable"])

    result = decisive(root, gateways)

    assert (result.decision.valid_runs, len(gateways.built)) == (10, 11)


def test_it_should_retry_an_errored_run_until_there_are_ten_valid_runs(root):
    gateways = Plan(["error"])

    result = decisive(root, gateways)

    assert (result.decision.valid_runs, len(gateways.built)) == (10, 11)


def test_it_should_leave_no_verdict_when_the_cap_stops_the_retries(root):
    # one errored run and nine that fit under the cap, then the retry is refused
    stub = StubProvider()
    gateways = Plan(["error"], default="stub", stub=stub)

    result = decisive(
        root,
        gateways,
        case=solo("solo"),
        max_samples=1,
        budget_usd=9 * STUB_CALL_COST + ASSISTANT_CALL_RESERVE_USD / 2,
    )

    assert (result.decision.verdict, result.decision.valid_runs) == ("no_verdict", 9)
    assert result.stop_reason == "cap"


def test_it_should_not_make_a_decision_without_a_reference_round(root):
    result = play(root, [steady()], Plan())

    assert result.decision is None


def test_it_should_name_the_third_column_after_the_runs(root):
    decisive(root, Plan())

    assert read_header(root)[2] == "k_of_10"


def test_it_should_record_the_runs_of_a_round(root):
    play(root, [make_case()], Gateways(), runs=5)

    assert (read_json(root)["runs"], read_header(root)[2]) == (5, "k_of_5")


# judge modes and re-judge


@pytest.fixture
def pending(root):
    # judge none: the judge case waits for a later re-judge
    cases = [make_case("judged", judge=["tone"])]
    result = play(root, cases, Gateways())
    return cases, result


def test_it_should_leave_the_judge_hash_null_when_the_judge_is_off(pending, root):
    saved = read_json(root)

    assert (saved["judge_prompt_sha256"], saved["judge_pending"]) == (None, True)


def test_it_should_mark_the_judge_cases_pending_in_the_output(pending):
    _, result = pending

    assert result.cases[0].judge_pending is True


def test_it_should_not_mark_a_case_without_judge_criteria_pending(root):
    result = play(root, [make_case("plain")], Gateways())

    assert result.cases[0].judge_pending is False


def test_it_should_record_the_judge_hash_when_every_verdict_is_in(root):
    case = make_case("judged", judge=["tone"])

    play(root, [case], Gateways(), runs=1, judge_client=JudgeStub())

    saved = read_json(root)
    assert (saved["judge_prompt_sha256"], saved["judge_pending"]) == (
        judge_prompt_sha256(),
        False,
    )


def test_it_should_fill_the_judge_log_when_the_round_is_rejudged(pending, root):
    cases, _ = pending

    replay(root, cases, JudgeStub())

    verdicts = current_verdicts(root / "round-1" / "judge.jsonl")
    assert set(verdicts) == {
        ("judged-e1", "tone"),
        ("judged-e2", "tone"),
        ("judged-e3", "tone"),
    }


def test_it_should_recompute_cases_csv_from_the_new_verdicts(pending, root):
    cases, _ = pending

    replay(root, cases, JudgeStub("fail"))

    [row] = read_rows(root)
    assert (row["k_of_3"], row["failed_checks"]) == ("0/3", "judge:tone")


def test_it_should_record_the_judge_hash_after_the_rejudge(pending, root):
    cases, _ = pending

    replay(root, cases, JudgeStub())

    saved = read_json(root)
    assert (saved["judge_prompt_sha256"], saved["judge_pending"]) == (
        judge_prompt_sha256(),
        False,
    )


def test_it_should_add_the_rejudge_cost_to_the_round_total(pending, root):
    cases, _ = pending

    replay(root, cases, JudgeStub())

    assert Decimal(read_json(root)["total_cost_usd"]) == 3 * JUDGE_CALL_COST


def test_it_should_leave_the_hash_null_when_the_rejudge_replaces_only_some_verdicts(
    root, monkeypatch
):
    first, second = make_case("a", judge=["tone"]), make_case("b", judge=["tone"])
    play(root, [first, second], Gateways(), runs=1)
    monkeypatch.setattr(judge_module, "judge_prompt_sha256", lambda: "hash-a")
    replay(root, [first, second], JudgeStub())
    monkeypatch.setattr(judge_module, "judge_prompt_sha256", lambda: "hash-b")

    replay(root, [first], JudgeStub())

    saved = read_json(root)
    assert (saved["judge_prompt_sha256"], saved["judge_pending"]) == (None, True)


def test_it_should_record_the_second_hash_when_the_rejudge_replaces_every_verdict(
    root, monkeypatch
):
    first, second = make_case("a", judge=["tone"]), make_case("b", judge=["tone"])
    play(root, [first, second], Gateways(), runs=1)
    monkeypatch.setattr(judge_module, "judge_prompt_sha256", lambda: "hash-a")
    replay(root, [first, second], JudgeStub())
    monkeypatch.setattr(judge_module, "judge_prompt_sha256", lambda: "hash-b")

    replay(root, [first, second], JudgeStub())

    saved = read_json(root)
    assert (saved["judge_prompt_sha256"], saved["judge_pending"]) == ("hash-b", False)


# concurrency and environment


def test_it_should_run_eight_conversations_at_once_on_the_anthropic_provider(root):
    gateways = Gateways(barrier=threading.Barrier(8))

    result = play(root, [make_case(f"c{i}") for i in range(8)], gateways, runs=1)

    assert outcomes(result) == ["pass"] * 8


def test_it_should_never_run_more_than_eight_conversations_at_once(root):
    gateways = Gateways(delay=0.2)

    play(root, [make_case(f"c{i}") for i in range(12)], gateways, runs=1)

    assert peak_concurrency(gateways) <= 8


def test_it_should_run_one_conversation_at_a_time_on_the_ollama_provider(root):
    gateways = Gateways(delay=0.05)

    play(
        root,
        [make_case(f"c{i}") for i in range(4)],
        gateways,
        runs=1,
        provider="ollama",
    )

    assert peak_concurrency(gateways) == 1


def snapshot_env(seen: dict[str, str | None]) -> Callable[[EvalCase, int], Any]:
    gateways = Gateways()

    def factory(case: EvalCase, epoch: int) -> Any:
        seen[FALLBACK_ENV] = os.environ.get(FALLBACK_ENV)
        seen[CONTENT_ENV] = os.environ.get(CONTENT_ENV)
        return gateways(case, epoch)

    return factory


def test_it_should_turn_the_fallback_off_for_the_round(root, monkeypatch):
    monkeypatch.setenv(FALLBACK_ENV, "ollama")
    seen: dict[str, str | None] = {}

    play(root, [make_case()], snapshot_env(seen), runs=1)

    assert seen[FALLBACK_ENV] == "none"


def test_it_should_turn_the_trace_content_on_for_the_round(root, monkeypatch):
    monkeypatch.setenv(CONTENT_ENV, "0")
    seen: dict[str, str | None] = {}

    play(root, [make_case()], snapshot_env(seen), runs=1)

    assert seen[CONTENT_ENV] == "1"


def test_it_should_restore_the_environment_the_caller_had(root, monkeypatch):
    monkeypatch.setenv(FALLBACK_ENV, "ollama")
    monkeypatch.setenv(CONTENT_ENV, "0")

    play(root, [make_case()], Gateways(), runs=1)

    assert (os.environ[FALLBACK_ENV], os.environ[CONTENT_ENV]) == ("ollama", "0")


def test_it_should_leave_unset_variables_unset_after_the_round(root):
    play(root, [make_case()], Gateways(), runs=1)

    assert (FALLBACK_ENV in os.environ, CONTENT_ENV in os.environ) == (False, False)


def test_it_should_not_fall_back_to_another_model_when_the_provider_fails(root):
    fallback = FakeGateway(["Resposta reserva."])

    play(root, [solo("c0")], StubProvider(status=503, fallback=fallback), runs=1)

    assert fallback.calls == []
