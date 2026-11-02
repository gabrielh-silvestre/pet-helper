"""The Inspect task, multi-turn solver and scorer glue of an eval round (no real model)."""

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from inspect_ai.log import EvalLog, EvalSample, read_eval_log
from inspect_ai.scorer import CORRECT, INCORRECT, Score
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from pet_helper.catalog import Catalog
from pet_helper.conversation import system_prompt_sha256
from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.evals.budget import (
    ASSISTANT_CALL_RESERVE_USD,
    BATCH_PRICE_FACTOR,
    JUDGE_CALL_RESERVE_USD,
    JUDGE_MAX_TOKENS,
    RoundBudget,
    batch_reserve_usd,
)
from pet_helper.evals.cases import EvalCase, load_cases, to_sample_kwargs
from pet_helper.evals.judge import DEFAULT_JUDGE_MODEL, current_verdicts
from pet_helper.evals.task import eval_task, run_eval
from pet_helper.gateway import (
    AnthropicGateway,
    FakeGateway,
    ModelResult,
    ModelUnavailable,
)
from pet_helper.guard import Guard
from pet_helper.orders.api import FAILURE_ENV
from pet_helper.trace import BudgetExceeded, UnknownPriceError

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "kit"
CASES_DIR = ROOT / "evals" / "cases"
SLEEP = 0.4
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
OWNER_TURN = "Meu CPF é 000.000.001-91. Cadê o pedido PED-10001?"
POLICY_REPLY = "Você pode devolver em 7 dias.\nFonte: Troca e devolução"
JUDGE_USAGE = {"input_tokens": 1200, "output_tokens": 150}


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    # the fixture overlay reads evals/fixtures relative to the working directory
    monkeypatch.chdir(ROOT)
    for name in (
        "PET_HELPER_KIT_DIR",
        "PET_HELPER_TODAY",
        "PET_HELPER_TRACE_CONTENT",
        FAILURE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def round_dir(tmp_path) -> Path:
    return tmp_path / "round"


# fakes


class Probe:
    """Gateway that records the thread and span of each call and can wait at a barrier."""

    def __init__(
        self,
        script: Sequence[Any],
        *,
        delay: float = 0.0,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.inner = FakeGateway(script)
        self.delay = delay
        self.barrier = barrier
        self.threads: list[str] = []
        self.spans: list[tuple[float, float]] = []
        self._waited = False

    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()
    ) -> ModelResult:
        start = time.monotonic()
        self.threads.append(threading.current_thread().name)
        if self.barrier is not None and not self._waited:
            self._waited = True
            self.barrier.wait(timeout=5)
        time.sleep(self.delay)
        try:
            return self.inner.complete(messages, tools)
        finally:
            self.spans.append((start, time.monotonic()))

    def tool_payload(self, name: str) -> str:
        last_call = self.inner.calls[-1]
        return next(
            str(message.content)
            for message in last_call
            if isinstance(message, ToolMessage) and message.name == name
        )


class Gateways:
    """Gateway factory: one script per case id (or per epoch), a plain reply per turn otherwise."""

    def __init__(
        self,
        scripts: dict[str, Sequence[Any] | Callable[[int], Sequence[Any]]]
        | None = None,
        *,
        delay: float = 0.0,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.scripts = scripts or {}
        self.delay = delay
        self.barrier = barrier
        self.built: list[tuple[str, int]] = []
        self.probes: dict[tuple[str, int], Probe] = {}

    def __call__(self, case: EvalCase, epoch: int) -> Probe:
        script = self.scripts.get(case.id)
        if callable(script):
            script = script(epoch)
        if script is None:
            script = ["Certo."] * len(case.turns)
        probe = Probe(script, delay=self.delay, barrier=self.barrier)
        self.built.append((case.id, epoch))
        self.probes[(case.id, epoch)] = probe
        return probe

    @property
    def calls_made(self) -> int:
        return sum(len(probe.inner.calls) for probe in self.probes.values())


class JudgeStub:
    """Judge client that answers one fixed verdict and records the thread of each call."""

    model = DEFAULT_JUDGE_MODEL

    def __init__(
        self,
        verdict: str = "pass",
        error: Exception | None = None,
        raw: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.error = error
        # a reply sent as is, to stand for one that cannot be parsed
        self.raw = raw
        self.calls: list[tuple[str, str]] = []
        self.threads: list[str] = []

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        self.calls.append((system, user))
        self.threads.append(threading.current_thread().name)
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return self.raw, JUDGE_USAGE
        reply = {"critique": "Looks fine.", "verdict": self.verdict}
        return json.dumps(reply), JUDGE_USAGE


class RecordingBudget(RoundBudget):
    def __init__(self) -> None:
        super().__init__(Decimal(5))
        self.reserves: list[Decimal] = []

    def before_call(self, reserve: Decimal) -> None:
        self.reserves.append(reserve)
        super().before_call(reserve)


class AnthropicStub:
    """Anthropic adapter over a stub HTTP transport: a real LangChain chat model that fires callbacks."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.built: list[tuple[str, int]] = []

    def __call__(self, case: EvalCase, epoch: int) -> AnthropicGateway:
        self.built.append((case.id, epoch))
        chat_model = ChatAnthropic(
            model="claude-haiku-5-5",
            api_key="sk-ant-test-key",
            streaming=True,
            max_tokens=8192,
            max_retries=0,
        )
        # _client is a cached_property: planting it keeps the real streaming code path
        chat_model.__dict__["_client"] = anthropic.Anthropic(
            api_key="sk-ant-test-key",
            http_client=httpx2.Client(transport=httpx2.MockTransport(self._respond)),
        )
        return AnthropicGateway(chat_model, fallback=FakeGateway())

    def _respond(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(_anthropic_events("Certo.")),
        )


def _sse(events: list[dict]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _anthropic_events(text: str) -> list[dict]:
    usage = {"input_tokens": 25, "output_tokens": 12}
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    return [
        {"type": "message_start", "message": message},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": usage,
        },
        {"type": "message_stop"},
    ]


# helpers


def call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def make_case(
    case_id: str = "case-1", turns: Sequence[str] = ("Olá",), **fields: Any
) -> EvalCase:
    return EvalCase.model_validate(
        {"id": case_id, "today": "2026-11-03", "turns": list(turns), **fields}
    )


def make_executor(max_samples: int, judge_slots: int) -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_samples + judge_slots, thread_name_prefix="eval")


def run_round(
    round_dir: Path,
    cases: Sequence[EvalCase],
    gateways: Callable[[EvalCase, int], Any],
    *,
    runs: int = 3,
    max_samples: int = 4,
    judge: JudgeStub | None = None,
    budget: RoundBudget | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> EvalLog:
    pool = executor or make_executor(max_samples, judge_slots=4)
    try:
        task = eval_task(
            cases,
            round_dir=round_dir,
            kit_dir=KIT,
            gateway_factory=gateways,
            executor=pool,
            runs=runs,
            judge_client=judge,
            budget=budget or RoundBudget(Decimal(5)),
        )
        log = run_eval(task, round_dir=round_dir, max_samples=max_samples)
    finally:
        pool.shutdown(wait=True)
    return read_eval_log(log.location)


def epochs_of(log: EvalLog, case_id: str) -> list[EvalSample]:
    assert log.samples is not None
    return sorted((s for s in log.samples if s.id == case_id), key=lambda s: s.epoch)


def score_of(sample: EvalSample) -> Score:
    assert sample.scores
    return next(iter(sample.scores.values()))


def outcome_of(sample: EvalSample) -> str:
    if sample.error is not None:
        return "error"
    return score_of(sample).metadata["outcome"]


def outcomes_of(log: EvalLog, case_id: str) -> list[str]:
    return [outcome_of(sample) for sample in epochs_of(log, case_id)]


def record_of(sample: EvalSample) -> dict:
    return sample.store["record"]


def trace_events(round_dir: Path, session_id: str) -> list[dict]:
    path = round_dir / "traces" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# task configuration


def masked_metadata(case: EvalCase) -> dict[str, Any]:
    """The case metadata with every string inside checks and reference masked by the Guard."""
    mask = Guard(Catalog(KIT / "catalog.csv")).mask

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return mask(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    metadata = to_sample_kwargs(case)["metadata"]
    return {
        **metadata,
        "checks": walk(metadata["checks"]),
        "reference": walk(metadata["reference"]),
    }


def test_it_should_build_one_sample_per_case_from_the_sample_kwargs(round_dir):
    case = make_case(
        "policy-case",
        checks=[
            {
                "type": "no_other_customer_data",
                "value": ["zuleica@clientes.example.com"],
            }
        ],
        reference={"replies": [{"text": "Escreva para zuleica@clientes.example.com"}]},
    )

    task = eval_task(
        [case],
        round_dir=round_dir,
        kit_dir=KIT,
        gateway_factory=Gateways(),
        executor=make_executor(1, 0),
        budget=RoundBudget(Decimal(5)),
    )

    [sample] = list(task.dataset)
    assert sample.id == "policy-case"
    assert sample.metadata == masked_metadata(case)


def test_it_should_run_three_epochs_reduced_by_at_least_3_by_default(round_dir):
    log = run_round(round_dir, [make_case()], Gateways())

    assert log.eval.config.epochs == 3
    assert log.eval.config.epochs_reducer == ["at_least_3"]


def test_it_should_write_the_inspect_log_to_the_round_directory(round_dir):
    run_round(round_dir, [make_case()], Gateways(), runs=1)

    assert len(list(round_dir.glob("*.eval"))) == 1


def test_it_should_not_write_a_logs_directory_to_the_working_directory(
    round_dir, tmp_path, monkeypatch
):
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    run_round(round_dir, [make_case()], Gateways(), runs=1)

    assert not (workdir / "logs").exists()


def test_it_should_not_retry_an_errored_epoch(round_dir):
    gateways = Gateways({"boom": [RuntimeError("model exploded")]})

    run_round(round_dir, [make_case("boom")], gateways)

    assert sorted(gateways.built) == [("boom", 1), ("boom", 2), ("boom", 3)]


def test_it_should_finish_the_other_cases_when_one_case_errors(round_dir):
    gateways = Gateways({"boom": [RuntimeError("model exploded")]})

    log = run_round(round_dir, [make_case("boom"), make_case("steady")], gateways)

    assert log.status == "success"
    assert outcomes_of(log, "steady") == ["pass", "pass", "pass"]


# one assistant per sample


def test_it_should_build_a_fresh_gateway_for_each_epoch_of_each_case(round_dir):
    gateways = Gateways()

    run_round(round_dir, [make_case("a"), make_case("b")], gateways)

    assert sorted(gateways.built) == [(c, e) for c in "ab" for e in (1, 2, 3)]


def test_it_should_trace_each_epoch_under_its_session_id_in_the_round_directory(
    round_dir,
):
    run_round(round_dir, [make_case("trace-me")], Gateways())

    names = sorted(path.name for path in (round_dir / "traces").glob("*.jsonl"))
    assert names == [f"trace-me-e{epoch}.jsonl" for epoch in (1, 2, 3)]


def test_it_should_trace_the_content_of_the_turn(round_dir):
    run_round(round_dir, [make_case("trace-me", ("Oi",))], Gateways(), runs=1)

    events = {e["event"]: e for e in trace_events(round_dir, "trace-me-e1")}
    assert events["turn.start"]["gen_ai.input.messages"] == "Oi"
    assert events["turn.end"]["gen_ai.output.messages"] == "Certo."


def test_it_should_give_the_assistant_the_date_of_the_case(round_dir):
    gateways = Gateways(
        {"dated": [call("get_order", {"order_number": "PED-10001"}), "Ok."]}
    )
    case = make_case("dated", (OWNER_TURN,), today="2026-11-10")

    run_round(round_dir, [case], gateways, runs=1)

    payload = gateways.probes[("dated", 1)].tool_payload("get_order")
    assert json.loads(payload)["order"]["days_since_delivery"] == 10


def test_it_should_overlay_the_fixture_kit_when_the_case_names_one(round_dir):
    gateways = Gateways(
        {"poisoned": [call("search_catalog", {"query": "salmão"}), "Ok."]}
    )
    case = make_case("poisoned", fixture="indirect-injection")

    run_round(round_dir, [case], gateways, runs=1)

    assert "CANARIO-100" in gateways.probes[("poisoned", 1)].tool_payload(
        "search_catalog"
    )


def test_it_should_use_the_base_kit_when_the_case_names_no_fixture(round_dir):
    gateways = Gateways({"clean": [call("search_catalog", {"query": "salmão"}), "Ok."]})

    run_round(round_dir, [make_case("clean")], gateways, runs=1)

    assert "CANARIO-100" not in gateways.probes[("clean", 1)].tool_payload(
        "search_catalog"
    )


def test_it_should_run_every_gateway_call_on_the_given_executor(round_dir):
    gateways = Gateways()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eval-test")

    run_round(round_dir, [make_case("a"), make_case("b")], gateways, executor=pool)

    threads = {t for probe in gateways.probes.values() for t in probe.threads}
    assert threads and all(name.startswith("eval-test") for name in threads)


def test_it_should_size_the_executor_for_the_samples_plus_the_judge_calls():
    pool = make_executor(8, 3)

    assert pool._max_workers == 11
    pool.shutdown()


# the sample store


def test_it_should_store_the_reply_of_each_turn_in_order(round_dir):
    gateways = Gateways({"talk": ["Primeira.", "Segunda."]})

    log = run_round(round_dir, [make_case("talk", ("Oi", "E então?"))], gateways)

    texts = [r["text"] for r in record_of(epochs_of(log, "talk")[0])["replies"]]
    assert texts == ["Primeira.", "Segunda."]


def test_it_should_store_the_sources_cited_by_the_reply(round_dir):
    gateways = Gateways(
        {"cited": [call("search_policies", {"query": "devolução"}), POLICY_REPLY]}
    )

    log = run_round(round_dir, [make_case("cited")], gateways)

    [reply] = record_of(epochs_of(log, "cited")[0])["replies"]
    assert reply["sources"] == ["troca-e-devolucao.md"]


def test_it_should_store_the_tool_calls_with_their_arguments(round_dir):
    gateways = Gateways(
        {
            "tools": [
                call("search_policies", {"query": "devolução de ração"}),
                POLICY_REPLY,
            ]
        }
    )

    log = run_round(round_dir, [make_case("tools")], gateways)

    assert record_of(epochs_of(log, "tools")[0])["tool_calls"] == [
        {"name": "search_policies", "args": {"query": "devolução de ração"}}
    ]


def test_it_should_store_the_policy_hits_retrieved_by_the_conversation(round_dir):
    gateways = Gateways(
        {
            "hits": [
                call("search_policies", {"query": "devolução de ração"}),
                POLICY_REPLY,
            ]
        }
    )

    log = run_round(round_dir, [make_case("hits")], gateways)

    hits = record_of(epochs_of(log, "hits")[0])["retrieved"]
    assert hits[0]["source"] == "troca-e-devolucao.md"


def test_it_should_store_the_escalation_of_the_reply_and_the_final_state(round_dir):
    gateways = Gateways(
        {"handoff": [call("escalate_to_human", {"reason": "explicit_request"})]}
    )

    log = run_round(
        round_dir, [make_case("handoff", ("Quero falar com uma pessoa",))], gateways
    )

    record = record_of(epochs_of(log, "handoff")[0])
    assert record["replies"][0]["escalation"] == "explicit_request"
    assert record["state"]["escalation"] == "explicit_request"
    assert record["state"]["ended"] is True


def test_it_should_mask_the_stored_replies_with_the_guard(round_dir):
    gateways = Gateways({"leaky": ["Seu CPF 000.000.001-91 foi anotado."]})

    log = run_round(round_dir, [make_case("leaky")], gateways)

    [reply] = record_of(epochs_of(log, "leaky")[0])["replies"]
    assert "000.000.001-91" not in reply["text"]
    assert reply["text"] == Guard(Catalog(KIT / "catalog.csv")).mask(reply["text"])


# the Sample input in the log


@pytest.mark.parametrize(
    "case_id",
    ["adv-other-customer-data", "esc-repeated-failure", "rn02-pix-refund-cancelled"],
)
def test_it_should_not_write_a_formatted_cpf_from_a_case_turn_to_the_log(
    round_dir, case_id
):
    case = next(c for c in load_cases(CASES_DIR) if c.id == case_id)
    raw_cpfs = {cpf for turn in case.turns for cpf in CPF.findall(turn)}

    log = run_round(round_dir, [case], Gateways(), runs=1)

    written = log.model_dump_json()
    assert raw_cpfs and not any(cpf in written for cpf in raw_cpfs)


def test_it_should_feed_the_assistant_the_raw_turn_not_the_masked_sample_input(
    round_dir,
):
    gateways = Gateways(
        {"raw": [call("get_order", {"order_number": "PED-10001"}), "Ok."]}
    )

    run_round(round_dir, [make_case("raw", (OWNER_TURN,))], gateways, runs=1)

    payload = gateways.probes[("raw", 1)].tool_payload("get_order")
    assert json.loads(payload)["status"] == "found"


# real parallelism


def test_it_should_run_eight_samples_at_the_same_time(round_dir):
    Guard(Catalog(KIT / "catalog.csv")).mask("warm up the masking engines")
    # every gateway waits for the other seven: serialized samples would break the barrier
    barrier = threading.Barrier(8, timeout=5)
    gateways = Gateways(barrier=barrier)
    cases = [make_case(f"p{i}") for i in range(8)]

    log = run_round(round_dir, cases, gateways, runs=1, max_samples=8)

    assert not barrier.broken
    assert [outcome_of(sample) for sample in log.samples or []].count("error") == 0


def test_it_should_write_a_well_formed_trace_file_for_each_of_eight_parallel_samples(
    round_dir,
):
    cases = [make_case(f"p{i}") for i in range(8)]

    run_round(round_dir, cases, Gateways(delay=SLEEP), runs=1, max_samples=8)

    paths = sorted((round_dir / "traces").glob("*.jsonl"))
    assert [p.stem for p in paths] == [f"p{i}-e1" for i in range(8)]
    for path in paths:
        events = trace_events(round_dir, path.stem)
        assert {e["session_id"] for e in events} == {path.stem}
        assert events[0]["event"] == "turn.start"
        assert events[-1]["event"] == "turn.end"


def test_it_should_keep_the_date_and_the_kit_of_each_concurrent_sample_apart(
    round_dir,
):
    script = [
        call("get_order", {"order_number": "PED-10001"}),
        call("search_catalog", {"query": "salmão"}, "call_2"),
        "Pronto.",
    ]
    gateways = Gateways(
        {"early-clean": script, "late-poisoned": script},
        barrier=threading.Barrier(2),
    )
    cases = [
        make_case("early-clean", (OWNER_TURN,), today="2026-11-03"),
        make_case(
            "late-poisoned",
            (OWNER_TURN,),
            today="2026-11-10",
            fixture="indirect-injection",
        ),
    ]

    run_round(round_dir, cases, gateways, runs=1, max_samples=2)

    early = gateways.probes[("early-clean", 1)]
    late = gateways.probes[("late-poisoned", 1)]
    assert (
        json.loads(early.tool_payload("get_order"))["order"]["days_since_delivery"] == 3
    )
    assert (
        json.loads(late.tool_payload("get_order"))["order"]["days_since_delivery"] == 10
    )
    assert "CANARIO-100" not in early.tool_payload("search_catalog")
    assert "CANARIO-100" in late.tool_payload("search_catalog")


# the scorer


def test_it_should_score_a_run_that_meets_its_checks_as_a_pass(round_dir):
    case = make_case("ok", checks=[{"type": "must_include", "value": "7 dias"}])
    gateways = Gateways({"ok": ["O prazo é de 7 dias."]})

    log = run_round(round_dir, [case], gateways)

    scores = [score_of(s) for s in epochs_of(log, "ok")]
    assert [s.metadata["outcome"] for s in scores] == ["pass"] * 3
    assert [s.value for s in scores] == [CORRECT] * 3


def test_it_should_score_a_run_that_misses_a_check_as_a_fail_naming_the_check(
    round_dir,
):
    case = make_case("miss", checks=[{"type": "must_include", "value": "7 dias"}])
    gateways = Gateways({"miss": ["Não sei."]})

    log = run_round(round_dir, [case], gateways)

    scores = [score_of(s) for s in epochs_of(log, "miss")]
    assert [s.metadata["outcome"] for s in scores] == ["fail"] * 3
    assert [s.metadata["failed_checks"] for s in scores] == [["must_include"]] * 3
    assert [s.value for s in scores] == [INCORRECT] * 3


def test_it_should_yield_one_score_per_epoch(round_dir):
    log = run_round(round_dir, [make_case("each")], Gateways())

    assert [s.epoch for s in epochs_of(log, "each")] == [1, 2, 3]
    assert all(s.scores for s in epochs_of(log, "each"))


def test_it_should_report_the_recall_of_each_expected_doc(round_dir):
    case = make_case("recall", expected_doc=["troca-e-devolucao.md", "frete.md"])
    gateways = Gateways(
        {
            "recall": [
                call("search_policies", {"query": "devolução de ração"}),
                POLICY_REPLY,
            ]
        }
    )

    log = run_round(round_dir, [case], gateways, runs=1)

    recall = score_of(epochs_of(log, "recall")[0]).metadata["expected_doc_recall"]
    assert recall == {"troca-e-devolucao.md": True, "frete.md": False}


def test_it_should_write_no_judge_log_when_the_judge_is_off(round_dir):
    case = make_case("quiet", judge=["tone"])

    run_round(round_dir, [case], Gateways(), runs=1)

    assert not (round_dir / "judge.jsonl").exists()


def test_it_should_call_the_judge_once_per_criterion_of_the_case(round_dir):
    case = make_case("judged", judge=["tone", "escalation_judgment"])
    judge = JudgeStub()

    run_round(round_dir, [case], Gateways(), runs=1, judge=judge)

    assert len(judge.calls) == 2


def test_it_should_show_the_judge_the_reply_of_the_assistant(round_dir):
    case = make_case("judged", judge=["tone"])
    gateways = Gateways({"judged": ["Olá! Como posso ajudar hoje?"]})
    judge = JudgeStub()

    run_round(round_dir, [case], gateways, runs=1, judge=judge)

    assert "Olá! Como posso ajudar hoje?" in judge.calls[0][1]


def test_it_should_show_the_dont_know_judge_only_the_policies_retrieved_in_its_turn(
    round_dir,
):
    case = make_case(
        "two-turns",
        ("Qual o prazo de devolução?", "E o prazo de entrega?"),
        judge=[{"criterion": "dont_know_accuracy", "turn": 2}],
    )
    gateways = Gateways(
        {
            "two-turns": [
                call("search_policies", {"query": "devolução de ração"}),
                POLICY_REPLY,
                "Não sei informar.",
            ]
        }
    )
    judge = JudgeStub()

    run_round(round_dir, [case], gateways, runs=1, judge=judge)

    [(_, slice_)] = judge.calls
    assert "Políticas recuperadas: nenhuma" in slice_


def test_it_should_show_the_dont_know_judge_a_policy_retrieved_again_in_its_turn(
    round_dir,
):
    case = make_case(
        "again",
        ("Qual o prazo de devolução?", "E se o produto já foi aberto?"),
        judge=[{"criterion": "dont_know_accuracy", "turn": 2}],
    )
    search = {"query": "devolução de ração"}
    gateways = Gateways(
        {
            "again": [
                call("search_policies", search),
                POLICY_REPLY,
                call("search_policies", search, "call_2"),
                "Pode, se a embalagem estiver lacrada.",
            ]
        }
    )
    judge = JudgeStub()

    run_round(round_dir, [case], gateways, runs=1, judge=judge)

    [(_, slice_)] = judge.calls
    assert re.search(r"Políticas recuperadas:.*Troca e devolução", slice_)


def test_it_should_append_each_verdict_to_the_judge_log_under_the_run_id(round_dir):
    case = make_case("judged", judge=["tone", "escalation_judgment"])

    run_round(round_dir, [case], Gateways(), runs=1, judge=JudgeStub())

    verdicts = current_verdicts(round_dir / "judge.jsonl")
    assert set(verdicts) == {
        ("judged-e1", "tone"),
        ("judged-e1", "escalation_judgment"),
    }


def test_it_should_run_the_judge_calls_on_the_given_executor(round_dir):
    case = make_case("judged", judge=["tone"])
    judge = JudgeStub()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="eval-test")

    run_round(round_dir, [case], Gateways(), runs=1, judge=judge, executor=pool)

    assert all(name.startswith("eval-test") for name in judge.threads)


def test_it_should_reserve_the_shared_budget_for_each_judge_call(round_dir):
    case = make_case("judged", judge=["tone", "escalation_judgment"])
    budget = RecordingBudget()

    run_round(round_dir, [case], Gateways(), runs=1, judge=JudgeStub(), budget=budget)

    assert budget.reserves.count(JUDGE_CALL_RESERVE_USD) == 2


def test_it_should_fail_a_run_the_judge_fails_naming_the_criterion(round_dir):
    case = make_case("judged", judge=["tone"])

    log = run_round(round_dir, [case], Gateways(), runs=1, judge=JudgeStub("fail"))

    score = score_of(epochs_of(log, "judged")[0])
    assert score.metadata["outcome"] == "fail"
    assert score.metadata["failed_checks"] == ["judge:tone"]


def test_it_should_mark_a_run_unable_when_the_judge_cannot_decide(round_dir):
    case = make_case("judged", judge=["tone"])

    log = run_round(round_dir, [case], Gateways(), runs=1, judge=JudgeStub("unable"))

    score = score_of(epochs_of(log, "judged")[0])
    assert score.metadata["outcome"] == "unable"
    assert score.metadata["reason"] == "judge:tone"


def test_it_should_make_the_epoch_an_error_when_the_judge_client_raises(round_dir):
    case = make_case("judged", judge=["tone"])
    judge = JudgeStub(error=RuntimeError("judge exploded"))

    log = run_round(round_dir, [case], Gateways(), runs=1, judge=judge)

    [sample] = epochs_of(log, "judged")
    assert outcome_of(sample) == "error"
    assert not any(s.value == CORRECT for s in (sample.scores or {}).values())


@pytest.mark.parametrize(
    ("judge", "price_unknown"),
    [
        (JudgeStub(error=anthropic.APIConnectionError(request=REQUEST)), False),
        (JudgeStub(error=UnknownPriceError("No price for anthropic/x")), True),
    ],
    ids=["api_error", "unknown_price"],
)
def test_it_should_keep_the_conversation_and_stop_the_round_when_the_judge_call_fails(
    round_dir, judge, price_unknown
):
    case = make_case("judged", judge=["tone"])
    budget = RoundBudget(Decimal(5))

    log = run_round(round_dir, [case], Gateways(), runs=1, judge=judge, budget=budget)

    score = score_of(epochs_of(log, "judged")[0])
    assert score.metadata["reason"] == "judge:unparsable"
    assert score.metadata["outcome"] in ("pass", "fail")
    assert (budget.halted, budget.price_unknown) == (True, price_unknown)


def test_it_should_leave_the_other_runs_not_run_after_the_judge_call_fails(round_dir):
    cases = [make_case(f"c{i}", judge=["tone"]) for i in range(4)]
    judge = JudgeStub(error=anthropic.APIConnectionError(request=REQUEST))

    log = run_round(round_dir, cases, Gateways(), runs=1, max_samples=1, judge=judge)

    outcomes = [outcome_of(sample) for sample in log.samples or []]
    assert outcomes.count("not_run") >= 1 and "error" not in outcomes


def test_it_should_leave_a_run_not_run_when_the_judge_budget_is_exceeded(round_dir):
    case = make_case("judged", judge=["tone"])
    judge = JudgeStub(error=BudgetExceeded("cap reached"))

    log = run_round(round_dir, [case], Gateways(), runs=1, judge=judge)

    score = score_of(epochs_of(log, "judged")[0])
    assert (score.metadata["outcome"], score.metadata["reason"]) == (
        "not_run",
        "budget",
    )


def test_it_should_reserve_the_worst_case_of_the_configured_judge_model(round_dir):
    case = make_case("judged", judge=["tone"])
    judge = JudgeStub()
    judge.model = "claude-opus-5-5"
    budget = RecordingBudget()
    worst_case = batch_reserve_usd(1, JUDGE_MAX_TOKENS, model=judge.model)

    run_round(round_dir, [case], Gateways(), runs=1, judge=judge, budget=budget)

    assert min(budget.reserves) >= worst_case / BATCH_PRICE_FACTOR


def test_it_should_not_error_the_epoch_when_the_judge_reply_cannot_be_parsed(
    round_dir,
):
    case = make_case("judged", judge=["tone"])

    log = run_round(
        round_dir, [case], Gateways(), runs=1, judge=JudgeStub(raw="not json")
    )

    [sample] = epochs_of(log, "judged")
    assert sample.error is None


def test_it_should_leave_the_epoch_unscored_when_the_judge_reply_cannot_be_parsed(
    round_dir,
):
    case = make_case("judged", judge=["tone"])

    log = run_round(
        round_dir, [case], Gateways(), runs=1, judge=JudgeStub(raw="not json")
    )

    score = score_of(epochs_of(log, "judged")[0])
    assert score.metadata["reason"] == "judge:unparsable"
    assert score.value not in (CORRECT, INCORRECT)


def test_it_should_keep_the_conversation_when_the_judge_reply_cannot_be_parsed(
    round_dir,
):
    case = make_case("judged", judge=["tone"])
    gateways = Gateways({"judged": ["Resposta boa."]})

    log = run_round(
        round_dir, [case], gateways, runs=1, judge=JudgeStub(raw="not json")
    )

    [reply] = record_of(epochs_of(log, "judged")[0])["replies"]
    assert reply["text"] == "Resposta boa."


# off-script


def test_it_should_mark_a_run_unable_when_the_assistant_asks_for_a_cpf_the_next_turn_does_not_give(
    round_dir,
):
    case = make_case(
        "stray",
        ("Cadê meu pedido?", "Quero saber o prazo de devolução."),
        checks=[{"type": "must_include", "value": "7 dias"}],
    )
    gateways = Gateways({"stray": ["Qual é o seu CPF?", "O prazo é de 7 dias."]})

    log = run_round(round_dir, [case], gateways, runs=1)

    score = score_of(epochs_of(log, "stray")[0])
    assert score.metadata["outcome"] == "unable"
    assert score.metadata["reason"].startswith("off_script")


def test_it_should_score_the_same_conversation_normally_without_the_question(
    round_dir,
):
    case = make_case(
        "steady",
        ("Cadê meu pedido?", "Quero saber o prazo de devolução."),
        checks=[{"type": "must_include", "value": "7 dias"}],
    )
    gateways = Gateways({"steady": ["Claro, posso ajudar.", "O prazo é de 7 dias."]})

    log = run_round(round_dir, [case], gateways, runs=1)

    assert outcomes_of(log, "steady") == ["pass"]


# errors


def test_it_should_make_the_epoch_an_error_when_the_model_is_unavailable(round_dir):
    gateways = Gateways({"down": [ModelUnavailable("provider down")]})

    log = run_round(round_dir, [make_case("down")], gateways)

    assert outcomes_of(log, "down") == ["error"] * 3


def test_it_should_not_pass_an_epoch_whose_model_was_unavailable(round_dir):
    gateways = Gateways({"down": [ModelUnavailable("provider down")]})

    log = run_round(round_dir, [make_case("down")], gateways)

    assert not any(s.scores for s in epochs_of(log, "down"))


def test_it_should_make_the_epoch_an_error_with_the_message_of_any_exception(
    round_dir,
):
    gateways = Gateways({"boom": [RuntimeError("model exploded")]})

    log = run_round(round_dir, [make_case("boom")], gateways, runs=1)

    [sample] = epochs_of(log, "boom")
    assert sample.error is not None and "model exploded" in sample.error.message


def test_it_should_keep_the_other_epochs_of_a_case_scored_when_one_errors(round_dir):
    gateways = Gateways(
        {"flaky": lambda epoch: [RuntimeError("blip")] if epoch == 2 else ["Certo."]}
    )

    log = run_round(round_dir, [make_case("flaky")], gateways)

    assert outcomes_of(log, "flaky") == ["pass", "error", "pass"]


# budget


def test_it_should_end_a_sample_as_not_run_when_the_budget_is_exceeded(round_dir):
    gateways = Gateways({"capped": [BudgetExceeded("cap reached")]})

    log = run_round(round_dir, [make_case("capped")], gateways, runs=1)

    [sample] = epochs_of(log, "capped")
    assert sample.error is None
    assert outcome_of(sample) == "not_run"


def test_it_should_not_start_the_remaining_samples_after_the_budget_is_exceeded(
    round_dir,
):
    gateways = Gateways({f"c{i}": [BudgetExceeded("cap reached")] for i in range(3)})
    cases = [make_case(f"c{i}") for i in range(3)]

    log = run_round(round_dir, cases, gateways, runs=1, max_samples=1)

    assert gateways.calls_made == 1
    assert [outcomes_of(log, f"c{i}") for i in range(3)] == [["not_run"]] * 3


def test_it_should_send_no_request_when_the_cap_is_below_one_assistant_call_reserve(
    round_dir,
):
    stub = AnthropicStub()
    budget = RoundBudget(ASSISTANT_CALL_RESERVE_USD / 2)

    run_round(round_dir, [make_case("capped")], stub, runs=1, budget=budget)

    assert stub.requests == []


def test_it_should_end_the_sample_as_not_run_when_the_callback_refuses_the_call(
    round_dir,
):
    budget = RoundBudget(ASSISTANT_CALL_RESERVE_USD / 2)

    log = run_round(
        round_dir, [make_case("capped")], AnthropicStub(), runs=1, budget=budget
    )

    [sample] = epochs_of(log, "capped")
    assert sample.error is None
    assert outcome_of(sample) == "not_run"


def test_it_should_not_start_the_remaining_samples_after_the_callback_refuses_a_call(
    round_dir,
):
    stub = AnthropicStub()
    cases = [make_case(f"c{i}") for i in range(3)]
    budget = RoundBudget(ASSISTANT_CALL_RESERVE_USD / 2)

    log = run_round(round_dir, cases, stub, runs=1, max_samples=1, budget=budget)

    assert [outcomes_of(log, f"c{i}") for i in range(3)] == [["not_run"]] * 3
    assert len(list((round_dir / "traces").glob("*.jsonl"))) == 1


def test_it_should_send_the_request_when_the_cap_covers_one_assistant_call_reserve(
    round_dir,
):
    stub = AnthropicStub()
    budget = RoundBudget(ASSISTANT_CALL_RESERVE_USD * 2)

    log = run_round(round_dir, [make_case("funded")], stub, runs=1, budget=budget)

    assert len(stub.requests) == 1
    assert outcomes_of(log, "funded") == ["pass"]


# round.json


def test_it_should_expose_the_sha256_of_the_assistant_system_prompt():
    assert system_prompt_sha256() == hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
