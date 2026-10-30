import pickle
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError

from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.conversation.record import record_node
from pet_helper.conversation.state import ConversationState
from pet_helper.gateway import ModelGateway, ModelUnavailable
from pet_helper.guard import Guard, extract_skus

HANDOFF_REPLY = "Vou encaminhar seu atendimento para um atendente humano."
CPF_CONFLICT_REPLY = (
    "Já identifiquei esta conversa com outro CPF e não posso trocar de identificação "
    "no meio do atendimento. Para consultar dados de outro cliente, inicie uma nova "
    "conversa."
)
_TOOL_FAILURE_TEXT = (
    "A ferramenta falhou de forma inesperada. Diga ao cliente que não conseguiu "
    "concluir a consulta agora."
)
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})
_ALREADY_HANDED_OFF_REPLY = (
    "Sua conversa já foi encaminhada para um atendente humano, que dará "
    "continuidade ao atendimento."
)
_CORRECTIVE_MESSAGE_ID = "output-guard-corrective"


class _PickleSerializer(JsonPlusSerializer):
    # shortcut: pickle only for the in-memory saver (artifacts keep their types, history stays prefix-stable); replace with a typed serde before any persistent checkpointer
    def __init__(self) -> None:
        super().__init__(pickle_fallback=True)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return "pickle", pickle.dumps(obj)


def _repeated_failure(cause: str) -> dict[str, Any]:
    return {"escalation": "repeated_failure", "escalation_cause": cause}


def _tool_error_text(error: Exception) -> str:
    # só o erro de argumentos é seguro para o modelo; o resto pode trazer URL ou CPF
    if isinstance(error, ToolInvocationError):
        return error.message
    return _TOOL_FAILURE_TEXT


def _conflicts_with(session_cpf: str | None, new_cpf: str | None) -> bool:
    return session_cpf is not None and new_cpf is not None and session_cpf != new_cpf


def _input_gate_node(state: ConversationState) -> dict[str, Any]:
    return {
        "tool_rounds": 0,
        "regen_used": False,
        "steps_counted": frozenset(),
        "injection_suspected": False,
        "failures": dict(state.get("failures") or {}),
        "ended": bool(state.get("ended", False)),
    }


def _route_after_input_gate(state: ConversationState) -> str:
    if state.get("ended"):
        return "handoff"
    return "input_guard"


def _route_after_input_guard(state: ConversationState) -> str:
    return "cpf_conflict" if state.get("cpf_conflict") else "agent"


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


def _route_after_output_guard(state: ConversationState) -> str:
    if state.get("escalation") is not None:
        return "handoff"
    if state["messages"][-1].id == _CORRECTIVE_MESSAGE_ID:
        return "agent"
    return END


def _corrective_text(
    invalid_skus: Sequence[str], unretrieved_sources: Sequence[str]
) -> str:
    problems = []
    if invalid_skus:
        problems.append(f"SKUs que não existem no catálogo: {', '.join(invalid_skus)}")
    if unretrieved_sources:
        problems.append(
            "fontes que não foram recuperadas por search_policies: "
            f"{', '.join(unretrieved_sources)}"
        )
    return (
        f"Sua resposta anterior citou {'; '.join(problems)}. Reescreva a resposta "
        "sem eles, usando só SKUs devolvidos por search_catalog e fontes "
        "recuperadas por search_policies."
    )


def _without_thinking(message: AIMessage) -> AIMessage:
    if not isinstance(message.content, list):
        return message
    kept = [
        block
        for block in message.content
        if not (isinstance(block, dict) and block.get("type") in _THINKING_BLOCK_TYPES)
    ]
    return message.model_copy(update={"content": kept})


def _without_regeneration(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    ids = [message.id for message in messages]
    if _CORRECTIVE_MESSAGE_ID not in ids:
        return []
    corrective_index = ids.index(_CORRECTIVE_MESSAGE_ID)
    removals = [
        RemoveMessage(id=message.id)
        for message in messages[corrective_index - 1 : corrective_index + 1]
        if message.id is not None
    ]
    # o thinking da resposta regenerada não pode seguir no histórico: o provedor valida a assinatura
    stripped = [
        _without_thinking(message)
        for message in messages[corrective_index + 1 :]
        if isinstance(message, AIMessage)
    ]
    return [*removals, *stripped]


def handoff_state(reply: str, cause: str | None = None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "messages": [AIMessage(reply)],
        "user_text": "",
        "ended": True,
    }
    return state if cause is None else {**state, **_repeated_failure(cause)}


def _handoff_node(state: ConversationState) -> dict[str, Any]:
    return handoff_state(
        _ALREADY_HANDED_OFF_REPLY if state.get("ended") else HANDOFF_REPLY
    )


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
            "cpf_conflict": _conflicts_with(state.get("session_cpf"), verdict.cpf),
            "injection_suspected": verdict.injection_suspected,
        }

    def cpf_conflict_node(_state: ConversationState) -> dict[str, Any]:
        return {"messages": [AIMessage(CPF_CONFLICT_REPLY)]}

    def agent_node(state: ConversationState) -> dict[str, Any]:
        try:
            # o histórico segue sem reescrita, para o prefixo ficar estável entre chamadas
            result = gateway.complete(
                [SystemMessage(SYSTEM_PROMPT), *state.get("messages", ())], tools
            )
        except ModelUnavailable:
            return _repeated_failure("model_unavailable")
        if result.refused:
            return _repeated_failure("refusal")
        if result.truncated:
            return _repeated_failure("truncated_reply")
        if not result.message.tool_calls and not result.message.text.strip():
            return _repeated_failure("empty_reply")
        return {"messages": [result.message]}

    def output_guard_node(state: ConversationState) -> dict[str, Any]:
        messages = state.get("messages", [])
        customer_skus = {
            sku
            for message in messages
            if isinstance(message, HumanMessage)
            and message.id != _CORRECTIVE_MESSAGE_ID
            for sku in extract_skus(message.text)
        }
        verdict = guard.check_reply(
            messages[-1].text, [hit.title for hit in state.get("retrieved", ())]
        )
        invalid_skus = [sku for sku in verdict.invalid_skus if sku not in customer_skus]
        regenerated = state.get("regen_used", False)
        if not invalid_skus and not verdict.unretrieved_sources:
            return {"messages": _without_regeneration(messages)} if regenerated else {}
        if regenerated:
            return _repeated_failure("output_guard")
        corrective = HumanMessage(
            _corrective_text(invalid_skus, verdict.unretrieved_sources),
            id=_CORRECTIVE_MESSAGE_ID,
        )
        return {"messages": [corrective], "regen_used": True}

    builder = StateGraph(ConversationState)
    builder.add_node("input_gate", _input_gate_node)
    builder.add_node("input_guard", input_guard_node)
    builder.add_node("cpf_conflict", cpf_conflict_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error_text))
    builder.add_node("record", record_node)
    builder.add_node("output_guard", output_guard_node)
    builder.add_node("handoff", _handoff_node)

    builder.add_edge(START, "input_gate")
    builder.add_conditional_edges(
        "input_gate",
        _route_after_input_gate,
        {"input_guard": "input_guard", "handoff": "handoff"},
    )
    builder.add_conditional_edges(
        "input_guard",
        _route_after_input_guard,
        {"agent": "agent", "cpf_conflict": "cpf_conflict"},
    )
    builder.add_edge("cpf_conflict", END)
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
