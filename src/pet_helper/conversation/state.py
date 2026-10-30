from collections.abc import Mapping, Sequence, Set
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from pet_helper.knowledge import PolicyHit

type EscalationReason = Literal[
    "animal_health_question",
    "strong_negative_sentiment",
    "repeated_failure",
    "explicit_request",
]


def _keep_first[T](current: T | None, update: T | None) -> T | None:
    return current if current is not None else update


def _union_retrieved(
    current: Sequence[PolicyHit],
    update: Sequence[PolicyHit],
) -> tuple[PolicyHit, ...]:
    seen_sources: set[str] = set()
    merged: list[PolicyHit] = []
    for hit in (*current, *update):
        if hit.source not in seen_sources:
            seen_sources.add(hit.source)
            merged.append(hit)
    return tuple(merged)


class ConversationState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    user_text: str
    session_cpf: Annotated[str | None, _keep_first]
    failures: dict[str, int]
    retrieved: Annotated[tuple[PolicyHit, ...], _union_retrieved]
    escalation: Annotated[EscalationReason | None, _keep_first]
    escalation_cause: Annotated[str | None, _keep_first]
    ended: bool
    tool_rounds: int
    regen_used: bool
    steps_counted: frozenset[str]
    injection_suspected: bool


def record_step_outcome(
    failures: Mapping[str, int],
    steps_counted: Set[str],
    step: str,
    succeeded: bool,
) -> tuple[dict[str, int], frozenset[str]]:
    updated_failures = dict(failures)
    counted = frozenset(steps_counted)
    if succeeded:
        updated_failures[step] = 0
        return updated_failures, counted
    if step in counted:
        return updated_failures, counted
    updated_failures[step] = updated_failures.get(step, 0) + 1
    return updated_failures, counted | {step}
