import json
import logging
import os
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackManager
from opentelemetry import trace
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.instrumentation.langchain.callback_handler import (
    TraceloopCallbackHandler,
)
from opentelemetry.sdk.trace import (
    Event,
    ReadableSpan,
    Span,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Status

CONTENT_ENV = "PET_HELPER_TRACE_CONTENT"
MAX_FIELD_CHARS = 10_000
SESSION_ID_KEY = "session_id"
TURN_KEY = "turn"

_OPENLLMETRY_CONTENT_ENV = "TRACELOOP_TRACE_CONTENT"
MIRROR_PREFIX = "langsmith.metadata."

FREE_TEXT_FIELDS = frozenset(
    {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.retrieval.query.text",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
    }
)
# names and ids chosen by us, the runtime or the provider, never customer text: a dated model
# id such as claude-haiku-4-5-20251001 or an all-digit session id would be mangled by the
# phone and CPF masks. Matched by exact key, never by prefix.
OPAQUE_KEYS = frozenset(
    {
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.tool.name",
        "error.type",
        SESSION_ID_KEY,
        "langsmith.span.kind",
        f"{MIRROR_PREFIX}{SESSION_ID_KEY}",
    }
)

logger = logging.getLogger(__name__)

_active: trace.Tracer | None = None
_handler: TraceloopCallbackHandler | None = None


def map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [map_strings(item, transform) for item in value]
    return value


def truncate(text: str) -> str:
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return f"{text[:MAX_FIELD_CHARS]} [truncated {len(text) - MAX_FIELD_CHARS} chars]"


def length_of(value: Any) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))


class _SwitchableTracer(trace.Tracer):
    """Hands every span to the tracer of the last install.

    The instrumentation binds the tracer it gets at instrument() time, and its uninstrument()
    leaves the LangChain and LangGraph wrappers in place (it unwraps with paths unwrap() cannot
    resolve), so a second instrument() would keep sending spans to the first provider.
    """

    def start_span(self, *args: Any, **kwargs: Any) -> trace.Span:
        return (_active or trace.NoOpTracer()).start_span(*args, **kwargs)

    def start_as_current_span(self, *args: Any, **kwargs: Any) -> Any:
        return (_active or trace.NoOpTracer()).start_as_current_span(*args, **kwargs)


class _SwitchableProvider(trace.TracerProvider):
    def get_tracer(self, *args: Any, **kwargs: Any) -> trace.Tracer:
        return _SwitchableTracer()


def install(provider: TracerProvider) -> None:
    """Send the LangChain spans of OpenLLMetry to `provider`, replacing any earlier install."""
    global _active, _handler
    os.environ[_OPENLLMETRY_CONTENT_ENV] = "false"
    _active = provider.get_tracer("pet_helper.openllmetry")
    if _handler is not None:
        return
    LangchainInstrumentor(disable_trace_context_propagation=True).instrument(
        tracer_provider=_SwitchableProvider()
    )
    # lookup through the public LangChain API: the instrumentation adds its handler to every manager
    handler = next(
        (
            item
            for item in BaseCallbackManager([]).inheritable_handlers
            if type(item).__name__ == "TraceloopCallbackHandler"
        ),
        None,
    )
    if handler is None:
        raise RuntimeError(
            "OpenLLMetry did not register its LangChain callback handler"
        )
    _handler = handler


def uninstall() -> None:
    global _active
    _active = None


def run_span(run_id: UUID) -> trace.Span | None:
    if _handler is None:
        return None
    # shortcut: reads the package's internal spans map, breaks if a 0.62.x release renames it, swap for a public lookup if one appears
    holder = _handler.spans.get(run_id)
    return holder.span if holder is not None else None


def fail_run(run_id: UUID, error: BaseException) -> None:
    """End the span of a run whose own error callback will never run."""
    if _handler is not None:
        _handler.on_llm_error(error, run_id=run_id)


class SessionTagProcessor(SpanProcessor):
    """Copies the session and turn of the parent span to every child span."""

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        parent_attributes = getattr(
            trace.get_current_span(parent_context), "attributes", None
        )
        for key in (SESSION_ID_KEY, TURN_KEY):
            if parent_attributes and key in parent_attributes:
                span.set_attribute(key, parent_attributes[key])


def _without_mirrored_text(text: str) -> str:
    try:
        mirrored = json.loads(text)
    except ValueError:
        return text
    if not isinstance(mirrored, dict):
        return text
    return json.dumps(
        {
            key: length_of(value) if key in FREE_TEXT_FIELDS else value
            for key, value in mirrored.items()
        },
        ensure_ascii=False,
        default=str,
    )


class MaskingSpanExporter(SpanExporter):
    """The single point that masks, truncates and gates the text of a span before it leaves."""

    def __init__(self, delegate: SpanExporter, mask: Callable[[str], str]):
        self._delegate = delegate
        self._mask = mask

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with_content = os.environ.get(CONTENT_ENV) != "0"
        try:
            return self._delegate.export(
                [self._cleaned(span, with_content) for span in spans]
            )
        except Exception:
            # the processors only log a failed export once in 20 seconds
            logger.exception("Span export failed")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)

    def _text(self, text: str) -> str:
        return truncate(self._mask(text))

    def _attribute(self, key: str, value: Any, with_content: bool) -> Any:
        if key in OPAQUE_KEYS:
            return map_strings(value, truncate)
        if not with_content and key in FREE_TEXT_FIELDS:
            return length_of(value)
        if (
            not with_content
            and key.startswith(MIRROR_PREFIX)
            and isinstance(value, str)
        ):
            value = _without_mirrored_text(value)
        return map_strings(value, self._text)

    def _attributes(self, attributes: Any, with_content: bool) -> dict[str, Any]:
        return {
            key: self._attribute(key, value, with_content)
            for key, value in (attributes or {}).items()
        }

    def _cleaned(self, span: ReadableSpan, with_content: bool) -> ReadableSpan:
        description = span.status.description
        return ReadableSpan(
            name=self._text(span.name),
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=self._attributes(span.attributes, with_content),
            events=[
                Event(
                    self._text(item.name),
                    self._attributes(item.attributes, with_content),
                    item.timestamp,
                )
                for item in span.events
            ],
            links=[
                Link(link.context, self._attributes(link.attributes, with_content))
                for link in span.links
            ],
            kind=span.kind,
            status=Status(
                span.status.status_code,
                self._text(description) if description else None,
            ),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )


class SessionRoutingExporter(SpanExporter):
    """Writes each span as one JSON line in the file of its session, masked first."""

    def __init__(self, directory: Path, mask: Callable[[str], str]):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._mask = mask
        self._files = ExitStack()
        self._sessions: dict[str, SpanExporter] = {}
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            for span in spans:
                session_id = (span.attributes or {}).get(SESSION_ID_KEY)
                # a span outside any turn has no session file to go to
                if session_id is not None:
                    self._exporter_of(str(session_id)).export([span])
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            self._files.close()
            self._sessions.clear()

    def _exporter_of(self, session_id: str) -> SpanExporter:
        if session_id not in self._sessions:
            sink: IO[str] = self._files.enter_context(
                (self._directory / f"{session_id}.jsonl").open("a", encoding="utf-8")
            )
            self._sessions[session_id] = MaskingSpanExporter(
                ConsoleSpanExporter(
                    out=sink, formatter=lambda span: span.to_json(indent=None) + "\n"
                ),
                self._mask,
            )
        return self._sessions[session_id]
