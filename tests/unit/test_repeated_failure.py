from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import pet_helper.conversation as conversation
from pet_helper.conversation import Assistant, ToolRegistry
from pet_helper.gateway import (
    FakeGateway,
    ModelConfigError,
    ModelResult,
    ModelUnavailable,
)
from pet_helper.orders.api import FAILURE_ENV

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SESSION_ID = "session-repeated-failure"
CPF_TEXT = "Meu CPF é 000.000.001-91."
UNKNOWN_ORDER = "PED-99999"
OWN_ORDER = "PED-10001"
LOOP_LENGTH = 200

STEPS = ["order_lookup", "identification", "order_number"]
# (first customer text, get_order args that fail the step)
STEP_FAILURES = {
    "order_lookup": (
        f"{CPF_TEXT} Consulte o pedido {UNKNOWN_ORDER}",
        {"order_number": UNKNOWN_ORDER},
    ),
    "identification": (f"Consulte o pedido {OWN_ORDER}", {"order_number": OWN_ORDER}),
    "order_number": (f"{CPF_TEXT} Consulte meu pedido", {}),
}
LATER_TEXT = "Tente de novo, por favor."
# model-driven reasons and whether they outrank repeated_failure
OUTRANKING = ["animal_health_question", "strong_negative_sentiment"]


@pytest.fixture(autouse=True)
def _point_at_the_repo_kit(monkeypatch):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    monkeypatch.delenv(FAILURE_ENV, raising=False)


def _call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


def _tool_call_message(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            _call(name, args, f"call_{index}")
            for index, (name, args) in enumerate(calls, start=1)
        ],
    )


def _failing_order_call(step: str) -> AIMessage:
    return _tool_call_message(("get_order", STEP_FAILURES[step][1]))


def _config() -> dict[str, Any]:
    return {"configurable": {"thread_id": SESSION_ID}}


def _state(assistant: Assistant) -> dict[str, Any]:
    return assistant.graph.get_state(_config()).values


def _failing_script(step: str, turns: int) -> list[AIMessage | str]:
    return [
        item
        for _ in range(turns)
        for item in (_failing_order_call(step), "Não consegui concluir a consulta.")
    ]


def _customer_texts(step: str, turns: int) -> list[str]:
    return [STEP_FAILURES[step][0], *[LATER_TEXT] * (turns - 1)]


def _run_failing_turns(step: str, turns: int) -> tuple[Assistant, FakeGateway, list]:
    gateway = FakeGateway(_failing_script(step, turns))
    assistant = conversation.build_assistant(gateway=gateway)
    replies = [
        assistant.turn(SESSION_ID, text) for text in _customer_texts(step, turns)
    ]
    return assistant, gateway, replies


def _looping_model() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[_call("search_policies", {"query": "frete"}, f"call_{n}")],
        )
        for n in range(LOOP_LENGTH)
    ]


def _looping_assistant() -> tuple[Assistant, FakeGateway]:
    gateway = FakeGateway(_looping_model())
    return conversation.build_assistant(gateway=gateway), gateway


def _with_recursion_limit(assistant: Assistant, monkeypatch, limit: int) -> None:
    original = assistant.graph.invoke

    def invoke(graph_input, config=None, **kwargs):
        return original(
            graph_input, {**(config or {}), "recursion_limit": limit}, **kwargs
        )

    monkeypatch.setattr(assistant.graph, "invoke", invoke)


def _visited_nodes(assistant: Assistant, text: str) -> list[str]:
    chunks = assistant.graph.stream(
        {"user_text": text}, _config(), stream_mode="updates"
    )
    return [node for chunk in chunks for node in chunk]


@pytest.mark.parametrize("step", STEPS)
def test_it_should_not_escalate_on_the_second_consecutive_failing_turn(step):
    _, _, replies = _run_failing_turns(step, 2)

    assert replies[-1].escalation is None


@pytest.mark.parametrize("step", STEPS)
def test_it_should_escalate_with_repeated_failure_on_the_third_consecutive_failing_turn(
    step,
):
    _, _, replies = _run_failing_turns(step, 3)

    assert replies[-1].escalation == "repeated_failure"


@pytest.mark.parametrize("step", STEPS)
def test_it_should_record_the_failing_step_as_the_escalation_cause(step):
    assistant, _, _ = _run_failing_turns(step, 3)

    assert _state(assistant)["escalation_cause"] == f"step:{step}"


@pytest.mark.parametrize("step", STEPS)
def test_it_should_end_the_third_failing_turn_with_the_handoff_without_calling_the_model_again(
    step,
):
    assistant, gateway, replies = _run_failing_turns(step, 3)

    assert len(gateway.calls) == 5
    assert "atendente" in replies[-1].text
    assert "step:" not in replies[-1].text
    assert _state(assistant)["ended"] is True


@pytest.mark.parametrize("step", STEPS)
def test_it_should_keep_the_step_counter_between_turns(step):
    assistant, _, _ = _run_failing_turns(step, 2)

    assert _state(assistant)["failures"][step] == 2


def test_it_should_never_call_the_model_on_a_turn_after_a_repeated_failure_handoff():
    assistant, gateway, _ = _run_failing_turns("order_lookup", 3)

    reply = assistant.turn(SESSION_ID, "Alô?")

    assert len(gateway.calls) == 5
    assert reply.escalation == "repeated_failure"


def test_it_should_reset_the_counter_when_the_same_step_succeeds_between_failures():
    gateway = FakeGateway(
        [
            _failing_order_call("order_lookup"),
            "Não achei.",
            _failing_order_call("order_lookup"),
            "Ainda não achei.",
            _tool_call_message(("get_order", {"order_number": OWN_ORDER})),
            "Achei o pedido.",
            _failing_order_call("order_lookup"),
            "Não achei.",
            _failing_order_call("order_lookup"),
            "Não achei de novo.",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)
    texts = [STEP_FAILURES["order_lookup"][0], *[LATER_TEXT] * 4]

    replies = [assistant.turn(SESSION_ID, text) for text in texts]

    assert [reply.escalation for reply in replies] == [None] * 5
    assert _state(assistant)["failures"]["order_lookup"] == 2


def test_it_should_not_add_up_failures_of_different_steps():
    gateway = FakeGateway(
        [
            _failing_order_call("order_lookup"),
            "Não achei.",
            _failing_order_call("order_number"),
            "Preciso do número do pedido.",
            _failing_order_call("order_lookup"),
            "Não achei.",
            _failing_order_call("order_number"),
            "Preciso do número do pedido.",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)
    texts = [STEP_FAILURES["order_lookup"][0], *[LATER_TEXT] * 3]

    replies = [assistant.turn(SESSION_ID, text) for text in texts]

    assert [reply.escalation for reply in replies] == [None] * 4


def test_it_should_count_a_step_once_per_turn_when_the_model_retries_the_failing_tool():
    gateway = FakeGateway(
        [
            _failing_order_call("order_lookup"),
            _failing_order_call("order_lookup"),
            _failing_order_call("order_lookup"),
            "Não consegui encontrar esse pedido.",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)

    reply = assistant.turn(SESSION_ID, STEP_FAILURES["order_lookup"][0])

    assert reply.escalation is None
    assert _state(assistant)["failures"]["order_lookup"] == 1


def test_it_should_count_a_step_once_per_turn_when_one_model_message_makes_several_failing_calls():
    failing_args = STEP_FAILURES["order_lookup"][1]
    gateway = FakeGateway(
        [
            _tool_call_message(
                ("get_order", failing_args),
                ("get_order", failing_args),
                ("get_order", failing_args),
            ),
            "Não consegui encontrar esse pedido.",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)

    reply = assistant.turn(SESSION_ID, STEP_FAILURES["order_lookup"][0])

    assert reply.escalation is None
    assert _state(assistant)["failures"]["order_lookup"] == 1


def test_it_should_count_only_the_order_number_step_for_a_call_without_number_and_without_session_cpf():
    gateway = FakeGateway([_failing_order_call("order_number"), "Qual o número?"])
    assistant = conversation.build_assistant(gateway=gateway)

    assistant.turn(SESSION_ID, "Quero saber do meu pedido")

    failures = _state(assistant)["failures"]
    assert failures.get("order_number") == 1
    assert failures.get("identification", 0) == 0


def test_it_should_never_count_an_uncovered_topic_as_a_failure():
    turns = 5
    gateway = FakeGateway(
        [
            item
            for _ in range(turns)
            for item in (
                _tool_call_message(
                    ("search_policies", {"query": "bitcoin criptomoeda dividendos"})
                ),
                "Não tenho essa informação nas políticas da petx.",
            )
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)

    replies = [
        assistant.turn(SESSION_ID, "Vocês aceitam bitcoin?") for _ in range(turns)
    ]

    assert [reply.escalation for reply in replies] == [None] * turns
    assert all(count == 0 for count in _state(assistant)["failures"].values())


def _registry_with(*tools: Any) -> ToolRegistry:
    registry = ToolRegistry()
    for registered in tools:
        registry.register(registered)
    return registry


@tool("get_order")
def _exploding_get_order(order_number: str) -> str:
    """Look up an order."""
    raise RuntimeError("order backend exploded")


@tool("search_policies")
def _exploding_search_policies(query: str) -> str:
    """Search the policies."""
    raise RuntimeError("policy index exploded")


def test_it_should_count_an_exception_raised_inside_get_order_as_an_order_lookup_failure():
    gateway = FakeGateway(
        [_tool_call_message(("get_order", {"order_number": OWN_ORDER})), "Falhou."]
    )
    assistant = conversation.build_assistant(
        gateway=gateway, registry=_registry_with(_exploding_get_order)
    )

    assistant.turn(SESSION_ID, "Consulte o pedido PED-10001")

    assert _state(assistant)["failures"]["order_lookup"] == 1


def test_it_should_escalate_after_three_turns_of_exceptions_inside_get_order():
    call = _tool_call_message(("get_order", {"order_number": OWN_ORDER}))
    gateway = FakeGateway(
        [call, "Falhou.", call, "Falhou de novo.", call, "Falhou outra vez."]
    )
    assistant = conversation.build_assistant(
        gateway=gateway, registry=_registry_with(_exploding_get_order)
    )

    replies = [
        assistant.turn(SESSION_ID, "Consulte o pedido PED-10001"),
        assistant.turn(SESSION_ID, LATER_TEXT),
        assistant.turn(SESSION_ID, LATER_TEXT),
    ]

    assert replies[-1].escalation == "repeated_failure"
    assert _state(assistant)["escalation_cause"] == "step:order_lookup"


def test_it_should_return_an_error_tool_message_to_the_model_for_an_exception_in_another_tool():
    gateway = FakeGateway(
        [
            _tool_call_message(("search_policies", {"query": "frete"})),
            "Não consegui consultar as políticas.",
        ]
    )
    assistant = conversation.build_assistant(
        gateway=gateway, registry=_registry_with(_exploding_search_policies)
    )

    assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    tool_results = [m for m in gateway.calls[1] if m.type == "tool"]
    assert [result.status for result in tool_results] == ["error"]


def test_it_should_not_count_an_exception_in_another_tool_for_any_step():
    call = _tool_call_message(("search_policies", {"query": "frete"}))
    gateway = FakeGateway([call, "Falhou.", call, "Falhou de novo.", call, "Falhou."])
    assistant = conversation.build_assistant(
        gateway=gateway, registry=_registry_with(_exploding_search_policies)
    )

    replies = [assistant.turn(SESSION_ID, "Qual o prazo de entrega?") for _ in range(3)]

    assert [reply.escalation for reply in replies] == [None] * 3
    assert all(count == 0 for count in _state(assistant)["failures"].values())


def test_it_should_escalate_with_repeated_failure_when_the_tool_rounds_hit_the_cap():
    assistant, _ = _looping_assistant()

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert reply.escalation == "repeated_failure"


def test_it_should_record_loop_cap_as_the_cause_when_the_tool_rounds_hit_the_cap():
    assistant, _ = _looping_assistant()

    assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    values = _state(assistant)
    assert values["escalation_cause"] == "loop_cap"
    assert values["ended"] is True


def test_it_should_stop_calling_the_model_once_the_tool_rounds_hit_the_cap():
    assistant, gateway = _looping_assistant()

    assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert len(gateway.calls) < LOOP_LENGTH


def test_it_should_still_answer_when_the_model_makes_one_tool_round_fewer_than_the_cap():
    probe, probe_gateway = _looping_assistant()
    probe.turn("probe-session", "Qual o prazo de entrega?")
    cap = len(probe_gateway.calls)
    script = [*_looping_model()[: cap - 1], "O prazo é de 3 dias úteis."]
    assistant = conversation.build_assistant(gateway=FakeGateway(script))

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert reply.text == "O prazo é de 3 dias úteis."
    assert reply.escalation is None


def test_it_should_map_a_recursion_error_to_repeated_failure_without_raising_from_turn(
    monkeypatch,
):
    assistant, _ = _looping_assistant()
    _with_recursion_limit(assistant, monkeypatch, 5)

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert reply.escalation == "repeated_failure"
    assert "atendente" in reply.text


def test_it_should_persist_the_recursion_handoff_in_the_checkpoint(monkeypatch):
    assistant, _ = _looping_assistant()
    _with_recursion_limit(assistant, monkeypatch, 5)

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    values = _state(assistant)
    assert values["escalation"] == "repeated_failure"
    assert values["ended"] is True
    assert values["messages"][-1].text == reply.text


def test_it_should_not_call_the_model_on_the_turn_after_a_recursion_handoff(
    monkeypatch,
):
    assistant, gateway = _looping_assistant()
    _with_recursion_limit(assistant, monkeypatch, 5)
    assistant.turn(SESSION_ID, "Qual o prazo de entrega?")
    calls_before = len(gateway.calls)

    assistant.turn(SESSION_ID, "Alô?")

    assert len(gateway.calls) == calls_before


def test_it_should_escalate_with_repeated_failure_on_the_first_refusal():
    gateway = FakeGateway([ModelResult(message=AIMessage(""), refused=True)])
    assistant = conversation.build_assistant(gateway=gateway)

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert reply.escalation == "repeated_failure"
    assert _state(assistant)["escalation_cause"] == "refusal"


def test_it_should_go_from_agent_straight_to_handoff_on_a_refusal():
    gateway = FakeGateway([ModelResult(message=AIMessage(""), refused=True)])
    assistant = conversation.build_assistant(gateway=gateway)

    visited = _visited_nodes(assistant, "Qual o prazo de entrega?")

    assert visited == ["input_gate", "input_guard", "agent", "handoff"]


def test_it_should_escalate_with_repeated_failure_when_the_model_is_unavailable():
    assistant = conversation.build_assistant(gateway=FakeGateway([ModelUnavailable()]))

    reply = assistant.turn(SESSION_ID, "Qual o prazo de entrega?")

    assert reply.escalation == "repeated_failure"
    assert "atendente" in reply.text
    assert _state(assistant)["escalation_cause"] == "model_unavailable"


def test_it_should_go_from_agent_straight_to_handoff_when_the_model_is_unavailable():
    assistant = conversation.build_assistant(gateway=FakeGateway([ModelUnavailable()]))

    visited = _visited_nodes(assistant, "Qual o prazo de entrega?")

    assert visited == ["input_gate", "input_guard", "agent", "handoff"]


def test_it_should_let_a_model_config_error_propagate_out_of_turn():
    gateway = FakeGateway([ModelConfigError("invalid API key")])
    assistant = conversation.build_assistant(gateway=gateway)

    with pytest.raises(ModelConfigError):
        assistant.turn(SESSION_ID, "Qual o prazo de entrega?")


def _two_failing_turns_then(third_calls: list[tuple[str, dict[str, Any]]]):
    gateway = FakeGateway(
        [
            _failing_order_call("order_lookup"),
            "Não achei.",
            _failing_order_call("order_lookup"),
            "Ainda não achei.",
            _tool_call_message(*third_calls),
            "Posso ajudar com mais alguma coisa?",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway)
    assistant.turn(SESSION_ID, STEP_FAILURES["order_lookup"][0])
    assistant.turn(SESSION_ID, LATER_TEXT)
    return assistant


def _third_turn_calls(reason: str, escalate_first: bool):
    failing = ("get_order", STEP_FAILURES["order_lookup"][1])
    escalate = ("escalate_to_human", {"reason": reason})
    return [escalate, failing] if escalate_first else [failing, escalate]


@pytest.mark.parametrize("escalate_first", [False, True])
@pytest.mark.parametrize("reason", OUTRANKING)
def test_it_should_let_a_higher_priority_reason_beat_repeated_failure_in_the_same_turn(
    reason, escalate_first
):
    assistant = _two_failing_turns_then(_third_turn_calls(reason, escalate_first))

    reply = assistant.turn(SESSION_ID, LATER_TEXT)

    assert reply.escalation == reason
    assert _state(assistant)["escalation_cause"] == "escalate_to_human"


@pytest.mark.parametrize("escalate_first", [False, True])
def test_it_should_let_repeated_failure_beat_an_explicit_request_in_the_same_turn(
    escalate_first,
):
    assistant = _two_failing_turns_then(
        _third_turn_calls("explicit_request", escalate_first)
    )

    reply = assistant.turn(SESSION_ID, LATER_TEXT)

    assert reply.escalation == "repeated_failure"
    assert _state(assistant)["escalation_cause"] == "step:order_lookup"
