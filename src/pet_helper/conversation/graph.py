from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from pet_helper.conversation.state import ConversationState
from pet_helper.gateway import ModelGateway, ModelUnavailable
from pet_helper.guard import Guard

_HANDOFF_REPLY = "Vou encaminhar seu atendimento para um atendente humano."


def _input_gate_node(state: ConversationState) -> dict[str, Any]:
    return {
        "tool_rounds": 0,
        "regen_used": False,
        "steps_counted": frozenset(),
        "injection_suspected": False,
        "failures": dict(state.get("failures") or {}),
        "ended": bool(state.get("ended", False)),
        "escalation": None,
        "escalation_cause": None,
    }


def _route_after_input_gate(state: ConversationState) -> str:
    if state.get("ended"):
        return "handoff"
    return "input_guard"


def _route_after_agent(state: ConversationState) -> str:
    if state.get("escalation") is not None:
        return "handoff"
    messages = state.get("messages", ())
    if messages:
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
    return "output_guard"


def _tools_node(state: ConversationState) -> dict[str, Any]:
    return {}


def _record_node(state: ConversationState) -> dict[str, Any]:
    return {"tool_rounds": state.get("tool_rounds", 0) + 1}


def _route_after_record(state: ConversationState) -> str:
    if state.get("escalation") is not None:
        return "handoff"
    return "agent"


def _output_guard_node(state: ConversationState) -> dict[str, Any]:
    return {}


def _route_after_output_guard(state: ConversationState) -> str:
    return END


def _handoff_node(state: ConversationState) -> dict[str, Any]:
    return {
        "messages": [AIMessage(_HANDOFF_REPLY)],
        "ended": True,
    }


def build_graph(
    gateway: ModelGateway,
    guard: Guard,
    tools: Sequence[Any] = (),
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    def input_guard_node(state: ConversationState) -> dict[str, Any]:
        verdict = guard.inspect_input(state.get("user_text", ""))
        return {
            "messages": [HumanMessage(verdict.model_text)],
            "user_text": "",
            "session_cpf": verdict.cpf,
            "injection_suspected": verdict.injection_suspected,
        }

    def agent_node(state: ConversationState) -> dict[str, Any]:
        try:
            result = gateway.complete(state.get("messages", ()), tools)
        except ModelUnavailable:
            return {
                "escalation": "repeated_failure",
                "escalation_cause": "model_unavailable",
                "ended": True,
            }
        if result.refused:
            return {
                "escalation": "repeated_failure",
                "escalation_cause": "refusal",
                "ended": True,
            }
        return {"messages": [result.message]}

    builder = StateGraph(ConversationState)
    builder.add_node("input_gate", _input_gate_node)
    builder.add_node("input_guard", input_guard_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", _tools_node)
    builder.add_node("record", _record_node)
    builder.add_node("output_guard", _output_guard_node)
    builder.add_node("handoff", _handoff_node)

    builder.add_edge(START, "input_gate")
    builder.add_conditional_edges(
        "input_gate",
        _route_after_input_gate,
        {"input_guard": "input_guard", "handoff": "handoff"},
    )
    builder.add_edge("input_guard", "agent")
    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "output_guard": "output_guard", "handoff": "handoff"},
    )
    builder.add_edge("tools", "record")
    builder.add_conditional_edges(
        "record",
        _route_after_record,
        {"agent": "agent", "handoff": "handoff"},
    )
    builder.add_conditional_edges(
        "output_guard",
        _route_after_output_guard,
        {"agent": "agent", "handoff": "handoff", END: END},
    )
    builder.add_edge("handoff", END)

    active_checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
    return builder.compile(checkpointer=active_checkpointer)
