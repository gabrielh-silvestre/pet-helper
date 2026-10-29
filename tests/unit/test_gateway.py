from langchain_core.messages import AIMessage, HumanMessage

from pet_helper.gateway import (
    DEFAULT_REPLY,
    FakeGateway,
    ModelResult,
    default_gateway,
)


def test_it_should_wrap_a_scripted_reply_in_an_ai_message():
    gateway = FakeGateway(replies=["hello"])

    result = gateway.complete([HumanMessage("hi")])

    assert isinstance(result, ModelResult)
    assert isinstance(result.message, AIMessage)
    assert result.message.text == "hello"


def test_it_should_return_scripted_replies_in_order_and_cycle():
    gateway = FakeGateway(replies=["first", "second"])

    texts = [gateway.complete([HumanMessage("hi")]).message.text for _ in range(3)]

    assert texts == ["first", "second", "first"]


def test_it_should_record_a_copy_of_the_messages_of_each_call():
    gateway = FakeGateway()
    messages = [HumanMessage("first")]
    gateway.complete(messages)
    messages.append(HumanMessage("second"))
    gateway.complete(messages)

    assert [[m.content for m in call] for call in gateway.calls] == [
        ["first"],
        ["first", "second"],
    ]


def test_it_should_answer_the_default_reply_from_the_default_gateway_in_the_skeleton():
    """Skeleton-only behaviour: the first real adapter unit replaces it."""
    gateway = default_gateway()

    result = gateway.complete([HumanMessage("hi")])

    assert result.message.text == DEFAULT_REPLY
