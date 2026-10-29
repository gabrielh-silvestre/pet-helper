from collections.abc import Sequence
from dataclasses import dataclass
from itertools import cycle
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage

DEFAULT_REPLY = "Hello! This is the pet-helper skeleton; I do not answer questions yet."


@dataclass(frozen=True)
class ModelResult:
    message: AIMessage


class ModelGateway(Protocol):
    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence = ()
    ) -> ModelResult: ...


class FakeGateway:
    def __init__(self, replies: Sequence[str] = (DEFAULT_REPLY,)):
        self._replies = cycle(replies)
        self.calls: list[list[BaseMessage]] = []

    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence = ()
    ) -> ModelResult:
        self.calls.append(list(messages))
        return ModelResult(AIMessage(next(self._replies)))


def default_gateway() -> ModelGateway:
    return FakeGateway()
