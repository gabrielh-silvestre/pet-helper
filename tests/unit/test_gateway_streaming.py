import json
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from pet_helper.gateway import AnthropicGateway, ModelResult

GATEWAY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_EFFORT",
    "PET_HELPER_FALLBACK",
)
EPHEMERAL = {"type": "ephemeral"}
USAGE = {
    "input_tokens": 25,
    "cache_creation_input_tokens": 10,
    "cache_read_input_tokens": 5,
    "output_tokens": 12,
}


@pytest.fixture(autouse=True)
def _clean_gateway_env(monkeypatch):
    for name in GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class _TokenRecorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.chunk_contents: list[Any] = []

    def on_llm_new_token(self, token, *, chunk=None, **kwargs):
        self.chunk_contents.append(chunk.message.content)

    @property
    def texts(self) -> list[str]:
        pieces = (_text_of(content) for content in self.chunk_contents)
        return [piece for piece in pieces if piece]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content)


def _thinking_blocks(content: list[Any]) -> list[tuple[str, str]]:
    return [
        (block["thinking"], block["signature"])
        for block in content
        if block.get("type") == "thinking"
    ]


def _sse(events: list[dict]) -> bytes:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _anthropic_events(
    texts: tuple[str, ...] = ("Olá, ", "mundo"),
    *,
    thinking: tuple[tuple[str, str], ...] = (),
    stop_reason: str = "end_turn",
) -> list[dict]:
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {**USAGE, "output_tokens": 1},
    }
    events: list[dict] = [{"type": "message_start", "message": message}]
    index = 0
    for thought, signature in thinking:
        events += [
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "thinking_delta", "thinking": thought},
            },
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "signature_delta", "signature": signature},
            },
            {"type": "content_block_stop", "index": index},
        ]
        index += 1
    events.append(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
    )
    events += [
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
        for text in texts
    ]
    events += [
        {"type": "content_block_stop", "index": index},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": USAGE,
        },
        {"type": "message_stop"},
    ]
    return events


def _serve_anthropic(chat_model: ChatAnthropic, events: list[dict]) -> list[dict]:
    requests: list[dict] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(events),
        )

    # _client is a cached_property: planting it keeps the real streaming code path
    chat_model.__dict__["_client"] = anthropic.Anthropic(
        api_key="sk-ant-test-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    )
    return requests


def _streaming_chat_model(**overrides: Any) -> ChatAnthropic:
    return ChatAnthropic(
        model="claude-haiku-5-5",
        api_key="sk-ant-test-key",
        streaming=True,
        max_tokens=8192,
        max_retries=0,
        **overrides,
    )


def _gateway_serving(monkeypatch, events: list[dict]) -> AnthropicGateway:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    gateway = AnthropicGateway()
    _serve_anthropic(gateway.chat_model, events)
    return gateway


def _ollama_chat_model(chunks: tuple[str, ...] = ("Olá, ", "mundo")) -> ChatOllama:
    lines = [
        {"model": "llama3.1:8b", "message": {"role": "assistant", "content": chunk}}
        | {"done": False}
        for chunk in chunks
    ]
    lines.append(
        {
            "model": "llama3.1:8b",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        body = "\n".join(json.dumps(line) for line in lines) + "\n"
        return httpx.Response(
            200, headers={"content-type": "application/x-ndjson"}, content=body
        )

    return ChatOllama(
        model="llama3.1:8b",
        client_kwargs={"transport": httpx.MockTransport(respond)},
    )


# characterization of the installed libraries: the adapters rely on these facts


def test_it_should_fire_on_llm_new_token_for_each_anthropic_text_chunk():
    chat_model = _streaming_chat_model()
    _serve_anthropic(chat_model, _anthropic_events(("Olá, ", "mundo")))
    recorder = _TokenRecorder()

    chat_model.invoke([HumanMessage("oi")], config={"callbacks": [recorder]})

    assert recorder.texts == ["Olá, ", "mundo"]


def test_it_should_keep_the_stop_reason_in_the_aggregated_anthropic_message():
    chat_model = _streaming_chat_model()
    _serve_anthropic(chat_model, _anthropic_events(stop_reason="max_tokens"))

    reply = chat_model.invoke([HumanMessage("oi")])

    assert reply.response_metadata["stop_reason"] == "max_tokens"


def test_it_should_keep_the_usage_with_cache_fields_in_the_aggregated_anthropic_message():
    chat_model = _streaming_chat_model()
    _serve_anthropic(chat_model, _anthropic_events())

    reply = chat_model.invoke([HumanMessage("oi")])

    assert reply.usage_metadata == {
        "input_tokens": 40,
        "output_tokens": 12,
        "total_tokens": 52,
        "input_token_details": {"cache_creation": 10, "cache_read": 5},
    }


def test_it_should_keep_every_thinking_block_with_its_signature_in_the_aggregated_message():
    chat_model = _streaming_chat_model(
        thinking={"type": "enabled", "budget_tokens": 2000}
    )
    _serve_anthropic(
        chat_model,
        _anthropic_events(thinking=(("primeiro", "sig-a"), ("segundo", "sig-b"))),
    )

    reply = chat_model.invoke([HumanMessage("oi")])

    assert _thinking_blocks(reply.content) == [
        ("primeiro", "sig-a"),
        ("segundo", "sig-b"),
    ]


def test_it_should_resend_the_thinking_blocks_unchanged_in_the_next_request():
    chat_model = _streaming_chat_model(
        thinking={"type": "enabled", "budget_tokens": 2000}
    )
    requests = _serve_anthropic(
        chat_model,
        _anthropic_events(thinking=(("primeiro", "sig-a"), ("segundo", "sig-b"))),
    )
    first_reply = chat_model.invoke([HumanMessage("oi")])

    chat_model.invoke([HumanMessage("oi"), first_reply, HumanMessage("e aí")])

    resent = requests[1]["messages"][1]["content"]
    assert _thinking_blocks(resent) == [("primeiro", "sig-a"), ("segundo", "sig-b")]


def test_it_should_keep_both_cache_control_breakpoints_in_the_streaming_request():
    chat_model = _streaming_chat_model()
    requests = _serve_anthropic(chat_model, _anthropic_events())
    system_blocks = [
        {"type": "text", "text": "Regras fixas.", "cache_control": EPHEMERAL}
    ]
    last_blocks = [{"type": "text", "text": "oi", "cache_control": EPHEMERAL}]

    chat_model.invoke([SystemMessage(system_blocks), HumanMessage(last_blocks)])

    assert (requests[0]["system"], requests[0]["messages"]) == (
        system_blocks,
        [{"role": "user", "content": last_blocks}],
    )


def test_it_should_fire_on_llm_new_token_for_each_ollama_chunk():
    recorder = _TokenRecorder()

    _ollama_chat_model(("Olá, ", "mundo")).invoke(
        [HumanMessage("oi")], config={"callbacks": [recorder]}
    )

    assert recorder.texts == ["Olá, ", "mundo"]


def test_it_should_keep_the_usage_in_the_aggregated_ollama_message():
    reply = _ollama_chat_model().invoke([HumanMessage("oi")])

    assert reply.usage_metadata == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_it_should_keep_the_done_reason_in_the_aggregated_ollama_message():
    reply = _ollama_chat_model().invoke([HumanMessage("oi")])

    assert reply.response_metadata["done_reason"] == "stop"


# the story's behavior: the Anthropic adapter streams and its results stay the same


def test_it_should_build_the_default_anthropic_chat_model_with_streaming_on(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    gateway = AnthropicGateway()

    assert gateway.chat_model.streaming is True


def test_it_should_stream_the_default_anthropic_model_through_the_gateway(monkeypatch):
    gateway = _gateway_serving(monkeypatch, _anthropic_events(("Olá, ", "mundo")))
    recorder = _TokenRecorder()

    # the handler reaches the model through the ambient config, as in the graph
    RunnableLambda(lambda messages: gateway.complete(messages)).invoke(
        [HumanMessage("oi")], config={"callbacks": [recorder]}
    )

    assert recorder.texts == ["Olá, ", "mundo"]


def test_it_should_return_the_same_model_result_for_a_streamed_reply(monkeypatch):
    gateway = _gateway_serving(monkeypatch, _anthropic_events(("Olá, ", "mundo")))

    result = gateway.complete([HumanMessage("oi")])

    assert (result.message.text, result.provider, result.model) == (
        "Olá, mundo",
        "anthropic",
        "claude-haiku-5-5",
    )
    assert (result.fell_back, result.refused, result.truncated) == (False, False, False)


def test_it_should_mark_refused_for_a_streamed_refusal(monkeypatch):
    gateway = _gateway_serving(monkeypatch, _anthropic_events(stop_reason="refusal"))

    result = gateway.complete([HumanMessage("oi")])

    assert result.refused is True


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_it_should_mark_truncated_for_a_streamed_reply_cut_short(
    monkeypatch, stop_reason
):
    gateway = _gateway_serving(monkeypatch, _anthropic_events(stop_reason=stop_reason))

    result = gateway.complete([HumanMessage("oi")])

    assert result.truncated is True


def test_it_should_keep_the_usage_on_the_streamed_gateway_message(monkeypatch):
    gateway = _gateway_serving(monkeypatch, _anthropic_events())

    result: ModelResult = gateway.complete([HumanMessage("oi")])

    assert isinstance(result.message, AIMessage)
    assert result.message.usage_metadata["input_token_details"] == {
        "cache_creation": 10,
        "cache_read": 5,
    }
