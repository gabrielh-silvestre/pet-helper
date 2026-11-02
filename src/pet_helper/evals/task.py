import asyncio
import tempfile
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import anthropic
from inspect_ai import Epochs, Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.log import EvalLog
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    Score,
    Scorer,
    Target,
    accuracy,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from pet_helper.catalog import Catalog
from pet_helper.conversation import ToolRegistry, build_assistant, build_kit_registry
from pet_helper.evals.budget import ASSISTANT_CALL_RESERVE_USD, RoundBudget
from pet_helper.evals.cases import PUBLIC_STATE_KEYS, EvalCase, to_sample_kwargs
from pet_helper.evals.checks import (
    RunRecord,
    ToolCall,
    evaluate_case,
    expected_doc_recall,
)
from pet_helper.evals.fixtures import overlay_kit
from pet_helper.evals.judge import (
    JudgeClient,
    Judged,
    JudgeReplyError,
    Transcript,
    TranscriptTurn,
    Verdict,
    _entries,
    _judge_charged,
)
from pet_helper.gateway import ModelGateway, ModelUnavailable
from pet_helper.guard import Guard
from pet_helper.knowledge import PolicyHit
from pet_helper.trace import (
    BudgetExceeded,
    TraceCallbackHandler,
    Tracer,
    UnknownPriceError,
)

GatewayFactory = Callable[[EvalCase, int], ModelGateway]

MOCK_MODEL = "mockllm/model"
# the reason of a run whose judge reply was billed but cannot be read: the run waits for a rejudge
JUDGE_UNPARSABLE = "judge:unparsable"
# building is pure-Python CPU work: serial it is faster than thrashing the GIL, and it guards the registry cache
_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class _Conversation:
    record: RunRecord
    known_skus: Collection[str]
    transcript: Transcript
    stored: Mapping[str, Any]


@dataclass(frozen=True)
class _Conductor:
    kit_dir: Path
    guard: Guard
    tracer: Tracer
    gateway_factory: GatewayFactory
    handler_factory: Callable[[], TraceCallbackHandler]
    registries: dict[tuple[str | None, date], ToolRegistry] = field(
        default_factory=dict
    )

    def converse(self, case: EvalCase, epoch: int) -> _Conversation:
        session_id = f"{case.id}-e{epoch}"
        with tempfile.TemporaryDirectory() as scratch:
            kit_dir = self.kit_dir
            if case.fixture:
                kit_dir = Path(scratch) / "kit"
                overlay_kit(self.kit_dir, case.fixture, kit_dir)
            with _BUILD_LOCK:
                # the tools only read the kit, so samples with the same kit and date share them
                key = (case.fixture, case.today)
                if key not in self.registries:
                    self.registries[key] = build_kit_registry(kit_dir, case.today)
                assistant = build_assistant(
                    gateway=self.gateway_factory(case, epoch),
                    registry=self.registries[key],
                    kit_dir=kit_dir,
                    today=case.today,
                    tracer=self.tracer,
                    handler_factory=self.handler_factory,
                )
            replies = []
            policy_titles = []
            seen = 0
            for text in case.turns:
                replies.append(assistant.turn(session_id, text))
                snapshot = assistant.snapshot(session_id)
                # the history is cumulative: the messages after the last turn are this turn's
                policy_titles.append(_policy_titles(snapshot["messages"][seen:]))
                seen = len(snapshot["messages"])
            known_skus = Catalog(kit_dir / "catalog.csv").skus
        # the graph turns an unavailable model into a handoff, which is not a result
        if snapshot.get("escalation_cause") == "model_unavailable":
            raise ModelUnavailable(f"{session_id}: the model was unavailable")
        record = RunRecord(
            replies=replies,
            tool_calls=tuple(
                ToolCall(call["name"], call["args"])
                for message in snapshot["messages"]
                if isinstance(message, AIMessage)
                for call in message.tool_calls
            ),
            retrieved=tuple(snapshot.get("retrieved", ())),
            state={key: snapshot.get(key) for key in PUBLIC_STATE_KEYS},
        )
        return _Conversation(
            record,
            known_skus,
            self._masked_transcript(case, record, policy_titles),
            self._masked_record(record, policy_titles),
        )

    def _masked(self, values: Mapping[str, Any]) -> dict[str, Any]:
        return {
            name: self.guard.mask(value) if isinstance(value, str) else value
            for name, value in values.items()
        }

    def _masked_record(
        self, record: RunRecord, policy_titles: Sequence[tuple[str, ...]]
    ) -> dict[str, Any]:
        return {
            "replies": [
                {
                    "text": self.guard.mask(reply.text),
                    "sources": list(reply.sources),
                    "escalation": reply.escalation,
                }
                for reply in record.replies
            ],
            "tool_calls": [
                {"name": call.name, "args": self._masked(call.args)}
                for call in record.tool_calls
            ],
            "retrieved": [
                {"source": hit.source, "title": hit.title} for hit in record.retrieved
            ],
            "policy_titles": [list(titles) for titles in policy_titles],
            "state": self._masked(record.state),
        }

    def _masked_transcript(
        self,
        case: EvalCase,
        record: RunRecord,
        policy_titles: Sequence[tuple[str, ...]],
    ) -> Transcript:
        return Transcript(
            turns=[
                TranscriptTurn(
                    self.guard.mask(user), self.guard.mask(reply.text), titles
                )
                for user, reply, titles in zip(
                    case.turns, record.replies, policy_titles, strict=True
                )
            ],
            escalation=record.replies[-1].escalation,
        )


def _policy_titles(messages: Sequence[BaseMessage]) -> tuple[str, ...]:
    # the state's `retrieved` is deduplicated by source, so it cannot tell a policy found again
    hits = (
        hit
        for message in messages
        if isinstance(message, ToolMessage) and isinstance(message.artifact, list)
        for hit in message.artifact
        if isinstance(hit, PolicyHit)
    )
    return tuple(dict.fromkeys(hit.title for hit in hits))


def _masked(guard: Guard, value: Any) -> Any:
    if isinstance(value, str):
        return guard.mask(value)
    if isinstance(value, list):
        return [_masked(guard, item) for item in value]
    if isinstance(value, dict):
        return {key: _masked(guard, item) for key, item in value.items()}
    return value


def _score_of(
    outcome: str,
    failed_checks: list[str],
    reason: str | None,
    recall: dict[str, bool],
) -> Score:
    metadata = {
        "outcome": outcome,
        "failed_checks": failed_checks,
        "reason": reason,
        "expected_doc_recall": recall,
    }
    if outcome == "pass":
        return Score(value=CORRECT, metadata=metadata)
    if outcome == "fail":
        return Score(value=INCORRECT, metadata=metadata)
    return Score.unscored(reason=reason, metadata=metadata)


def _with_judge_verdicts(
    outcome: str,
    failed_checks: list[str],
    reason: str | None,
    verdicts: Sequence[tuple[str, Verdict]],
) -> tuple[str, list[str], str | None]:
    judge_failed = [f"judge:{name}" for name, v in verdicts if v.verdict == "fail"]
    judge_unable = [f"judge:{name}" for name, v in verdicts if v.verdict == "unable"]
    failed_checks = [*failed_checks, *judge_failed]
    if outcome != "pass":
        return outcome, failed_checks, reason
    if judge_failed:
        return "fail", failed_checks, reason
    if judge_unable:
        return "unable", failed_checks, judge_unable[0]
    return outcome, failed_checks, reason


def eval_task(
    cases: Sequence[EvalCase],
    *,
    round_dir: Path,
    kit_dir: Path,
    gateway_factory: GatewayFactory,
    executor: ThreadPoolExecutor,
    runs: int = 3,
    judge_client: JudgeClient | None = None,
    budget: RoundBudget,
    epoch_offset: int = 0,
) -> Task:
    guard = Guard(Catalog(kit_dir / "catalog.csv"))
    conductor = _Conductor(
        kit_dir=kit_dir,
        guard=guard,
        tracer=Tracer(round_dir / "traces", mask=guard.mask),
        gateway_factory=gateway_factory,
        handler_factory=partial(
            TraceCallbackHandler,
            reserve=ASSISTANT_CALL_RESERVE_USD,
            before_call=budget.before_call,
            on_cost=budget.on_cost,
            on_error=budget.on_error,
            strict=True,
        ),
    )
    cases_by_id = {case.id: case for case in cases}
    # the raw turns and replies stay here: the log only gets the masked copy
    conversations: dict[str, _Conversation] = {}
    # once the round cap refuses a call, the samples still waiting must not start
    stop = threading.Event()

    @solver
    def conversation_solver() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            case = cases_by_id[str(state.sample_id)]
            if stop.is_set():
                state.store.set("record", {"not_run": True})
                return state
            # a retry eval of the same round continues the epoch numbering of the first
            epoch = state.epoch + epoch_offset
            try:
                conversation = await asyncio.get_running_loop().run_in_executor(
                    executor, conductor.converse, case, epoch
                )
            except BudgetExceeded:
                stop.set()
                state.store.set("record", {"not_run": True})
                return state
            conversations[f"{state.sample_id}-e{epoch}"] = conversation
            state.store.set("record", conversation.stored)
            return state

        return solve

    @scorer(metrics=[accuracy()])
    def case_scorer() -> Scorer:
        async def score(state: TaskState, target: Target) -> Score:
            if state.store.get("record", {}).get("not_run"):
                return Score.unscored(
                    reason="budget",
                    metadata={
                        "outcome": "not_run",
                        "failed_checks": [],
                        "reason": "budget",
                        "expected_doc_recall": {},
                    },
                )
            session_id = f"{state.sample_id}-e{state.epoch + epoch_offset}"
            case = cases_by_id[str(state.sample_id)]
            conversation = conversations[session_id]
            result = evaluate_case(case, conversation.record, conversation.known_skus)
            recall = expected_doc_recall(case.expected_doc, conversation.record)
            outcome, failed_checks, reason = (
                result.outcome,
                result.failed_checks,
                result.reason,
            )
            if judge_client is not None and outcome != "unable":
                judged = Judged(session_id, case, conversation.transcript)
                loop = asyncio.get_running_loop()
                verdicts = []
                try:
                    for entry in _entries(case, None):
                        verdict = await loop.run_in_executor(
                            executor,
                            _judge_charged,
                            entry,
                            judged,
                            judge_client,
                            round_dir / "judge.jsonl",
                            budget,
                        )
                        verdicts.append((entry.criterion, verdict))
                except BudgetExceeded:
                    stop.set()
                    return Score.unscored(
                        reason="budget",
                        metadata={
                            "outcome": "not_run",
                            "failed_checks": [],
                            "reason": "budget",
                            "expected_doc_recall": {},
                        },
                    )
                except (
                    JudgeReplyError,
                    anthropic.APIError,
                    UnknownPriceError,
                ) as error:
                    # the conversation is paid for: keep it, so `--rejudge` can read it again;
                    # an API or price failure also stops the round, the rest would fail the same
                    if not isinstance(error, JudgeReplyError):
                        budget.halted = True
                        budget.price_unknown |= isinstance(error, UnknownPriceError)
                        stop.set()
                    return Score.unscored(
                        reason=JUDGE_UNPARSABLE,
                        metadata={
                            "outcome": outcome,
                            "failed_checks": failed_checks,
                            "reason": JUDGE_UNPARSABLE,
                            "expected_doc_recall": recall,
                        },
                    )
                outcome, failed_checks, reason = _with_judge_verdicts(
                    outcome, failed_checks, reason, verdicts
                )
            return _score_of(outcome, failed_checks, reason, recall)

        return score

    samples = []
    for case in cases:
        kwargs = to_sample_kwargs(case)
        kwargs["input"] = [
            {**message, "content": guard.mask(message["content"])}
            for message in kwargs["input"]
        ]
        metadata = kwargs["metadata"]
        for key in ("checks", "reference"):
            metadata[key] = _masked(guard, metadata[key])
        samples.append(Sample(**kwargs))
    return Task(
        dataset=samples,
        solver=conversation_solver(),
        scorer=case_scorer(),
        epochs=Epochs(runs, f"at_least_{runs}"),
        fail_on_error=False,
        metadata={"epoch_offset": epoch_offset},
    )


def run_eval(task: Task, *, round_dir: Path, max_samples: int = 8) -> EvalLog:
    [log] = inspect_eval(
        task,
        model=MOCK_MODEL,
        log_dir=str(round_dir),
        retry_on_error=0,
        max_samples=max_samples,
        display="none",
    )
    return log
