from collections import deque
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from pet_helper.gateway.models import ModelResult

type ScriptItem = str | AIMessage | ModelResult | BaseException


class FakeScriptExhaustedError(RuntimeError):
    pass


def _to_fake_result(item: ScriptItem) -> ModelResult:
    if isinstance(item, BaseException):
        raise item
    if isinstance(item, ModelResult):
        return item
    if isinstance(item, AIMessage):
        # a copy, so add_messages stamps an id on it and not on the scripted original, which a script may reuse
        return ModelResult(message=item.model_copy(), provider="fake", model="fake")
    return ModelResult(message=AIMessage(item), provider="fake", model="fake")


class FakeGateway:
    def __init__(self, script: Sequence[ScriptItem] = ()):
        self._script: deque[ScriptItem] = deque(script)
        self.calls: list[list[BaseMessage]] = []

    def complete(
        self, messages: Sequence[BaseMessage], tools: Sequence[Any] = ()
    ) -> ModelResult:
        self.calls.append(list(messages))
        if not self._script:
            raise FakeScriptExhaustedError("FakeGateway script is exhausted")
        return _to_fake_result(self._script.popleft())
