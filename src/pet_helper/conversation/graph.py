import pickle
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.conversation.record import record_node
from pet_helper.conversation.state import ConversationState
from pet_helper.gateway import ModelGateway, ModelUnavailable
from pet_helper.guard import Guard

HANDOFF_REPLY = "Vou encaminhar seu atendimento para um atendente humano."
_ALREADY_HANDED_OFF_REPLY = (
    "Sua conversa já foi encaminhada para um atendente humano, que dará "
    "continuidade ao atendimento."
)


class _PickleSerializer(JsonPlusSerializer):
    # shortcut: pickle only for the in-memory saver (artifacts keep their types, history stays prefix-stable); replace with a typed serde before any persistent checkpointer
    def __init__(self) -> None:
        super().__init__(pickle_fallback=True)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "pickle", pickle.dumps(obj)


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


def _route_after_record(state: ConversationState) -> str:
    if state.get("escalation") is not None:
        return "handoff"
    return "agent"


def _output_guard_node(state: ConversationState) -> dict[str, Any]:
    return {}


def _route_after_output_guard(state: ConversationState) -> str:
    return END


def _handoff_node(state: ConversationState) -> dict[str, Any]:
    reply = _ALREADY_HANDED_OFF_REPLY if state.get("ended") else HANDOFF_REPLY
    return {
        "messages": [AIMessage(reply)],
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
            # o histórico segue sem reescrita, para o prefixo ficar estável entre chamadas
            result = gateway.complete(
                [SystemMessage(SYSTEM_PROMPT), *state.get("messages", ())], tools
            )
        except ModelUnavailable:
            return {
                "escalation": "repeated_failure",
                "escalation_cause": "model_unavailable",
            }
        if result.refused:
            return {
                "escalation": "repeated_failure",
                "escalation_cause": "refusal",
            }
        return {"messages": [result.message]}

    builder = StateGraph(ConversationState)
    builder.add_node("input_gate", _input_gate_node)
    builder.add_node("input_guard", input_guard_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    builder.add_node("record", record_node)
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

    active_checkpointer = (
        checkpointer
        if checkpointer is not None
        else InMemorySaver(serde=_PickleSerializer())
    )
    return builder.compile(checkpointer=active_checkpointer)
