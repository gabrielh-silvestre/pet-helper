import time
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

import anthropic
import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import LangSmithParams
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from pydantic import Field, ValidationError

from pet_helper.evals.budget import BudgetExceeded
from pet_helper.gateway import AnthropicGateway, OllamaGateway
from pet_helper.orders import ORDER_NUMBER_PATTERN
from pet_helper.trace import (
    TraceCallbackHandler,
    Tracer,
    UnknownPriceError,
    cost_usd,
    turn_scope,
)

USAGE = {
    "input_tokens": 40,
    "output_tokens": 12,
    "total_tokens": 52,
    "input_token_details": {"cache_creation": 10, "cache_read": 5},
}
FIRST_TEXT_DELAY = 0.05
LATE_DELAY = 0.3
RESERVE = Decimal("0.005")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv("PET_HELPER_TRACE_CONTENT", raising=False)
    monkeypatch.delenv("PET_HELPER_FALLBACK", raising=False)


class _RecordingTracer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        self.events.append({"event": kind, **fields})

    def of_kind(self, kind: str) -> list[dict]:
        return [event for event in self.events if event["event"] == kind]

    def only(self, kind: str) -> dict:
        (event,) = self.of_kind(kind)
        return event


class _RaisingTracer:
    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        raise RuntimeError("sink is down")


class _FakeChat(BaseChatModel):
    provider: str = "anthropic"
    model: str = "claude-haiku-5-5"
    streaming: bool = False
    chunks: list[AIMessageChunk] = Field(default_factory=list)
    delays: list[float] = Field(default_factory=list)
    reply: AIMessage | None = None
    failures: list[Any] = Field(default_factory=list)
    error_after_chunks: Any = None
    requests: list[int] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _get_ls_params(self, stop=None, **kwargs) -> LangSmithParams:
        return LangSmithParams(
            ls_provider=self.provider, ls_model_type="chat", ls_model_name=self.model
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.requests.append(1)
        if self.failures:
            raise self.failures.pop(0)
        reply = self.reply if self.reply is not None else AIMessage("ok")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(1)
        if self.failures:
            raise self.failures.pop(0)
        for index, chunk in enumerate(self.chunks):
            if index < len(self.delays):
                time.sleep(self.delays[index])
            yield ChatGenerationChunk(message=chunk)
        if self.error_after_chunks is not None:
            raise self.error_after_chunks


def _text(value: str, *, as_blocks: bool = False) -> AIMessageChunk:
    content = [{"type": "text", "text": value, "index": 0}] if as_blocks else value
    return AIMessageChunk(content=content)


def _final(
    *,
    stop_reason: str = "end_turn",
    usage: dict | None = None,
    model_name: str = "claude-haiku-5-5-20260101",
) -> AIMessageChunk:
    return AIMessageChunk(
        content="",
        usage_metadata=usage if usage is not None else USAGE,
        response_metadata={"model_name": model_name, "stop_reason": stop_reason},
    )


def _streaming_chat(*chunks: AIMessageChunk, **fields: Any) -> _FakeChat:
    return _FakeChat(streaming=True, chunks=list(chunks), **fields)


def _invoke(
    model: BaseChatModel,
    handler: TraceCallbackHandler | None = None,
    tracer: Any = None,
    **config: Any,
) -> tuple[AIMessage, Any]:
    selected_tracer = tracer if tracer is not None else _RecordingTracer()
    selected_handler = handler if handler is not None else TraceCallbackHandler()
    with turn_scope(selected_tracer, "session-1", 1):
        reply = model.invoke(
            [HumanMessage("oi")],
            config={"callbacks": [selected_handler], **config},
        )
    return reply, selected_tracer


def _model_call(model: BaseChatModel, **handler_kwargs: Any) -> dict:
    _, tracer = _invoke(model, TraceCallbackHandler(**handler_kwargs))
    return tracer.only("model.call")


def _streamed_call(**final: Any) -> dict:
    return _model_call(_streaming_chat(_text("Olá"), _final(**final)))


def _connection_error() -> Exception:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


def _gateway_call_events(
    gateway: AnthropicGateway | OllamaGateway,
) -> list[dict]:
    tracer = _RecordingTracer()
    handler = TraceCallbackHandler()
    with turn_scope(tracer, "session-1", 1):
        # the handler reaches the gateway's model through the ambient config, as in the graph
        RunnableLambda(lambda messages: gateway.complete(messages)).invoke(
            [HumanMessage("oi")], config={"callbacks": [handler]}
        )
    return tracer.of_kind("model.call")


@tool("get_order")
def _get_order(
    order_number: Annotated[str, Field(pattern=ORDER_NUMBER_PATTERN)],
) -> str:
    """Look up an order by its number."""
    return "found"


@tool("failing_tool")
def _failing_tool(query: str) -> str:
    """Always fails."""
    raise RuntimeError("backend down")


def _tool_call(tool_to_run, arguments: dict, handler=None, tracer=None) -> dict:
    selected_tracer = tracer if tracer is not None else _RecordingTracer()
    selected_handler = handler if handler is not None else TraceCallbackHandler()
    with turn_scope(selected_tracer, "session-1", 1):
        tool_to_run.invoke(arguments, config={"callbacks": [selected_handler]})
    return selected_tracer.only("tool.call")


# model.call attributes


def test_it_should_emit_one_model_call_event_for_a_streamed_call():
    _, tracer = _invoke(_streaming_chat(_text("Olá"), _final()))

    assert len(tracer.of_kind("model.call")) == 1


def test_it_should_record_the_provider_and_the_request_model():
    event = _streamed_call()

    assert (event["gen_ai.provider.name"], event["gen_ai.request.model"]) == (
        "anthropic",
        "claude-haiku-5-5",
    )


def test_it_should_record_the_response_model_from_the_reply_metadata():
    event = _streamed_call(model_name="claude-haiku-5-5-20260101")

    assert event["gen_ai.response.model"] == "claude-haiku-5-5-20260101"


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("gen_ai.usage.input_tokens", 40),
        ("gen_ai.usage.output_tokens", 12),
        ("gen_ai.usage.cache_read.input_tokens", 5),
        ("gen_ai.usage.cache_creation.input_tokens", 10),
    ],
)
def test_it_should_record_the_token_usage_of_the_call(field, expected):
    event = _streamed_call()

    assert event[field] == expected


def test_it_should_record_the_stop_reason_as_the_finish_reasons():
    event = _streamed_call(stop_reason="max_tokens")

    assert event["gen_ai.response.finish_reasons"] == ["max_tokens"]


def test_it_should_record_a_refusal_in_the_model_call():
    event = _streamed_call(stop_reason="refusal")

    assert event["gen_ai.response.finish_reasons"] == ["refusal"]


def test_it_should_record_the_cost_priced_by_cost_usd():
    event = _streamed_call()

    expected = cost_usd("anthropic", "claude-haiku-5-5", USAGE)
    assert event["pet_helper.cost_usd"] == pytest.approx(float(expected))


def test_it_should_use_the_run_id_as_the_span_id():
    run_id = uuid4()

    _, tracer = _invoke(_streaming_chat(_text("Olá"), _final()), run_id=run_id)

    assert tracer.only("model.call")["span_id"] == str(run_id)


def test_it_should_use_the_parent_run_id_as_the_parent_span_id():
    parent_run_id = uuid4()
    model = _streaming_chat(_text("Olá"), _final())
    tracer = _RecordingTracer()
    handler = TraceCallbackHandler()

    with turn_scope(tracer, "session-1", 1):
        RunnableLambda(lambda messages: model.invoke(messages)).invoke(
            [HumanMessage("oi")],
            config={"callbacks": [handler], "run_id": parent_run_id},
        )

    assert tracer.only("model.call")["parent_span_id"] == str(parent_run_id)


def test_it_should_record_the_duration_in_milliseconds():
    model = _streaming_chat(_text("Olá"), _final(), delays=[FIRST_TEXT_DELAY, 0.0])

    event = _model_call(model)

    assert event["duration_ms"] >= FIRST_TEXT_DELAY * 1000


# time to first chunk


@pytest.mark.parametrize(
    "leading_chunk",
    [
        pytest.param(AIMessageChunk(content=[]), id="empty-blocks"),
        pytest.param(AIMessageChunk(content=""), id="empty-string"),
        pytest.param(
            AIMessageChunk(
                content=[{"type": "thinking", "thinking": "hm", "index": 0}]
            ),
            id="thinking-only",
        ),
    ],
)
def test_it_should_not_stop_the_clock_on_a_chunk_without_text(leading_chunk):
    model = _streaming_chat(
        leading_chunk,
        _text("Olá"),
        _final(),
        delays=[0.0, FIRST_TEXT_DELAY, 0.0],
    )

    event = _model_call(model)

    assert event["gen_ai.response.time_to_first_chunk"] >= FIRST_TEXT_DELAY * 0.8


@pytest.mark.parametrize("as_blocks", [False, True], ids=["string", "text-blocks"])
def test_it_should_stop_the_clock_at_the_first_text_chunk(as_blocks):
    model = _streaming_chat(
        _text("Olá", as_blocks=as_blocks),
        _text("!", as_blocks=as_blocks),
        _final(),
        delays=[FIRST_TEXT_DELAY, LATE_DELAY, 0.0],
    )

    event = _model_call(model)

    assert (
        FIRST_TEXT_DELAY * 0.8
        <= event["gen_ai.response.time_to_first_chunk"]
        < LATE_DELAY
    )


def test_it_should_mark_a_streamed_call_as_streaming():
    event = _streamed_call()

    assert event["pet_helper.streaming"] is True


def test_it_should_mark_a_call_that_did_not_stream_as_not_streaming():
    event = _model_call(_FakeChat(reply=AIMessage("ok", usage_metadata=USAGE)))

    assert event["pet_helper.streaming"] is False


def test_it_should_record_a_null_time_to_first_chunk_when_the_call_did_not_stream():
    event = _model_call(_FakeChat(reply=AIMessage("ok", usage_metadata=USAGE)))

    assert event["gen_ai.response.time_to_first_chunk"] is None


def test_it_should_keep_the_duration_when_the_call_did_not_stream():
    event = _model_call(_FakeChat(reply=AIMessage("ok", usage_metadata=USAGE)))

    assert event["duration_ms"] >= 0


# retries and fallback


def test_it_should_mark_a_primary_call_as_not_a_fallback():
    event = _streamed_call()

    assert event["pet_helper.fallback"] is False


def test_it_should_emit_one_model_call_per_retry_attempt():
    flaky = _FakeChat(
        provider="ollama",
        model="llama3.1:8b",
        failures=[
            httpx.ConnectError("down", request=httpx.Request("POST", "http://x"))
        ],
        reply=AIMessage("ok"),
    )

    events = _gateway_call_events(OllamaGateway(chat_model=flaky))

    assert len(events) == 2


def test_it_should_record_the_error_type_of_a_failed_attempt():
    flaky = _FakeChat(
        provider="ollama",
        model="llama3.1:8b",
        failures=[
            httpx.ConnectError("down", request=httpx.Request("POST", "http://x"))
        ],
        reply=AIMessage("ok"),
    )

    events = _gateway_call_events(OllamaGateway(chat_model=flaky))

    assert events[0]["error.type"] == "ConnectError"


def _fallback_events() -> list[dict]:
    primary = _FakeChat(failures=[_connection_error()])
    fallback = _FakeChat(provider="ollama", model="llama3.1:8b", reply=AIMessage("ok"))
    gateway = AnthropicGateway(
        chat_model=primary, fallback=OllamaGateway(chat_model=fallback)
    )
    return _gateway_call_events(gateway)


def test_it_should_emit_one_model_call_per_provider_attempt_when_falling_back():
    events = _fallback_events()

    assert [event["gen_ai.provider.name"] for event in events] == [
        "anthropic",
        "ollama",
    ]


def test_it_should_flag_only_the_fallback_attempt_as_a_fallback():
    events = _fallback_events()

    assert [event["pet_helper.fallback"] for event in events] == [False, True]


# tool.call


def test_it_should_record_the_tool_name():
    event = _tool_call(_get_order, {"order_number": "PED-10001"})

    assert event["gen_ai.tool.name"] == "get_order"


def test_it_should_record_the_tool_arguments():
    event = _tool_call(_get_order, {"order_number": "PED-10001"})

    assert event["gen_ai.tool.call.arguments"] == {"order_number": "PED-10001"}


def test_it_should_record_an_ok_status_for_a_tool_that_succeeded():
    event = _tool_call(_get_order, {"order_number": "PED-10001"})

    assert event["pet_helper.status"] == "ok"


def test_it_should_record_an_error_status_for_a_tool_that_failed():
    tracer = _RecordingTracer()

    with pytest.raises(RuntimeError):
        _tool_call(_failing_tool, {"query": "x"}, tracer=tracer)

    assert tracer.only("tool.call")["pet_helper.status"] == "error"


def test_it_should_record_the_validation_error_text_when_the_arguments_are_invalid():
    tracer = _RecordingTracer()

    with pytest.raises(ValidationError):
        _tool_call(_get_order, {"order_number": "not-an-order"}, tracer=tracer)

    assert "order_number" in tracer.only("tool.call")["error.message"]


def test_it_should_write_only_the_length_of_the_tool_arguments_without_content(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PET_HELPER_TRACE_CONTENT", "0")
    tracer = Tracer(tmp_path, mask=lambda text: text)

    with turn_scope(tracer, "session-1", 1):
        _get_order.invoke(
            {"order_number": "PED-10001"},
            config={"callbacks": [TraceCallbackHandler()]},
        )

    written = (tmp_path / "session-1.jsonl").read_text(encoding="utf-8")
    assert "PED-10001" not in written


# budget hooks and failure isolation


def test_it_should_ask_the_callback_to_raise_handler_errors():
    handler = TraceCallbackHandler()

    assert handler.raise_error is True


def test_it_should_not_stop_the_model_call_when_the_sink_raises():
    reply, _ = _invoke(_streaming_chat(_text("Olá"), _final()), tracer=_RaisingTracer())

    assert reply.text == "Olá"


def test_it_should_not_stop_a_tool_call_when_the_sink_raises():
    tracer = _RaisingTracer()

    with turn_scope(tracer, "session-1", 1):
        result = _get_order.invoke(
            {"order_number": "PED-10001"},
            config={"callbacks": [TraceCallbackHandler()]},
        )

    assert result == "found"


def test_it_should_call_before_call_with_the_reserve_before_the_request():
    reserves: list[Decimal] = []
    model = _streaming_chat(_text("Olá"), _final())
    handler = TraceCallbackHandler(reserve=RESERVE, before_call=reserves.append)

    _invoke(model, handler)

    assert reserves == [RESERVE]


def test_it_should_not_send_the_request_when_before_call_raises_budget_exceeded():
    def refuse(reserve):
        raise BudgetExceeded("cap reached")

    model = _streaming_chat(_text("Olá"), _final())
    handler = TraceCallbackHandler(reserve=RESERVE, before_call=refuse)

    with pytest.raises(BudgetExceeded):
        _invoke(model, handler)

    assert model.requests == []


def test_it_should_send_the_request_when_before_call_fails_with_its_own_bug():
    def broken(reserve):
        raise RuntimeError("bug in the hook")

    model = _streaming_chat(_text("Olá"), _final())
    handler = TraceCallbackHandler(reserve=RESERVE, before_call=broken)

    reply, _ = _invoke(model, handler)

    assert reply.text == "Olá"


def test_it_should_pass_the_cost_of_a_finished_call_to_on_cost():
    costs: list[Decimal] = []
    handler = TraceCallbackHandler(reserve=RESERVE, on_cost=costs.append)

    _invoke(_streaming_chat(_text("Olá"), _final()), handler)

    assert costs == [cost_usd("anthropic", "claude-haiku-5-5", USAGE)]


def test_it_should_not_stop_the_call_when_on_cost_fails_with_its_own_bug():
    def broken(cost):
        raise RuntimeError("bug in the hook")

    handler = TraceCallbackHandler(reserve=RESERVE, on_cost=broken)

    reply, _ = _invoke(_streaming_chat(_text("Olá"), _final()), handler)

    assert reply.text == "Olá"


def test_it_should_pass_the_usage_of_a_cut_call_to_on_error():
    usages: list[Any] = []
    cut = _streaming_chat(
        _text("Olá"),
        _final(),
        error_after_chunks=_connection_error(),
    )
    handler = TraceCallbackHandler(reserve=RESERVE, on_error=usages.append)

    with pytest.raises(anthropic.APIConnectionError):
        _invoke(cut, handler)

    assert usages == [USAGE]


def test_it_should_pass_no_usage_to_on_error_when_the_call_failed_before_any_usage():
    usages: list[Any] = []
    failing = _streaming_chat(failures=[_connection_error()])
    handler = TraceCallbackHandler(reserve=RESERVE, on_error=usages.append)

    with pytest.raises(anthropic.APIConnectionError):
        _invoke(failing, handler)

    assert usages == [None]


def test_it_should_pass_no_usage_to_on_error_when_a_call_ends_without_usage():
    usages: list[Any] = []
    model = _streaming_chat(_text("Olá"), AIMessageChunk(content=""))
    handler = TraceCallbackHandler(reserve=RESERVE, on_error=usages.append)

    _invoke(model, handler)

    assert usages == [None]


def test_it_should_not_call_on_cost_for_a_call_that_ended_without_usage():
    costs: list[Decimal] = []
    model = _streaming_chat(_text("Olá"), AIMessageChunk(content=""))
    handler = TraceCallbackHandler(reserve=RESERVE, on_cost=costs.append)

    _invoke(model, handler)

    assert costs == []


# unknown prices


def test_it_should_stop_the_call_on_an_unknown_price_in_a_strict_round():
    model = _streaming_chat(_text("Olá"), _final(), model="claude-unknown-9")

    with pytest.raises(UnknownPriceError):
        _invoke(model, TraceCallbackHandler(strict=True))


def test_it_should_flag_an_unknown_price_outside_a_strict_round():
    model = _streaming_chat(_text("Olá"), _final(), model="claude-unknown-9")

    event = _model_call(model)

    assert event["pet_helper.price_unknown"] is True


def test_it_should_record_no_cost_for_an_unknown_price_outside_a_strict_round():
    model = _streaming_chat(_text("Olá"), _final(), model="claude-unknown-9")

    event = _model_call(model)

    assert event["pet_helper.cost_usd"] is None
