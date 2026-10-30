import dataclasses

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from pet_helper.gateway import FakeGateway, ModelResult


def test_it_should_default_fell_back_to_false_on_model_result():
    message = AIMessage("hello")

    result = ModelResult(message=message)

    assert result.message == message
    assert result.fell_back is False


def test_it_should_default_truncated_to_false_on_model_result():
    message = AIMessage("hello")

    result = ModelResult(message=message)

    assert result.truncated is False


def test_it_should_default_refused_to_false_on_model_result():
    message = AIMessage("hello")

    result = ModelResult(message=message)

    assert result.refused is False


def test_it_should_keep_model_result_immutable():
    result = ModelResult(message=AIMessage("hello"))

    with pytest.raises(dataclasses.FrozenInstanceError) as exc_info:
        result.refused = True  # type: ignore[misc]

    assert exc_info.type is dataclasses.FrozenInstanceError


def test_it_should_wrap_a_scripted_string_in_a_fake_provider_model_result():
    gateway = FakeGateway(script=["hello"])

    result = gateway.complete([HumanMessage("hi")])

    assert result.message == AIMessage("hello")
    assert result.provider == "fake"
    assert result.fell_back is False
    assert result.refused is False


def test_it_should_preserve_tool_calls_on_a_scripted_ai_message():
    scripted_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_order",
                "args": {"order_number": "PED-10001"},
                "id": "call_1",
            }
        ],
    )
    gateway = FakeGateway([scripted_message])

    result = gateway.complete([HumanMessage("onde está meu pedido?")])

    assert result.provider == "fake"
    assert result.message.tool_calls == [
        {
            "name": "get_order",
            "args": {"order_number": "PED-10001"},
            "id": "call_1",
            "type": "tool_call",
        }
    ]


def test_it_should_return_mixed_script_items_in_order():
    tool_message = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_policies", "args": {"query": "frete"}, "id": "call_1"}
        ],
    )
    explicit_result = ModelResult(
        message=AIMessage("resposta via fallback"),
        provider="ollama",
        model="llama3.1:8b",
        fell_back=True,
        refused=False,
    )
    gateway = FakeGateway(["primeiro", tool_message, explicit_result])

    outputs = [gateway.complete([HumanMessage("turno")]) for _ in range(3)]

    assert outputs[0].message.text == "primeiro"
    assert outputs[0].provider == "fake"
    assert outputs[1].message == tool_message
    assert outputs[1].provider == "fake"
    assert outputs[2] == explicit_result


def test_it_should_record_a_defensive_copy_of_messages_on_each_call():
    first = HumanMessage("first")
    second = HumanMessage("second")
    gateway = FakeGateway(["reply-1", "reply-2"])
    messages = [first]

    gateway.complete(messages)
    messages.append(second)
    gateway.complete(messages)
    messages.clear()

    assert gateway.calls == [[first], [first, second]]


def test_it_should_raise_a_dedicated_error_instead_of_cycling_when_script_is_exhausted():
    gateway = FakeGateway(["only reply"])
    gateway.complete([HumanMessage("first")])

    with pytest.raises(Exception) as exc_info:
        gateway.complete([HumanMessage("second")])

    assert not isinstance(exc_info.value, (IndexError, StopIteration))
    assert type(exc_info.value).__module__.startswith("pet_helper.gateway")


def test_it_should_raise_the_dedicated_error_when_an_empty_script_is_called():
    gateway = FakeGateway([])

    with pytest.raises(Exception) as exc_info:
        gateway.complete([HumanMessage("first")])

    assert not isinstance(exc_info.value, (IndexError, StopIteration))
    assert type(exc_info.value).__module__.startswith("pet_helper.gateway")


def test_it_should_raise_a_scripted_exception_item_when_its_turn_arrives():
    scripted_error = RuntimeError("simulated failure")
    gateway = FakeGateway(["ok", scripted_error])
    first = gateway.complete([HumanMessage("turn 1")])

    with pytest.raises(RuntimeError) as exc_info:
        gateway.complete([HumanMessage("turn 2")])

    assert first.message.text == "ok"
    assert exc_info.value is scripted_error
