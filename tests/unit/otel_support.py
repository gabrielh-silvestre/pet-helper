"""Shared fakes for the OTel span tests: a scripted streaming chat model and its gateway."""

import json
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import LangSmithParams
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from pet_helper.gateway import ModelResult

USAGE = {
    "input_tokens": 40,
    "output_tokens": 12,
    "total_tokens": 52,
    "input_token_details": {"cache_creation": 10, "cache_read": 5},
}
MODEL = "claude-haiku-5-5"
FIRST_TEXT_DELAY = 0.02


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


class ScriptedChat(BaseChatModel):
    """Chat model that answers from a script, streaming each reply chunk by chunk."""

    provider: str = "anthropic"
    model: str = MODEL
    streaming: bool = True
    script: list[AIMessage] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChat":
        return self

    def _get_ls_params(self, stop=None, **kwargs) -> LangSmithParams:
        return LangSmithParams(
            ls_provider=self.provider, ls_model_type="chat", ls_model_name=self.model
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        reply = self.script.pop(0)
        message = reply.model_copy(update={"usage_metadata": USAGE})
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self.script.pop(0)
        text = str(reply.content)
        if text:
            time.sleep(FIRST_TEXT_DELAY)
            middle = len(text) // 2
            for piece in (text[:middle], text[middle:]):
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
        for index, call in enumerate(reply.tool_calls):
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": _json(call["args"]),
                        "id": call["id"],
                        "index": index,
                    }
                ],
            )
            yield ChatGenerationChunk(message=chunk)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata=USAGE,
                response_metadata={"model_name": MODEL, "stop_reason": "end_turn"},
            )
        )


def _json(value: Any) -> str:
    return json.dumps(value)


class ScriptedGateway:
    """Gateway whose model streams, so the LangChain callbacks fire as with a real provider."""

    def __init__(self, script: Sequence[str | AIMessage], *, streaming: bool = True):
        replies = [
            AIMessage(item) if isinstance(item, str) else item for item in script
        ]
        self.chat = ScriptedChat(script=replies, streaming=streaming)

    def complete(
        self, messages: Sequence[Any], tools: Sequence[Any] = ()
    ) -> ModelResult:
        runnable = self.chat.bind_tools(tools) if tools else self.chat
        message = runnable.invoke(list(messages))
        return ModelResult(message=message, provider="anthropic", model=self.chat.model)
