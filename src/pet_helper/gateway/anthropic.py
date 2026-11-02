import os
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage

from pet_helper.gateway.config import (
    ANTHROPIC_API_KEY_ENV,
    resolve_anthropic_model,
    resolve_effort,
    resolve_fallback,
)
from pet_helper.gateway.models import (
    ModelConfigError,
    ModelGateway,
    ModelResult,
    ModelUnavailable,
    extract_model_name,
)
from pet_helper.gateway.ollama import OllamaGateway
from pet_helper.trace import fallback_scope

# thinking counts against max_tokens, so the cap stays roomy
DEFAULT_MAX_TOKENS = 8192
# the SDK default is 10 minutes; max_retries=1 repeats it, so the fallback fires after about 2x this
REQUEST_TIMEOUT_SECONDS = 60
_TRUNCATED_STOP_REASONS = {"max_tokens", "model_context_window_exceeded"}

# 503, 504, and 529 inherit directly from APIStatusError rather than InternalServerError
ANTHROPIC_TRANSPORT_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
)


def _build_default_chat_model(model_name: str) -> ChatAnthropic:
    if not os.environ.get(ANTHROPIC_API_KEY_ENV):
        raise ModelConfigError(
            f"{ANTHROPIC_API_KEY_ENV} is required when PET_HELPER_PROVIDER=anthropic"
        )
    # Sampling params (temperature, top_p, top_k) are intentionally omitted: Haiku 5.5 rejects them with HTTP 400
    return ChatAnthropic(
        model=model_name,
        max_retries=1,
        max_tokens=DEFAULT_MAX_TOKENS,
        # streaming is what lets the trace callback measure the time to first chunk
        streaming=True,
        timeout=REQUEST_TIMEOUT_SECONDS,
        output_config={"effort": resolve_effort()},
    )


class AnthropicGateway:
    def __init__(
        self,
        chat_model: Any = None,
        fallback: ModelGateway | None = None,
    ):
        configured_model = resolve_anthropic_model()
        self.chat_model: Any = (
            chat_model
            if chat_model is not None
            else _build_default_chat_model(configured_model)
        )
        self._model_name = extract_model_name(self.chat_model, configured_model)
        self._fallback: ModelGateway = (
            fallback if fallback is not None else OllamaGateway()
        )

    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()
    ) -> ModelResult:
        runnable = self.chat_model.bind_tools(tools) if tools else self.chat_model
        try:
            message: AIMessage = runnable.invoke(list(messages))
        except ANTHROPIC_TRANSPORT_ERRORS as exc:
            if resolve_fallback() == "none":
                raise ModelUnavailable(str(exc)) from exc
            with fallback_scope():
                fallback_result = self._fallback.complete(messages, tools)
            return replace(fallback_result, fell_back=True)
        except anthropic.APIStatusError as exc:
            raise ModelConfigError(str(exc)) from exc
        return ModelResult(
            message=message,
            provider="anthropic",
            model=self._model_name,
            fell_back=False,
            refused=message.response_metadata.get("stop_reason") == "refusal",
            truncated=message.response_metadata.get("stop_reason")
            in _TRUNCATED_STOP_REASONS,
        )
