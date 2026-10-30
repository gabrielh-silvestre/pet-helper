import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from pet_helper.conversation.state import (
    ConversationState,
    EscalationReason,
    highest_priority_reason,
    record_step_outcome,
)
from pet_helper.conversation.tools import ESCALATE_TOOL, GET_ORDER_TOOL
from pet_helper.knowledge import PolicyHit
from pet_helper.orders import (
    ORDER_NUMBER_PATTERN,
    Found,
    NeedsIdentification,
    NotFound,
    Unavailable,
)

_STEP_FAILURE_LIMIT = 3
TOOL_ROUNDS_CAP = 8


def _latest_tool_round(
    messages: list[BaseMessage],
) -> tuple[list[ToolMessage], dict[str, dict[str, Any]]]:
    results: list[ToolMessage] = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        results.append(message)
    results.reverse()
    caller = messages[-len(results) - 1] if results else None
    args_by_call_id = (
        {call["id"]: call["args"] for call in caller.tool_calls}
        if isinstance(caller, AIMessage)
        else {}
    )
    return results, args_by_call_id


def _order_step_outcomes(
    result: ToolMessage, args: dict[str, Any]
) -> list[tuple[str, bool]]:
    if result.status == "error":
        order_number = args.get("order_number")
        valid = isinstance(order_number, str) and re.fullmatch(
            ORDER_NUMBER_PATTERN, order_number
        )
        return [("order_lookup" if valid else "order_number", False)]
    # a result that is not an error means a well-formed number reached the lookup
    match result.artifact:
        case Found():
            return [("order_number", True), ("order_lookup", True)]
        case NotFound() | Unavailable():
            return [("order_number", True), ("order_lookup", False)]
        case NeedsIdentification():
            return [("order_number", True), ("identification", False)]
    return []


def _repeated_failure_cause(failures: dict[str, int], tool_rounds: int) -> str | None:
    for step, count in failures.items():
        if count >= _STEP_FAILURE_LIMIT:
            return f"step:{step}"
    return "loop_cap" if tool_rounds >= TOOL_ROUNDS_CAP else None


def record_node(state: ConversationState) -> dict[str, Any]:
    results, args_by_call_id = _latest_tool_round(state.get("messages", []))
    failures = dict(state.get("failures") or {})
    counted = frozenset(state.get("steps_counted", ()))
    hits: list[PolicyHit] = []
    reasons: list[EscalationReason] = []
    for result in results:
        if isinstance(result.artifact, list):
            hits.extend(item for item in result.artifact if isinstance(item, PolicyHit))
        if result.name == ESCALATE_TOOL and result.status != "error":
            reasons.append(result.artifact)
        if result.name != GET_ORDER_TOOL:
            continue
        for step, succeeded in _order_step_outcomes(
            result, args_by_call_id.get(result.tool_call_id, {})
        ):
            failures, counted = record_step_outcome(failures, counted, step, succeeded)
    tool_rounds = state.get("tool_rounds", 0) + 1
    failure_cause = _repeated_failure_cause(failures, tool_rounds)
    if failure_cause is not None:
        reasons.append("repeated_failure")
    escalation = {}
    if reasons:
        reason = highest_priority_reason(reasons)
        escalation = {
            "escalation": reason,
            "escalation_cause": failure_cause
            if reason == "repeated_failure" and failure_cause is not None
            else ESCALATE_TOOL,
        }
    return {
        **escalation,
        "tool_rounds": tool_rounds,
        "retrieved": tuple(hits),
        "failures": failures,
        "steps_counted": counted,
    }
