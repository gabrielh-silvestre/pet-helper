"""Contract with the OpenLLMetry instrumentation: what the spans carry and what the handler adds."""

import contextlib
import os
from decimal import Decimal
from functools import partial
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from pet_helper.conversation import ToolRegistry, build_assistant
from pet_helper.trace import BudgetExceeded, TraceCallbackHandler, otel, turn_scope
from tests.unit.otel_fixtures import (
    KIT_DIR,
    PII_TEXT,
    POLICY_REPLY,
    RAW_PII,
    SESSION_ID,
    all_strings,
    attribute,
    descends_from,
    finish,
    model_spans,
    open_traceloop_spans,
    roots,
    run_turns,
    tool_spans,
    traced,
)
from tests.unit.otel_support import ScriptedChat, ScriptedGateway, tool_call

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")

CUSTOMER_TEXT = "CLIENTE-SECRETO-ABC"
RESULT_TEXT = "RESULTADO-SECRETO-XYZ"


@tool("echo_note")
def _echo_note(query: str) -> str:
    """Echo a note."""
    return RESULT_TEXT


@tool("contact_note")
def _contact_note(query: str) -> str:
    """Return a note with contact data."""
    return f"Contato: {PII_TEXT}"


@tool("failing_note")
def _failing_note(query: str) -> str:
    """Always fails."""
    raise RuntimeError(f"backend down para maria.silva@example.com {query}")


def _registry(*tools) -> ToolRegistry:
    registry = ToolRegistry()
    for item in tools:
        registry.register(item)
    return registry


def _policy_spans(tmp_path):
    gateway = ScriptedGateway(
        [tool_call("search_policies", {"query": "devolução de ração"}), POLICY_REPLY]
    )
    return run_turns(tmp_path, gateway, "Como devolvo uma ração?")


def _direct_tool_call(tmp_path, tool_to_run, arguments=None):
    tracer, memory = traced(tmp_path)
    call = {
        "name": tool_to_run.name,
        "args": arguments or {"query": "x"},
        "id": "call_1",
        "type": "tool_call",
    }
    with turn_scope(tracer, SESSION_ID, 1), contextlib.suppress(RuntimeError):
        tool_to_run.invoke(call, config={"callbacks": [TraceCallbackHandler()]})
    return finish(tracer, memory)


# install


def test_it_should_send_the_spans_to_the_provider_of_the_last_install():
    first, second = InMemorySpanExporter(), InMemorySpanExporter()
    providers = []
    for exporter in (first, second):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        providers.append(provider)

    otel.install(providers[0])
    otel.install(providers[1])
    ScriptedChat(script=[AIMessage("oi")]).invoke([HumanMessage("oi")])

    assert (len(first.get_finished_spans()), len(second.get_finished_spans())) == (0, 1)


def test_it_should_force_the_openllmetry_content_capture_off(monkeypatch):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")

    otel.install(TracerProvider())

    assert os.environ["TRACELOOP_TRACE_CONTENT"] == "false"


def test_it_should_not_set_the_global_tracer_provider():
    provider = TracerProvider()

    otel.install(provider)

    assert trace.get_tracer_provider() is not provider


def test_it_should_return_no_span_for_an_unknown_run():
    otel.install(TracerProvider())

    assert otel.run_span(uuid4()) is None


# model span fed by the handler


def test_it_should_overwrite_the_provider_name_with_the_one_of_the_call(tmp_path):
    spans = _policy_spans(tmp_path)

    assert {attribute(span, "gen_ai.provider.name") for span in model_spans(spans)} == {
        "anthropic"
    }


def test_it_should_record_the_cost_on_the_model_span_without_streaming(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá!"], streaming=False), "Oi")

    span = model_spans(spans)[0]
    assert (
        attribute(span, "pet_helper.cost_usd") > 0,
        attribute(span, "pet_helper.streaming"),
    ) == (True, False)


def test_it_should_leave_no_openllmetry_span_open_after_a_turn(tmp_path):
    _policy_spans(tmp_path)

    assert open_traceloop_spans() == {}


# tool span


def test_it_should_name_the_tool_span_after_the_tool(tmp_path):
    spans = _policy_spans(tmp_path)

    (span,) = tool_spans(spans)
    assert (span.name, attribute(span, "gen_ai.tool.name")) == (
        "execute_tool search_policies",
        "search_policies",
    )


def test_it_should_create_the_tool_span_under_the_root_span(tmp_path):
    spans = _policy_spans(tmp_path)

    (root,) = roots(spans)
    (span,) = tool_spans(spans)
    assert descends_from(span, root, spans)


def test_it_should_record_the_tool_arguments_on_the_tool_span(tmp_path):
    spans = _policy_spans(tmp_path)

    (span,) = tool_spans(spans)
    assert "devolução de ração" in str(attribute(span, "gen_ai.tool.call.arguments"))


def test_it_should_record_the_tool_result_on_the_tool_span(tmp_path):
    spans = _policy_spans(tmp_path)

    (span,) = tool_spans(spans)
    assert "found" in str(attribute(span, "gen_ai.tool.call.result"))


def test_it_should_mark_a_successful_tool_span_with_status_ok(tmp_path):
    spans = _policy_spans(tmp_path)

    (span,) = tool_spans(spans)
    assert attribute(span, "pet_helper.status") == "ok"


def test_it_should_mask_the_pii_of_the_tool_result(tmp_path):
    spans = _direct_tool_call(tmp_path, _contact_note)

    (span,) = tool_spans(spans)
    result = attribute(span, "gen_ai.tool.call.result")
    assert ("Contato:" in result, [pii for pii in RAW_PII if pii in result]) == (
        True,
        [],
    )


def test_it_should_mask_the_pii_of_the_tool_arguments(tmp_path):
    spans = _direct_tool_call(tmp_path, _contact_note, {"query": PII_TEXT})

    (span,) = tool_spans(spans)
    arguments = str(attribute(span, "gen_ai.tool.call.arguments"))
    assert [pii for pii in RAW_PII if pii in arguments] == []


def test_it_should_mark_a_failed_tool_span_with_status_error(tmp_path):
    spans = _direct_tool_call(tmp_path, _failing_note)

    (span,) = tool_spans(spans)
    assert attribute(span, "pet_helper.status") == "error"


def test_it_should_record_the_masked_error_message_on_a_failed_tool_span(tmp_path):
    spans = _direct_tool_call(tmp_path, _failing_note)

    (span,) = tool_spans(spans)
    message = attribute(span, "error.message")
    assert ("backend down" in message, "maria.silva@example.com" in message) == (
        True,
        False,
    )


def test_it_should_leave_no_raw_pii_in_the_exception_event_of_a_failed_tool(tmp_path):
    spans = _direct_tool_call(tmp_path, _failing_note)

    (span,) = tool_spans(spans)
    assert "maria.silva@example.com" not in "\n".join(all_strings(span))


def test_it_should_keep_only_the_size_of_the_tool_result_when_content_is_off(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PET_HELPER_TRACE_CONTENT", "0")

    spans = _direct_tool_call(tmp_path, _contact_note)

    (span,) = tool_spans(spans)
    assert isinstance(attribute(span, "gen_ai.tool.call.result"), int)


# content capture of the package


def test_it_should_keep_the_customer_text_out_of_every_span_but_the_root(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")
    gateway = ScriptedGateway([tool_call("echo_note", {"query": "x"}), "Certo."])

    spans = run_turns(tmp_path, gateway, CUSTOMER_TEXT, registry=_registry(_echo_note))

    (root,) = roots(spans)
    others = [span for span in spans if span is not root]
    assert [s.name for s in others if CUSTOMER_TEXT in "\n".join(all_strings(s))] == []
    assert CUSTOMER_TEXT in attribute(root, "gen_ai.input.messages")


def test_it_should_keep_the_tool_result_out_of_every_span_but_the_tool_span(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")
    gateway = ScriptedGateway([tool_call("echo_note", {"query": "x"}), "Certo."])

    spans = run_turns(tmp_path, gateway, "Oi", registry=_registry(_echo_note))

    (tool_span,) = tool_spans(spans)
    others = [span for span in spans if span is not tool_span]
    assert [s.name for s in others if RESULT_TEXT in "\n".join(all_strings(s))] == []
    assert RESULT_TEXT in attribute(tool_span, "gen_ai.tool.call.result")


def test_it_should_carry_the_tool_result_only_in_the_result_attribute_of_the_tool_span(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")
    gateway = ScriptedGateway([tool_call("echo_note", {"query": "x"}), "Certo."])

    spans = run_turns(tmp_path, gateway, "Oi", registry=_registry(_echo_note))

    (tool_span,) = tool_spans(spans)
    carriers = [k for k, v in tool_span.attributes.items() if RESULT_TEXT in str(v)]
    assert carriers == ["gen_ai.tool.call.result"]


# cost brake


def test_it_should_leave_no_model_span_when_the_brake_refuses_before_the_call(tmp_path):
    def refuse(reserve: Decimal) -> None:
        raise BudgetExceeded("over the cap")

    tracer, memory = traced(tmp_path)
    handler = partial(TraceCallbackHandler, before_call=refuse)
    assistant = build_assistant(
        gateway=ScriptedGateway(["Olá!"]),
        tracer=tracer,
        kit_dir=KIT_DIR,
        handler_factory=handler,
    )

    with pytest.raises(BudgetExceeded):
        assistant.turn(SESSION_ID, "Oi")

    assert (len(model_spans(finish(tracer, memory))), open_traceloop_spans()) == (0, {})


def _spend_over_the_cap(tmp_path):
    def over_the_cap(cost: Decimal) -> None:
        raise BudgetExceeded("over the cap")

    tracer, memory = traced(tmp_path)
    handler = TraceCallbackHandler(on_cost=over_the_cap)
    with turn_scope(tracer, SESSION_ID, 1), pytest.raises(BudgetExceeded):
        ScriptedChat(script=[AIMessage("Olá!")]).invoke(
            [HumanMessage("oi")], config={"callbacks": [handler]}
        )
    return finish(tracer, memory)


def test_it_should_keep_the_tokens_on_the_model_span_when_the_cost_crosses_the_cap(
    tmp_path,
):
    spans = _spend_over_the_cap(tmp_path)

    (span,) = model_spans(spans)
    assert (
        attribute(span, "gen_ai.usage.input_tokens"),
        attribute(span, "gen_ai.usage.output_tokens"),
    ) == (40, 12)


def test_it_should_mark_the_model_span_as_an_error_when_the_cost_crosses_the_cap(
    tmp_path,
):
    spans = _spend_over_the_cap(tmp_path)

    (span,) = model_spans(spans)
    assert span.status.status_code is StatusCode.ERROR


def test_it_should_end_the_model_span_when_the_cost_crosses_the_cap(tmp_path):
    spans = _spend_over_the_cap(tmp_path)

    assert len(model_spans(spans)) == 1
