from typing import Any
from unittest.mock import MagicMock

import httpx
import ollama
import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from pet_helper.gateway import (
    ModelConfigError,
    ModelResult,
    ModelUnavailable,
    OllamaGateway,
    build_gateway,
)

OLLAMA_ENV_VARS = (
    "OLLAMA_BASE_URL",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_OLLAMA_MODEL",
    "PET_HELPER_FALLBACK",
)


@pytest.fixture(autouse=True)
def _clean_ollama_env(monkeypatch):
    for name in OLLAMA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _build_default_chat_model() -> Any:
    from langchain_ollama import ChatOllama

    gateway = OllamaGateway()
    assert isinstance(gateway.chat_model, ChatOllama)
    return gateway.chat_model


def _mock_ollama_chat_model(reply_text: str = "done") -> MagicMock:
    chat_model = MagicMock()
    chat_model.model = "llama3.1:8b"
    chat_model.invoke.return_value = AIMessage(content=reply_text)
    return chat_model


def test_it_should_default_the_ollama_model_to_llama3_1_8b(monkeypatch):
    monkeypatch.delenv("PET_HELPER_OLLAMA_MODEL", raising=False)

    chat_model = _build_default_chat_model()

    assert chat_model.model == "llama3.1:8b"


def test_it_should_read_the_ollama_model_from_pet_helper_ollama_model(monkeypatch):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    monkeypatch.setenv("PET_HELPER_OLLAMA_MODEL", "llama3.2:3b")

    gateway = build_gateway()

    assert isinstance(gateway, OllamaGateway)
    assert gateway.chat_model.model == "llama3.2:3b"


def test_it_should_ignore_pet_helper_assistant_model_for_the_ollama_provider(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    monkeypatch.setenv("PET_HELPER_ASSISTANT_MODEL", "claude-sonnet-5-5")

    gateway = build_gateway()

    assert isinstance(gateway, OllamaGateway)
    assert gateway.chat_model.model == "llama3.1:8b"


def test_it_should_set_an_explicit_8192_context_window_on_chat_ollama(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    chat_model = _build_default_chat_model()

    assert chat_model.num_ctx == 8192


def test_it_should_set_temperature_to_zero_on_chat_ollama(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    chat_model = _build_default_chat_model()

    assert chat_model.temperature == 0


def test_it_should_configure_base_url_from_ollama_base_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")

    chat_model = _build_default_chat_model()

    assert chat_model.base_url == "http://ollama:11434"


def test_it_should_return_an_ollama_model_result_for_a_normal_reply():
    reply_message = AIMessage(content="Order found.", id="msg_ollama")
    fake_model = FakeMessagesListChatModel(responses=[reply_message])
    gateway = OllamaGateway(chat_model=fake_model)

    result = gateway.complete([HumanMessage("hello")])

    assert result == ModelResult(
        message=reply_message,
        provider="ollama",
        model="llama3.1:8b",
        fell_back=False,
        refused=False,
    )


def test_it_should_bind_tools_on_the_ollama_chat_model_when_tools_are_provided():
    reply_message = AIMessage(content="Order found.")
    tools = [{"name": "get_order", "description": "Look up an order", "parameters": {}}]
    chat_model = _mock_ollama_chat_model()
    chat_model.bind_tools.return_value = FakeMessagesListChatModel(
        responses=[reply_message]
    )
    gateway = OllamaGateway(chat_model=chat_model)

    result = gateway.complete([HumanMessage("PED-10001")], tools=tools)

    chat_model.bind_tools.assert_called_once_with(tools)
    assert result.message == reply_message
    assert result.provider == "ollama"


def test_it_should_replace_structured_ai_blocks_with_plain_text_preserving_tool_calls():
    tool_call = {
        "name": "get_order",
        "args": {"order_number": "PED-10001"},
        "id": "call_1",
    }
    ai_with_blocks = AIMessage(
        content=[
            {
                "type": "thinking",
                "thinking": "Need to look up PED-10001",
                "signature": "sig_1",
            },
            {"type": "text", "text": "Checking your order."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "get_order",
                "input": {"order_number": "PED-10001"},
            },
        ],
        tool_calls=[tool_call],
    )
    chat_model = _mock_ollama_chat_model()
    gateway = OllamaGateway(chat_model=chat_model)

    gateway.complete([HumanMessage("where is PED-10001?"), ai_with_blocks])

    chat_model.invoke.assert_called_once_with(
        [
            HumanMessage("where is PED-10001?"),
            AIMessage(content="Checking your order.", tool_calls=[tool_call]),
        ]
    )


def test_it_should_turn_thinking_only_ai_blocks_into_an_empty_string_preserving_tool_calls():
    tool_call = {
        "name": "search_policies",
        "args": {"query": "refund"},
        "id": "call_2",
    }
    thinking_only_ai = AIMessage(
        content=[
            {
                "type": "thinking",
                "thinking": "Search refund policy first",
                "signature": "sig_2",
            },
            {
                "type": "tool_use",
                "id": "call_2",
                "name": "search_policies",
                "input": {"query": "refund"},
            },
        ],
        tool_calls=[tool_call],
    )
    chat_model = _mock_ollama_chat_model()
    gateway = OllamaGateway(chat_model=chat_model)

    gateway.complete([HumanMessage("refund rules?"), thinking_only_ai])

    chat_model.invoke.assert_called_once_with(
        [
            HumanMessage("refund rules?"),
            AIMessage(content="", tool_calls=[tool_call]),
        ]
    )


def test_it_should_flatten_non_assistant_content_blocks_to_plain_text_for_ollama():
    system_with_cache = SystemMessage(
        content=[
            {
                "type": "text",
                "text": "System instructions",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )
    human_with_cache = HumanMessage(
        content=[
            {
                "type": "text",
                "text": "Where is my order?",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )
    chat_model = _mock_ollama_chat_model()
    gateway = OllamaGateway(chat_model=chat_model)

    gateway.complete([system_with_cache, human_with_cache])

    chat_model.invoke.assert_called_once_with(
        [
            SystemMessage("System instructions"),
            HumanMessage("Where is my order?"),
        ]
    )


def test_it_should_keep_tool_message_results_in_the_history_sent_to_ollama():
    tool_call = {
        "name": "get_order",
        "args": {"order_number": "PED-10001"},
        "id": "call_1",
    }
    tool_result = ToolMessage(
        content='{"status": "Delivered"}',
        tool_call_id="call_1",
        name="get_order",
    )
    chat_model = _mock_ollama_chat_model()
    gateway = OllamaGateway(chat_model=chat_model)

    gateway.complete(
        [
            HumanMessage("where is PED-10001?"),
            AIMessage(
                content=[{"type": "thinking", "thinking": "lookup", "signature": "s1"}],
                tool_calls=[tool_call],
            ),
            tool_result,
        ]
    )

    chat_model.invoke.assert_called_once_with(
        [
            HumanMessage("where is PED-10001?"),
            AIMessage(content="", tool_calls=[tool_call]),
            ToolMessage(
                content='{"status": "Delivered"}',
                tool_call_id="call_1",
                name="get_order",
            ),
        ]
    )


def test_it_should_not_mutate_original_message_objects_when_normalizing_for_ollama():
    original_blocks = [
        {"type": "thinking", "thinking": "keep intact", "signature": "sig_orig"},
        {"type": "text", "text": "Checking now."},
    ]
    original_ai = AIMessage(
        content=[dict(block) for block in original_blocks],
        tool_calls=[
            {"name": "get_order", "args": {"order_number": "PED-10001"}, "id": "call_1"}
        ],
    )
    history = [HumanMessage("status?"), original_ai]
    gateway = OllamaGateway(chat_model=_mock_ollama_chat_model())

    gateway.complete(history)

    assert original_ai.content == original_blocks
    assert history == [HumanMessage("status?"), original_ai]


def test_it_should_raise_model_unavailable_when_the_ollama_chat_model_fails():
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    chat_model = _mock_ollama_chat_model()
    chat_model.invoke.side_effect = httpx.ConnectError(
        "connection refused", request=request
    )
    gateway = OllamaGateway(chat_model=chat_model)

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert isinstance(exc_info.value, ModelUnavailable)


def _failing_gateway(error: Exception) -> OllamaGateway:
    chat_model = _mock_ollama_chat_model()
    chat_model.invoke.side_effect = error
    return OllamaGateway(chat_model=chat_model)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout(
            "timed out", request=httpx.Request("POST", "http://localhost:11434")
        ),
        ConnectionError("connection refused"),
        ollama.ResponseError("server error", 500),
        ollama.ResponseError("unknown failure"),
    ],
    ids=["read-timeout", "builtin-connection-error", "http-500", "status-minus-one"],
)
def test_it_should_raise_model_unavailable_on_transport_and_server_errors(error):
    gateway = _failing_gateway(error)

    with pytest.raises(ModelUnavailable):
        gateway.complete([HumanMessage("hello")])


@pytest.mark.parametrize("status_code", [400, 404])
def test_it_should_raise_model_config_error_on_a_client_error_response(status_code):
    gateway = _failing_gateway(ollama.ResponseError("model not found", status_code))

    with pytest.raises(ModelConfigError, match="model not found"):
        gateway.complete([HumanMessage("hello")])


def test_it_should_let_an_unexpected_exception_propagate_unchanged():
    error = ValueError("bad message conversion")
    gateway = _failing_gateway(error)

    with pytest.raises(ValueError) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert exc_info.value is error


def test_it_should_pass_a_request_timeout_to_the_ollama_client():
    chat_model = _build_default_chat_model()

    assert chat_model.client_kwargs.get("timeout") is not None
