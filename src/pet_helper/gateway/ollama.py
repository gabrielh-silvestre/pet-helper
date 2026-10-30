from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_ollama import ChatOllama

from pet_helper.gateway.config import resolve_ollama_base_url, resolve_ollama_model
from pet_helper.gateway.models import (
    ModelResult,
    ModelUnavailable,
    extract_model_name,
)

DEFAULT_NUM_CTX = 8192
DEFAULT_TEMPERATURE = 0


def _normalize_message(message: BaseMessage) -> BaseMessage:
    if isinstance(message.content, str):
        return message
    if isinstance(message, AIMessage):
        return AIMessage(
            content=str(message.text),
            tool_calls=list(message.tool_calls),
        )
    return message.model_copy(update={"content": str(message.text)})


class OllamaGateway:
    def __init__(self, chat_model: Any = None):
        configured_model = resolve_ollama_model()
        self.chat_model: Any = (
            chat_model
            if chat_model is not None
            else ChatOllama(
                model=configured_model,
                num_ctx=DEFAULT_NUM_CTX,
                temperature=DEFAULT_TEMPERATURE,
                base_url=resolve_ollama_base_url(),
            )
        )
        self._model_name = extract_model_name(self.chat_model, configured_model)

    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()
    ) -> ModelResult:
        runnable = self.chat_model.bind_tools(tools) if tools else self.chat_model
        normalized_messages = [_normalize_message(msg) for msg in messages]
        try:
            message: AIMessage = runnable.invoke(normalized_messages)
        except Exception as exc:
            raise ModelUnavailable(str(exc)) from exc
        return ModelResult(
            message=message,
            provider="ollama",
            model=self._model_name,
            fell_back=False,
            refused=False,
        )
