from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from pet_helper.gateway import (
    AnthropicGateway,
    FakeGateway,
    ModelConfigError,
    ModelResult,
    ModelUnavailable,
    resolve_fallback,
    resolve_provider,
)

GATEWAY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_EFFORT",
    "PET_HELPER_FALLBACK",
)


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch):
    for name in GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _build_chat_model(monkeypatch, **env: str) -> Any:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    gateway = AnthropicGateway()
    assert isinstance(gateway.chat_model, ChatAnthropic)
    return gateway.chat_model


def _anthropic_error(kind: int | str) -> Exception:
    import anthropic
    import httpx

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
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        503: anthropic.InternalServerError,
    }
    return error_classes[status_code](
        f"HTTP {status_code}",
        response=response,
        body=None,
    )


def _raising_gateway(error: Exception) -> AnthropicGateway:
    def _fail(_messages):
        raise error

    return AnthropicGateway(chat_model=RunnableLambda(_fail))


def test_it_should_define_distinct_typed_exceptions_for_unavailable_versus_config_error():
    unavailable = ModelUnavailable("service down")
    config_error = ModelConfigError("invalid key")

    are_exceptions = (
        isinstance(unavailable, Exception),
        isinstance(config_error, Exception),
    )

    assert are_exceptions == (True, True)
    assert not isinstance(unavailable, ModelConfigError)
    assert not isinstance(config_error, ModelUnavailable)


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: ModelUnavailable("both providers down"),
        lambda: ModelConfigError("bad api key"),
    ],
)
def test_it_should_drive_typed_gateway_errors_through_fake_gateway_scripts(
    error_factory,
):
    scripted_error = error_factory()
    gateway = FakeGateway([scripted_error])

    with pytest.raises(type(scripted_error)) as exc_info:
        gateway.complete([HumanMessage("olá")])

    assert exc_info.value is scripted_error


def test_it_should_default_the_anthropic_model_to_claude_haiku_5_5_in_the_payload(
    monkeypatch,
):
    chat_model = _build_chat_model(monkeypatch)

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert payload["model"] == "claude-haiku-5-5"


def test_it_should_override_the_anthropic_model_from_pet_helper_assistant_model(
    monkeypatch,
):
    chat_model = _build_chat_model(
        monkeypatch,
        PET_HELPER_ASSISTANT_MODEL="claude-sonnet-5-5",
    )

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert payload["model"] == "claude-sonnet-5-5"


def test_it_should_configure_max_retries_to_one_on_the_anthropic_chat_model(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    chat_model = _build_chat_model(monkeypatch)

    assert chat_model.max_retries == 1


def test_it_should_default_output_config_effort_to_low_in_the_payload(monkeypatch):
    chat_model = _build_chat_model(monkeypatch)

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert payload["output_config"] == {"effort": "low"}


def test_it_should_override_output_config_effort_from_pet_helper_effort(monkeypatch):
    chat_model = _build_chat_model(monkeypatch, PET_HELPER_EFFORT="medium")

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert payload["output_config"] == {"effort": "medium"}


def test_it_should_set_a_roomy_max_tokens_in_the_payload(monkeypatch):
    chat_model = _build_chat_model(monkeypatch)

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert payload["max_tokens"] >= 2048


@pytest.mark.parametrize("sampling_key", ["temperature", "top_p", "top_k"])
def test_it_should_omit_sampling_parameters_from_the_anthropic_payload(
    monkeypatch, sampling_key
):
    chat_model = _build_chat_model(monkeypatch)

    payload = chat_model._get_request_payload([HumanMessage("olá")])

    assert sampling_key not in payload


def test_it_should_return_an_anthropic_model_result_for_a_normal_reply():
    reply_message = AIMessage(
        content="Olá, como posso ajudar?",
        id="msg_normal",
        response_metadata={"stop_reason": "end_turn"},
    )
    fake_model = FakeMessagesListChatModel(responses=[reply_message])
    gateway = AnthropicGateway(chat_model=fake_model)

    result = gateway.complete([HumanMessage("olá")])

    assert result == ModelResult(
        message=reply_message,
        provider="anthropic",
        model="claude-haiku-5-5",
        fell_back=False,
        refused=False,
    )


def test_it_should_bind_tools_on_the_chat_model_when_tools_are_provided():
    reply_message = AIMessage(
        content="Pedido encontrado.",
        id="msg_tool",
        response_metadata={"stop_reason": "end_turn"},
    )
    tools = [{"name": "get_order", "description": "Look up an order", "parameters": {}}]
    chat_model = MagicMock()
    chat_model.model = "claude-haiku-5-5"
    chat_model.bind_tools.return_value = FakeMessagesListChatModel(
        responses=[reply_message]
    )
    gateway = AnthropicGateway(chat_model=chat_model)

    result = gateway.complete([HumanMessage("PED-10001")], tools=tools)

    chat_model.bind_tools.assert_called_once_with(tools)
    assert result.message == reply_message
    assert result.refused is False


def test_it_should_mark_refused_true_when_stop_reason_is_refusal():
    refusal_message = AIMessage(
        content="",
        id="msg_refusal",
        response_metadata={"stop_reason": "refusal"},
    )
    fake_model = FakeMessagesListChatModel(responses=[refusal_message])
    gateway = AnthropicGateway(chat_model=fake_model)

    result = gateway.complete([HumanMessage("pedido proibido")])

    assert result.provider == "anthropic"
    assert result.refused is True
    assert result.fell_back is False


def test_it_should_raise_model_config_error_on_http_400_bad_request():
    gateway = _raising_gateway(_anthropic_error(400))

    with pytest.raises(ModelConfigError) as exc_info:
        gateway.complete([HumanMessage("olá")])

    assert isinstance(exc_info.value, ModelConfigError)


def test_it_should_raise_model_config_error_on_http_401_authentication_error():
    gateway = _raising_gateway(_anthropic_error(401))

    with pytest.raises(ModelConfigError) as exc_info:
        gateway.complete([HumanMessage("olá")])

    assert isinstance(exc_info.value, ModelConfigError)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_it_should_raise_model_unavailable_on_transport_http_status_errors(
    monkeypatch, status_code
):
    monkeypatch.setenv("PET_HELPER_FALLBACK", "none")
    gateway = _raising_gateway(_anthropic_error(status_code))

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("olá")])

    assert isinstance(exc_info.value, ModelUnavailable)


@pytest.mark.parametrize("kind", ["connection", "timeout"])
def test_it_should_raise_model_unavailable_on_connection_or_timeout_errors(
    monkeypatch, kind
):
    monkeypatch.setenv("PET_HELPER_FALLBACK", "none")
    gateway = _raising_gateway(_anthropic_error(kind))

    with pytest.raises(ModelUnavailable) as exc_info:
        gateway.complete([HumanMessage("olá")])

    assert isinstance(exc_info.value, ModelUnavailable)


@pytest.mark.parametrize(
    ("configured_provider", "api_key", "expected"),
    [
        ("ollama", "sk-ant-present", "ollama"),
        ("anthropic", None, "anthropic"),
    ],
)
def test_it_should_let_pet_helper_provider_override_api_key_presence(
    monkeypatch, configured_provider, api_key, expected
):
    monkeypatch.setenv("PET_HELPER_PROVIDER", configured_provider)
    if api_key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)

    provider = resolve_provider()

    assert provider == expected


def test_it_should_select_anthropic_when_provider_is_unset_with_api_key_present(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    provider = resolve_provider()

    assert provider == "anthropic"


@pytest.mark.parametrize("api_key", [None, ""])
def test_it_should_select_ollama_when_provider_is_unset_without_api_key(
    monkeypatch, api_key
):
    if api_key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)

    provider = resolve_provider()

    assert provider == "ollama"


def test_it_should_raise_a_clear_error_for_an_unknown_provider(monkeypatch):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "openai")

    with pytest.raises((ModelConfigError, ValueError), match="openai") as exc_info:
        resolve_provider()

    assert "openai" in str(exc_info.value)


def test_it_should_default_pet_helper_fallback_to_ollama_when_unset(monkeypatch):
    monkeypatch.delenv("PET_HELPER_FALLBACK", raising=False)

    fallback = resolve_fallback()

    assert fallback == "ollama"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("ollama", "ollama"),
        ("none", "none"),
    ],
)
def test_it_should_accept_supported_pet_helper_fallback_values(
    monkeypatch, raw_value, expected
):
    monkeypatch.setenv("PET_HELPER_FALLBACK", raw_value)

    fallback = resolve_fallback()

    assert fallback == expected


def test_it_should_raise_a_clear_error_for_an_unsupported_fallback_value(monkeypatch):
    monkeypatch.setenv("PET_HELPER_FALLBACK", "openrouter")

    with pytest.raises((ModelConfigError, ValueError), match="openrouter") as exc_info:
        resolve_fallback()

    assert "openrouter" in str(exc_info.value)
