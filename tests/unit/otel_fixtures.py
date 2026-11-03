"""Helpers for the OTel span tests that need the opentelemetry packages."""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackManager
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from pet_helper.catalog import Catalog
from pet_helper.conversation import build_assistant
from pet_helper.guard import Guard
from pet_helper.trace import Tracer

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SESSION_ID = "session-otel"
RAW_PII = [
    "529.982.247-25",
    "52998224725",
    "maria.silva@example.com",
    "(11) 98888-7777",
]
PII_TEXT = "cpf 529.982.247-25 ou 52998224725, e-mail maria.silva@example.com, tel (11) 98888-7777"
POLICY_REPLY = "Você pode devolver em 7 dias.\nFonte: Troca e devolução"
ENVIRONMENT = (
    "PET_HELPER_TRACE_CONTENT",
    "TRACELOOP_TRACE_CONTENT",
    "LANGSMITH_API_KEY",
    "PET_HELPER_KIT_DIR",
    "PET_HELPER_TODAY",
)


@pytest.fixture
def _otel_clean(monkeypatch):
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    yield
    # lazy: the module is the production code under test and the instrumentor is a process singleton
    try:
        from pet_helper.trace import otel
    except ImportError:
        return
    otel.uninstall()


def mask() -> Callable[[str], str]:
    return Guard(Catalog(KIT_DIR / "catalog.csv")).mask


def traced(
    directory: Path, *exporters: SpanExporter
) -> tuple[Tracer, InMemorySpanExporter]:
    memory = InMemorySpanExporter()
    tracer = Tracer(directory, mask=mask(), span_exporters=[memory, *exporters])
    return tracer, memory


def finish(tracer: Tracer, memory: InMemorySpanExporter) -> list[ReadableSpan]:
    tracer.shutdown()
    return list(memory.get_finished_spans())


def run_turns(
    directory: Path, gateway: Any, *texts: str, session_id: str = SESSION_ID, **build
) -> list[ReadableSpan]:
    tracer, memory = traced(directory)
    assistant = build_assistant(
        gateway=gateway, tracer=tracer, kit_dir=KIT_DIR, **build
    )
    for text in texts:
        assistant.turn(session_id, text)
    return finish(tracer, memory)


def roots(spans: Iterable[ReadableSpan]) -> list[ReadableSpan]:
    return [span for span in spans if span.parent is None]


def attribute(span: ReadableSpan, name: str) -> Any:
    return (span.attributes or {}).get(name)


def model_spans(spans: Iterable[ReadableSpan]) -> list[ReadableSpan]:
    return [s for s in spans if attribute(s, "gen_ai.operation.name") == "chat"]


def tool_spans(spans: Iterable[ReadableSpan]) -> list[ReadableSpan]:
    return [s for s in spans if attribute(s, "traceloop.span.kind") == "tool"]


def event_names(span: ReadableSpan) -> list[str]:
    return [event.name for event in span.events]


def event(span: ReadableSpan, name: str) -> Event:
    return next(item for item in span.events if item.name == name)


def descends_from(
    span: ReadableSpan, ancestor: ReadableSpan, spans: Sequence[ReadableSpan]
) -> bool:
    by_id = {item.context.span_id: item for item in spans}
    current = span
    while current.parent is not None:
        if current.parent.span_id == ancestor.context.span_id:
            return True
        current = by_id[current.parent.span_id]
    return False


def strings_of(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in strings_of(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in strings_of(item)]
    return []


def all_strings(span: ReadableSpan) -> list[str]:
    texts = [span.name, *strings_of(span.attributes)]
    if span.status.description:
        texts.append(span.status.description)
    for item in span.events:
        texts += [item.name, *strings_of(item.attributes)]
    for link in span.links:
        texts += strings_of(link.attributes)
    return texts


def span_file(directory: Path, session_id: str = SESSION_ID) -> Path:
    return directory / "spans" / f"{session_id}.jsonl"


def file_lines(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def open_traceloop_spans() -> dict:
    handler = next(
        item
        for item in BaseCallbackManager([]).inheritable_handlers
        if type(item).__name__ == "TraceloopCallbackHandler"
    )
    return dict(handler.spans)


def make_span(
    *,
    name: str = "operation",
    attributes: Mapping[str, Any] | None = None,
    events: Sequence[Event] = (),
    links: Sequence[Link] = (),
    status: Status | None = None,
    session_id: str | None = "session-1",
    span_id: int = 1,
) -> ReadableSpan:
    context = SpanContext(
        trace_id=0xABC, span_id=span_id, is_remote=False, trace_flags=TraceFlags(1)
    )
    session = {"session_id": session_id} if session_id is not None else {}
    return ReadableSpan(
        name=name,
        context=context,
        attributes={**session, **(attributes or {})},
        events=list(events),
        links=list(links),
        status=status or Status(StatusCode.OK),
        start_time=1,
        end_time=2,
        resource=Resource.create({}),
        instrumentation_scope=InstrumentationScope("test"),
    )


def exported_by_masking(spans: Sequence[ReadableSpan]) -> list[ReadableSpan]:
    from pet_helper.trace.otel import MaskingSpanExporter

    memory = InMemorySpanExporter()
    result = MaskingSpanExporter(memory, mask()).export(spans)
    assert result is SpanExportResult.SUCCESS
    return list(memory.get_finished_spans())


@pytest.fixture
def langsmith_on(monkeypatch):
    """LangSmith tracing switched on in the environment; yields the tracers it creates."""
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith.utils import get_env_var

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    # nothing may reach the real service, and get_env_var caches the first value it reads
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    get_env_var.cache_clear()
    created: list[Any] = []
    original = LangChainTracer.__init__

    def spy(self, *args, **kwargs):
        created.append(self)
        original(self, *args, **kwargs)

    monkeypatch.setattr(LangChainTracer, "__init__", spy)
    yield created
    monkeypatch.undo()
    get_env_var.cache_clear()


def count_shutdowns(monkeypatch) -> list[int]:
    calls: list[int] = []
    original = Tracer.shutdown

    def spy(self):
        calls.append(1)
        original(self)

    monkeypatch.setattr(Tracer, "shutdown", spy)
    return calls
