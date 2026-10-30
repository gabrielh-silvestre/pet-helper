from unittest.mock import MagicMock, call

import httpx
import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from pet_helper.gateway import (
    AnthropicGateway,
    ModelConfigError,
    ModelResult,
    ModelUnavailable,
    OllamaGateway,
    build_gateway,
)

GATEWAY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_EFFORT",
    "PET_HELPER_FALLBACK",
)


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch):
    for name in GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _anthropic_error(kind: int | str) -> Exception:
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    if kind == "connection":
        return anthropic.APIConnectionError(request=request)
    if kind == "timeout":
        return anthropic.APITimeoutError(request=request)

    status_code = int(kind)
    response = httpx.Response(status_code, request=request)
    error_classes = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        422: anthropic.UnprocessableEntityError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        503: anthropic.ServiceUnavailableError,
        504: anthropic.DeadlineExceededError,
        529: anthropic.OverloadedError,
    }
    return error_classes[status_code](
        f"HTTP {status_code}",
        response=response,
        body=None,
    )


def _raising_runnable(error: Exception) -> RunnableLambda:
    def _fail(_messages):
        raise error

    return RunnableLambda(_fail)


def _mock_ollama_model(reply_text: str = "fallback answer") -> MagicMock:
    chat_model = MagicMock()
    chat_model.model = "llama3.1:8b"
    chat_model.invoke.return_value = AIMessage(content=reply_text)
    return chat_model


@pytest.mark.parametrize(
    "transport_error",
    ["connection", "timeout", 429, 500, 503, 504, 529],
)
def test_it_should_fall_back_to_ollama_once_on_each_anthropic_transport_error(
    transport_error,
):
    ollama_reply = AIMessage(content="answered by ollama")
    ollama_model = _mock_ollama_model()
    ollama_model.invoke.return_value = ollama_reply
    anthropic_model = MagicMock()
    anthropic_model.model = "claude-haiku-5-5"
    anthropic_model.invoke.side_effect = _anthropic_error(transport_error)
    gateway = AnthropicGateway(
        chat_model=anthropic_model,
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    result = gateway.complete([HumanMessage("hello")])

    anthropic_model.invoke.assert_called_once_with([HumanMessage("hello")])
    ollama_model.invoke.assert_called_once_with([HumanMessage("hello")])
    assert result == ModelResult(
        message=ollama_reply,
        provider="ollama",
        model="llama3.1:8b",
        fell_back=True,
        refused=False,
    )


def test_it_should_normalize_history_only_on_the_ollama_fallback_branch():
    tool_call = {
        "name": "get_order",
        "args": {"order_number": "PED-10001"},
        "id": "call_1",
    }
    prior_ai = AIMessage(
        content=[
            {"type": "thinking", "thinking": "cached thought", "signature": "sig_1"},
            {"type": "text", "text": "Checking order."},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "get_order",
                "input": {"order_number": "PED-10001"},
            },
        ],
        tool_calls=[tool_call],
    )
    tool_msg = ToolMessage(
        content='{"status": "Delivered"}',
        tool_call_id="call_1",
        name="get_order",
    )
    history = [HumanMessage("status of PED-10001?"), prior_ai, tool_msg]
    anthropic_model = MagicMock()
    anthropic_model.model = "claude-haiku-5-5"
    anthropic_model.invoke.side_effect = _anthropic_error(529)
    ollama_model = _mock_ollama_model()
    gateway = AnthropicGateway(
        chat_model=anthropic_model,
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    gateway.complete(history)

    anthropic_model.invoke.assert_called_once_with(history)
    ollama_model.invoke.assert_called_once_with(
        [
            HumanMessage("status of PED-10001?"),
            AIMessage(content="Checking order.", tool_calls=[tool_call]),
            tool_msg,
        ]
    )


def test_it_should_forward_tools_to_the_ollama_fallback_when_anthropic_fails():
    tools = [{"name": "get_order", "description": "Look up an order", "parameters": {}}]
    ollama_reply = AIMessage(content="fallback with tools")
    anthropic_model = MagicMock()
    anthropic_model.model = "claude-haiku-5-5"
    anthropic_model.bind_tools.return_value = _raising_runnable(_anthropic_error(503))
    ollama_model = _mock_ollama_model()
    ollama_model.bind_tools.return_value = FakeMessagesListChatModel(
        responses=[ollama_reply]
    )
    gateway = AnthropicGateway(
        chat_model=anthropic_model,
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    result = gateway.complete([HumanMessage("PED-10001")], tools=tools)

    ollama_model.bind_tools.assert_called_once_with(tools)
    assert result.fell_back is True
    assert result.provider == "ollama"
    assert result.message == ollama_reply


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_it_should_raise_model_config_error_without_fallback_on_non_transport_4xx(
    status_code,
):
    ollama_model = _mock_ollama_model()
    gateway = AnthropicGateway(
        chat_model=_raising_runnable(_anthropic_error(status_code)),
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    with pytest.raises(ModelConfigError) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert isinstance(exc_info.value, ModelConfigError)
    ollama_model.invoke.assert_not_called()


def test_it_should_return_refusal_without_calling_the_ollama_fallback():
    refusal_message = AIMessage(
        content="",
        id="msg_refusal",
        response_metadata={"stop_reason": "refusal"},
    )
    ollama_model = _mock_ollama_model()
    gateway = AnthropicGateway(
        chat_model=FakeMessagesListChatModel(responses=[refusal_message]),
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    result = gateway.complete([HumanMessage("disallowed request")])

    ollama_model.invoke.assert_not_called()
    assert result == ModelResult(
        message=refusal_message,
        provider="anthropic",
        model="claude-haiku-5-5",
        fell_back=False,
        refused=True,
    )


def test_it_should_disable_fallback_when_pet_helper_fallback_is_none(monkeypatch):
    monkeypatch.setenv("PET_HELPER_FALLBACK", "none")
    ollama_model = _mock_ollama_model()
    gateway = AnthropicGateway(
        chat_model=_raising_runnable(_anthropic_error(529)),
        fallback=OllamaGateway(chat_model=ollama_model),
    )

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert isinstance(exc_info.value, ModelUnavailable)
    ollama_model.invoke.assert_not_called()


def test_it_should_raise_model_unavailable_when_both_providers_fail():
    ollama_request = httpx.Request("POST", "http://localhost:11434/api/chat")
    failing_ollama = _raising_runnable(
        httpx.ConnectError("ollama down", request=ollama_request)
    )
    gateway = AnthropicGateway(
        chat_model=_raising_runnable(_anthropic_error("connection")),
        fallback=OllamaGateway(chat_model=failing_ollama),
    )

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert isinstance(exc_info.value, ModelUnavailable)


def test_it_should_build_an_anthropic_gateway_when_anthropic_is_selected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    gateway = build_gateway()

    assert isinstance(gateway, AnthropicGateway)


@pytest.mark.parametrize(
    ("provider_env", "api_key"),
    [
        ("ollama", "sk-ant-test-key"),
        (None, None),
    ],
)
def test_it_should_build_an_ollama_gateway_when_ollama_is_selected(
    monkeypatch, provider_env, api_key
):
    if provider_env is not None:
        monkeypatch.setenv("PET_HELPER_PROVIDER", provider_env)
    if api_key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)

    gateway = build_gateway()

    assert isinstance(gateway, OllamaGateway)


def test_it_should_propagate_model_config_error_when_building_an_unsupported_provider(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "openai")

    with pytest.raises(ModelConfigError, match="openai") as exc_info:
        build_gateway()

    assert isinstance(exc_info.value, ModelConfigError)


def test_it_should_have_no_fallback_when_ollama_is_the_selected_provider(monkeypatch):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("PET_HELPER_FALLBACK", "ollama")
    ollama_request = httpx.Request("POST", "http://localhost:11434/api/chat")
    gateway = OllamaGateway(
        chat_model=_raising_runnable(
            httpx.ConnectError("ollama down", request=ollama_request)
        )
    )

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("hello")])

    assert isinstance(exc_info.value, ModelUnavailable)


def test_it_should_send_text_only_history_on_every_call_for_the_ollama_provider(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    ollama_model = _mock_ollama_model()
    ollama_model.invoke.side_effect = [
        AIMessage(content="first reply"),
        AIMessage(content="second reply"),
    ]
    gateway = OllamaGateway(chat_model=ollama_model)
    first_turn = [HumanMessage(content=[{"type": "text", "text": "turn 1"}])]
    second_turn = [
        HumanMessage("turn 1"),
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "t1", "signature": "s1"},
                {"type": "text", "text": "first reply"},
            ]
        ),
        HumanMessage("turn 2"),
    ]

    gateway.complete(first_turn)
    gateway.complete(second_turn)

    ollama_model.invoke.assert_has_calls(
        [
            call([HumanMessage("turn 1")]),
            call(
                [
                    HumanMessage("turn 1"),
                    AIMessage("first reply"),
                    HumanMessage("turn 2"),
                ]
            ),
        ]
    )
    assert ollama_model.invoke.call_count == 2
