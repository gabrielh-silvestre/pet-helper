"""The MaskingSpanExporter: the single point that masks, truncates and gates span text."""

import json

import pytest
from opentelemetry.sdk.trace import Event
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from pet_helper.trace.otel import MaskingSpanExporter
from tests.unit.otel_fixtures import (
    PII_TEXT,
    RAW_PII,
    all_strings,
    attribute,
    exported_by_masking,
    make_span,
    mask,
)

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")

CONTENT_ENV = "PET_HELPER_TRACE_CONTENT"
FREE_TEXT_KEYS = [
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.retrieval.query.text",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
]
OPAQUE_STRING_KEYS = [
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.tool.name",
    "error.type",
    "session_id",
    "langsmith.span.kind",
    "langsmith.metadata.session_id",
]
# a digit run that is a valid CPF: masked under any key that is not on the opaque list
OPAQUE_PROBE = "52998224725"
MASKED_KEYS = [
    "langsmith.metadata.retrieval",
    "langsmith.metadata.guard.input",
    "langsmith.anything",
    "pet_helper.unretrieved_sources",
    "pet_helper.reason",
]
PII_LOCATIONS = {
    "name": lambda: make_span(name=PII_TEXT),
    "attribute": lambda: make_span(attributes={"custom.note": PII_TEXT}),
    "list attribute": lambda: make_span(attributes={"custom.notes": [PII_TEXT, "ok"]}),
    "status description": lambda: make_span(status=Status(StatusCode.ERROR, PII_TEXT)),
    "event name": lambda: make_span(events=[Event(PII_TEXT, {}, 1)]),
    "event attribute": lambda: make_span(
        events=[Event("note", {"custom.note": PII_TEXT}, 1)]
    ),
    "exception message": lambda: make_span(
        events=[Event("exception", {"exception.message": PII_TEXT}, 1)]
    ),
    "exception stacktrace": lambda: make_span(
        events=[Event("exception", {"exception.stacktrace": PII_TEXT}, 1)]
    ),
    "link attribute": lambda: make_span(
        links=[
            Link(
                SpanContext(0xABC, 2, False, TraceFlags(1)),
                {"custom.note": PII_TEXT},
            )
        ]
    ),
}


class _RecordingExporter(SpanExporter):
    def __init__(self) -> None:
        self.shutdowns = 0

    def export(self, spans):
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.shutdowns += 1


@pytest.mark.parametrize("location", PII_LOCATIONS)
def test_it_should_mask_pii_planted_in_every_string_field_of_a_span(location):
    span = PII_LOCATIONS[location]()

    (exported,) = exported_by_masking([span])

    written = "\n".join(all_strings(exported))
    assert [pii for pii in RAW_PII if pii in written] == []


def test_it_should_keep_the_non_text_parts_of_the_span():
    span = make_span(
        name="chat",
        attributes={"gen_ai.usage.input_tokens": 40, "pet_helper.cost_usd": 0.01},
    )

    (exported,) = exported_by_masking([span])

    assert (exported.name, dict(exported.attributes)) == (
        "chat",
        {
            "session_id": "session-1",
            "gen_ai.usage.input_tokens": 40,
            "pet_helper.cost_usd": 0.01,
        },
    )


@pytest.mark.parametrize("key", OPAQUE_STRING_KEYS)
def test_it_should_pass_the_exact_opaque_keys_intact(key):
    span = make_span(attributes={key: OPAQUE_PROBE})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, key) == OPAQUE_PROBE


def test_it_should_keep_a_dated_model_id_intact():
    span = make_span(attributes={"gen_ai.request.model": "claude-haiku-4-5-20251001"})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, "gen_ai.request.model") == "claude-haiku-4-5-20251001"


@pytest.mark.parametrize("key", MASKED_KEYS)
def test_it_should_mask_the_other_langsmith_and_pet_helper_keys(key):
    span = make_span(attributes={key: f"contato {PII_TEXT}"})

    (exported,) = exported_by_masking([span])

    assert [pii for pii in RAW_PII if pii in str(attribute(exported, key))] == []


@pytest.mark.parametrize("key", ["session_id_copy", "langsmith.metadata.session_id.x"])
def test_it_should_match_the_opaque_keys_by_exact_name_and_not_by_prefix(key):
    span = make_span(attributes={key: OPAQUE_PROBE})

    (exported,) = exported_by_masking([span])

    assert OPAQUE_PROBE not in str(attribute(exported, key))


def test_it_should_truncate_a_long_field_at_10000_characters_after_masking():
    padding = "a" * 9_980
    text = f"{padding} 529.982.247-25 {'b' * 200}"
    span = make_span(attributes={"gen_ai.input.messages": text})

    (exported,) = exported_by_masking([span])

    value = attribute(exported, "gen_ai.input.messages")
    kept, _, notice = value.partition(" [truncated ")
    assert (len(kept), notice.endswith(" chars]")) == (10_000, True)


def test_it_should_mask_a_cpf_that_crosses_the_truncation_boundary_as_a_whole():
    text = f"{'a' * 9_989} 529.982.247-25 {'b' * 200}"
    span = make_span(attributes={"gen_ai.input.messages": text})

    (exported,) = exported_by_masking([span])

    value = attribute(exported, "gen_ai.input.messages")
    assert [
        part for part in ("529.982", "247-25", "52998224725") if part in value
    ] == []


def test_it_should_not_truncate_a_field_shorter_than_the_limit():
    span = make_span(attributes={"gen_ai.output.messages": "x" * 9_999})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, "gen_ai.output.messages") == "x" * 9_999


@pytest.mark.parametrize("key", FREE_TEXT_KEYS)
def test_it_should_keep_only_the_size_of_free_text_when_content_is_off(
    monkeypatch, key
):
    monkeypatch.setenv(CONTENT_ENV, "0")
    span = make_span(attributes={key: "texto livre de teste"})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, key) == len("texto livre de teste")


def test_it_should_keep_only_the_size_of_free_text_in_an_event_when_content_is_off(
    monkeypatch,
):
    monkeypatch.setenv(CONTENT_ENV, "0")
    span = make_span(
        events=[
            Event("retrieval", {"gen_ai.retrieval.query.text": "segredo-de-teste"}, 1)
        ]
    )

    (exported,) = exported_by_masking([span])

    (event,) = exported.events
    assert event.attributes["gen_ai.retrieval.query.text"] == len("segredo-de-teste")


def test_it_should_keep_only_the_size_of_the_json_messages_when_content_is_off(
    monkeypatch,
):
    monkeypatch.setenv(CONTENT_ENV, "0")
    messages = json.dumps([{"role": "user", "content": "segredo-de-teste"}])
    span = make_span(attributes={"gen_ai.input.messages": messages})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, "gen_ai.input.messages") == len(messages)


def test_it_should_drop_the_text_of_the_event_mirror_when_content_is_off(monkeypatch):
    monkeypatch.setenv(CONTENT_ENV, "0")
    mirror = json.dumps({"gen_ai.retrieval.query.text": "segredo-de-teste"})
    span = make_span(attributes={"langsmith.metadata.retrieval": mirror})

    (exported,) = exported_by_masking([span])

    assert "segredo-de-teste" not in str(
        attribute(exported, "langsmith.metadata.retrieval")
    )


def test_it_should_keep_the_numbers_of_a_span_when_content_is_off(monkeypatch):
    monkeypatch.setenv(CONTENT_ENV, "0")
    span = make_span(attributes={"gen_ai.usage.input_tokens": 40})

    (exported,) = exported_by_masking([span])

    assert attribute(exported, "gen_ai.usage.input_tokens") == 40


def test_it_should_keep_the_free_text_masked_when_content_is_on():
    span = make_span(attributes={"gen_ai.input.messages": f"oi {PII_TEXT}"})

    (exported,) = exported_by_masking([span])

    value = attribute(exported, "gen_ai.input.messages")
    assert (value.startswith("oi "), [pii for pii in RAW_PII if pii in value]) == (
        True,
        [],
    )


def test_it_should_report_success_when_it_exports():
    exporter = MaskingSpanExporter(_RecordingExporter(), mask())

    result = exporter.export([make_span()])

    assert result is SpanExportResult.SUCCESS


def test_it_should_shut_the_wrapped_exporter_down():
    inner = _RecordingExporter()

    MaskingSpanExporter(inner, mask()).shutdown()

    assert inner.shutdowns == 1
