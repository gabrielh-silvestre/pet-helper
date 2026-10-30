import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from pet_helper.conversation.state import ConversationState, record_step_outcome
from pet_helper.knowledge import PolicyHit
from pet_helper.orders import Found, NeedsIdentification, NotFound, Unavailable
from pet_helper.orders.models import ORDER_NUMBER_PATTERN

_ORDER_TOOL = "get_order"


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


def _order_step_outcome(
    result: ToolMessage, args: dict[str, Any]
) -> tuple[str, bool] | None:
    if result.status == "error":
        order_number = args.get("order_number")
        valid = isinstance(order_number, str) and re.fullmatch(
            ORDER_NUMBER_PATTERN, order_number
        )
        return ("order_lookup" if valid else "order_number", False)
    match result.artifact:
        case Found():
            return "order_lookup", True
        case NotFound() | Unavailable():
            return "order_lookup", False
        case NeedsIdentification():
            return "identification", False
    return None


def record_node(state: ConversationState) -> dict[str, Any]:
    results, args_by_call_id = _latest_tool_round(state.get("messages", []))
    failures = dict(state.get("failures") or {})
    counted = frozenset(state.get("steps_counted", ()))
    hits: list[PolicyHit] = []
    for result in results:
        if isinstance(result.artifact, list):
            hits.extend(item for item in result.artifact if isinstance(item, PolicyHit))
        if result.name != _ORDER_TOOL:
            continue
        outcome = _order_step_outcome(
            result, args_by_call_id.get(result.tool_call_id, {})
        )
        if outcome is not None:
            failures, counted = record_step_outcome(failures, counted, *outcome)
    return {
        "tool_rounds": state.get("tool_rounds", 0) + 1,
        "retrieved": tuple(hits),
        "failures": failures,
        "steps_counted": counted,
    }
