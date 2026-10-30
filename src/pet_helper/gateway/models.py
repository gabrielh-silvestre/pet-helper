from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage


class ModelUnavailable(Exception):
    pass


class ModelConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelResult:
    message: AIMessage
    provider: str = ""
    model: str = ""
    fell_back: bool = False
    refused: bool = False


class ModelGateway(Protocol):
    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()
    ) -> ModelResult: ...


def extract_model_name(chat_model: Any, fallback_model: str) -> str:
    candidate = getattr(chat_model, "model", None)
    return candidate if isinstance(candidate, str) and candidate else fallback_model
