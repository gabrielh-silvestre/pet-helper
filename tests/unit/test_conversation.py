import dataclasses

import pytest

from pet_helper.conversation import Assistant, Reply, build_graph
from pet_helper.gateway import FakeGateway


def test_it_should_reply_with_the_text_of_the_model_answer():
    assistant = Assistant(FakeGateway(replies=["scripted answer"]))

    reply = assistant.turn("session-1", "hello")

    assert reply == Reply(text="scripted answer", sources=(), escalation=None)


def test_it_should_reply_with_a_plain_string_text():
    assistant = Assistant(FakeGateway(replies=["scripted answer"]))

    reply = assistant.turn("session-1", "hello")

    assert type(reply.text) is str


def test_it_should_send_the_gateway_only_the_human_message_of_the_turn():
    gateway = FakeGateway()
    assistant = Assistant(gateway)

    assistant.turn("session-1", "where is my order?")

    assert [(m.type, m.content) for m in gateway.calls[0]] == [
        ("human", "where is my order?")
    ]


def test_it_should_carry_no_history_between_turns_of_the_same_session():
    gateway = FakeGateway()
    assistant = Assistant(gateway)

    assistant.turn("session-1", "first")
    assistant.turn("session-1", "second")

    assert [len(call) for call in gateway.calls] == [1, 1]


def test_it_should_build_a_graph_with_only_the_agent_node():
    graph = build_graph(FakeGateway())

    assert set(graph.get_graph().nodes) == {"__start__", "agent", "__end__"}


def test_it_should_build_a_graph_without_a_checkpointer():
    graph = build_graph(FakeGateway())

    assert graph.checkpointer is None


def test_it_should_not_allow_a_reply_to_be_changed():
    reply = Reply(text="hello")

    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.text = "changed"  # type: ignore[misc]
