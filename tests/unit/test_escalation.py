import json
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import pet_helper.conversation as conversation
from pet_helper.catalog import Catalog
from pet_helper.conversation import Reply
from pet_helper.gateway import FakeGateway
from pet_helper.knowledge import PolicyIndex
from pet_helper.orders import OrderService
from pet_helper.orders.api import MockOrderApi

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SESSION_ID = "session-escalation"

# highest priority first
PRIORITY = [
    "animal_health_question",
    "strong_negative_sentiment",
    "repeated_failure",
    "explicit_request",
]
MODEL_DRIVEN_REASONS = [
    "animal_health_question",
    "strong_negative_sentiment",
    "explicit_request",
]
PLANTED_DIAGNOSIS = "O diagnóstico do seu cachorro é gastrite."


@pytest.fixture(autouse=True)
def _point_at_the_repo_kit(monkeypatch):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))


def _escalation_call(*reasons: Any, text: str = "") -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[
            {
                "name": "escalate_to_human",
                "args": {"reason": reason},
                "id": f"call_escalate_{index}",
            }
            for index, reason in enumerate(reasons, start=1)
        ],
    )


def _escalated_assistant(*reasons: Any, text: str = "", extra_script=()):
    gateway = FakeGateway([_escalation_call(*reasons, text=text), *extra_script])
    return conversation.build_assistant(gateway=gateway), gateway


def _visited_nodes(assistant, session_id: str, text: str) -> list[str]:
    chunks = assistant.graph.stream(
        {"user_text": text},
        {"configurable": {"thread_id": session_id}},
        stream_mode="updates",
    )
    return [node for chunk in chunks for node in chunk]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pair
        for pair in permutations(PRIORITY, 2)
        if PRIORITY.index(pair[0]) < PRIORITY.index(pair[1])
    ],
)
def test_it_should_pick_the_higher_priority_reason_from_any_pair(first, second):
    picked_forward = conversation.highest_priority_reason([first, second])
    picked_backward = conversation.highest_priority_reason([second, first])

    assert picked_forward == first
    assert picked_backward == first


@pytest.mark.parametrize("reason", PRIORITY)
def test_it_should_return_a_lone_reason_as_it_is(reason):
    picked = conversation.highest_priority_reason([reason])

    assert picked == reason


@pytest.mark.parametrize("reason", MODEL_DRIVEN_REASONS)
def test_it_should_carry_the_reason_in_reply_escalation_when_the_model_escalates(
    reason,
):
    assistant, _ = _escalated_assistant(reason)

    reply = assistant.turn(SESSION_ID, "Quero falar com alguém")

    assert reply.escalation == reason


@pytest.mark.parametrize("reason", MODEL_DRIVEN_REASONS)
def test_it_should_route_the_escalation_tool_call_to_the_handoff_node(reason):
    assistant, _ = _escalated_assistant(reason)

    visited = _visited_nodes(assistant, SESSION_ID, "Quero falar com alguém")

    assert visited[-4:] == ["agent", "tools", "record", "handoff"]


@pytest.mark.parametrize("reason", MODEL_DRIVEN_REASONS)
def test_it_should_not_call_the_model_again_after_it_escalates(reason):
    assistant, gateway = _escalated_assistant(reason)

    assistant.turn(SESSION_ID, "Quero falar com alguém")

    assert len(gateway.calls) == 1


@pytest.mark.parametrize("reason", MODEL_DRIVEN_REASONS)
def test_it_should_announce_the_handoff_in_portuguese_without_the_reason(reason):
    assistant, _ = _escalated_assistant(reason)

    reply = assistant.turn(SESSION_ID, "Quero falar com alguém")

    assert "atendente" in reply.text
    assert reason not in reply.text
    assert "_" not in reply.text


def test_it_should_use_the_same_announcement_for_every_model_driven_reason():
    texts = set()
    for reason in MODEL_DRIVEN_REASONS:
        assistant, _ = _escalated_assistant(reason)
        texts.add(assistant.turn(SESSION_ID, "Quero falar com alguém").text)

    assert len(texts) == 1


def test_it_should_discard_the_model_text_written_next_to_the_escalation_call():
    assistant, _ = _escalated_assistant(
        "animal_health_question", text=PLANTED_DIAGNOSIS
    )

    reply = assistant.turn(SESSION_ID, "Meu cachorro vomitou, o que ele tem?")

    assert "gastrite" not in reply.text
    assert "diagnóstico" not in reply.text.lower()


@pytest.mark.parametrize(("first", "second"), permutations(MODEL_DRIVEN_REASONS, 2))
def test_it_should_record_one_reason_by_priority_when_the_model_calls_the_tool_twice(
    first, second
):
    expected = min((first, second), key=PRIORITY.index)
    assistant, _ = _escalated_assistant(first, second)

    reply = assistant.turn(SESSION_ID, "Estou furioso, quero um humano")

    assert reply.escalation == expected


@pytest.mark.parametrize("reason", ["repeated_failure", "made_up_reason"])
def test_it_should_return_a_tool_error_instead_of_escalating_for_a_reason_the_model_cannot_use(
    reason,
):
    assistant, gateway = _escalated_assistant(
        reason, extra_script=["Posso ajudar com outra coisa?"]
    )

    reply = assistant.turn(SESSION_ID, "Quero falar com alguém")

    tool_results = [m for m in gateway.calls[1] if isinstance(m, ToolMessage)]
    assert reply.escalation is None
    assert [result.status for result in tool_results] == ["error"]
    assert "not a valid tool" not in str(tool_results[0].content)


def test_it_should_offer_the_model_only_the_three_model_driven_reasons():
    catalog = Catalog(REPO_KIT_DIR / "catalog.csv")
    registry = conversation.build_tool_registry(
        policies=PolicyIndex.load(REPO_KIT_DIR / "policies"),
        catalog=catalog,
        orders=OrderService(MockOrderApi(REPO_KIT_DIR / "orders.json")),
    )
    escalate = {t.name: t for t in registry.tools}["escalate_to_human"]

    schema = json.dumps(escalate.tool_call_schema.model_json_schema())

    assert "repeated_failure" not in schema
    assert all(reason in schema for reason in MODEL_DRIVEN_REASONS)


@pytest.mark.parametrize("reason", MODEL_DRIVEN_REASONS)
def test_it_should_keep_the_session_ended_and_the_reason_in_the_checkpoint(reason):
    assistant, _ = _escalated_assistant(reason)
    config = {"configurable": {"thread_id": SESSION_ID}}

    assistant.turn(SESSION_ID, "Quero falar com alguém")

    values = assistant.graph.get_state(config).values
    assert values["escalation"] == reason
    assert values["ended"] is True


def test_it_should_never_call_the_model_on_a_later_turn_of_an_escalated_session():
    assistant, gateway = _escalated_assistant("explicit_request")
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    assistant.turn(SESSION_ID, "Alô? Ainda está aí?")

    assert len(gateway.calls) == 1


def test_it_should_keep_the_reason_in_the_reply_of_a_later_turn_of_an_escalated_session():
    assistant, _ = _escalated_assistant("strong_negative_sentiment")
    assistant.turn(SESSION_ID, "Isso é um absurdo!")

    reply = assistant.turn(SESSION_ID, "Alô? Ainda está aí?")

    assert reply.escalation == "strong_negative_sentiment"


def test_it_should_tell_the_customer_in_portuguese_that_the_conversation_was_already_forwarded():
    assistant, _ = _escalated_assistant("explicit_request")
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    reply = assistant.turn(SESSION_ID, "Alô? Ainda está aí?")

    assert isinstance(reply, Reply)
    assert "encaminhad" in reply.text


def test_it_should_answer_every_later_turn_of_an_escalated_session_with_the_same_text():
    assistant, _ = _escalated_assistant("explicit_request")
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    first_later = assistant.turn(SESSION_ID, "Alô?")
    second_later = assistant.turn(SESSION_ID, "Como está meu pedido?")

    assert first_later.text == second_later.text


def test_it_should_go_straight_from_input_gate_to_handoff_on_a_later_turn():
    assistant, _ = _escalated_assistant("explicit_request")
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    visited = _visited_nodes(assistant, SESSION_ID, "Alô? Ainda está aí?")

    assert visited == ["input_gate", "handoff"]


def test_it_should_keep_answering_other_sessions_after_one_session_escalates():
    assistant, gateway = _escalated_assistant(
        "explicit_request", extra_script=["Olá! Como posso ajudar?"]
    )
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    reply = assistant.turn("another-session", "Olá")

    assert reply == Reply(text="Olá! Como posso ajudar?")
    assert len(gateway.calls) == 2


def test_it_should_leave_no_raw_cpf_in_the_state_after_a_turn_following_an_escalation():
    assistant, _ = _escalated_assistant("explicit_request")
    assistant.turn(SESSION_ID, "Quero falar com alguém")

    assistant.turn(SESSION_ID, "Meu CPF é 000.000.001-91, alguém me responde?")

    values = assistant.graph.get_state(
        {"configurable": {"thread_id": SESSION_ID}}
    ).values
    assert values["user_text"] == ""
