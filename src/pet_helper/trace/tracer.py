import json
import os
import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import langsmith
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span

from pet_helper.trace import otel
from pet_helper.trace.otel import (
    CONTENT_ENV,
    FREE_TEXT_FIELDS,
    MIRROR_PREFIX,
    OPAQUE_KEYS,
    SESSION_ID_KEY,
    TURN_KEY,
    MaskingSpanExporter,
    SessionRoutingExporter,
    SessionTagProcessor,
    length_of,
    map_strings,
    truncate,
)

# opaque ids from the runtime: masking could mangle a digit run that looks like a phone
_SPAN_ID_FIELDS = ("span_id", "parent_span_id")
# events that already became spans of their own
_SPAN_KINDS = frozenset({"model.call", "tool.call"})
# events whose fields are the input and output of the whole turn, kept as root attributes
_ROOT_MESSAGE_ROLES = {
    "turn.start": ("gen_ai.input.messages", "user"),
    "turn.end": ("gen_ai.output.messages", "assistant"),
}


class TracerPort(Protocol):
    def event(
        self, kind: str, *, session_id: str, turn: int, **fields: Any
    ) -> None: ...


class NullTracer:
    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        pass


class Tracer:
    def __init__(
        self,
        directory: Path,
        *,
        mask: Callable[[str], str],
        span_exporters: Sequence[SpanExporter] = (),
    ):
        if mask is None:
            raise ValueError("Tracer needs a mask function")
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._mask = mask
        self._with_content = os.environ.get(CONTENT_ENV) != "0"
        self._lock = threading.Lock()
        self._provider = TracerProvider()
        self._provider.add_span_processor(SessionTagProcessor())
        # synchronous: the metrics and the round read the span file right after the turn
        self._provider.add_span_processor(
            SimpleSpanProcessor(SessionRoutingExporter(self._directory / "spans", mask))
        )
        for exporter in span_exporters:
            self._provider.add_span_processor(
                BatchSpanProcessor(MaskingSpanExporter(exporter, mask))
            )
        self.span_tracer = self._provider.get_tracer("pet_helper")
        otel.install(self._provider)

    def shutdown(self) -> None:
        self._provider.shutdown()

    def _sink_value(self, name: str, value: Any) -> Any:
        if not self._with_content and name in FREE_TEXT_FIELDS:
            return length_of(value)
        if name in OPAQUE_KEYS:
            return map_strings(value, truncate)
        return map_strings(value, lambda text: truncate(self._mask(text)))

    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        span_ids = {
            name: fields.pop(name) for name in _SPAN_ID_FIELDS if name in fields
        }
        body = {name: self._sink_value(name, value) for name, value in fields.items()}
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "turn": turn,
            "event": kind,
            "trace_id": f"{session_id}:{turn}",
            **span_ids,
            **body,
        }
        # ASCII-only: U+2028 and U+0085 would split a line for readers using str.splitlines
        line = json.dumps(record, default=str)
        with (
            self._lock,
            (self._directory / f"{session_id}.jsonl").open(
                "a", encoding="utf-8"
            ) as sink,
        ):
            sink.write(line + "\n")


@dataclass(frozen=True)
class _TurnScope:
    tracer: TracerPort
    session_id: str
    turn: int
    span: Span | None


_CURRENT_TURN: ContextVar[_TurnScope | None] = ContextVar("current_turn", default=None)


@contextmanager
def turn_scope(tracer: TracerPort, session_id: str, turn: int):
    # a TracerPort that is not a Tracer has no span pipeline behind it
    root = (
        tracer.span_tracer.start_as_current_span(
            "turn", attributes={SESSION_ID_KEY: session_id, TURN_KEY: turn}
        )
        if isinstance(tracer, Tracer)
        else nullcontext(None)
    )
    # LangSmith's own tracer would send the unmasked runs: the masked spans are the only trace
    with langsmith.tracing_context(enabled=False), root as span:
        token = _CURRENT_TURN.set(_TurnScope(tracer, session_id, turn, span))
        try:
            yield
        finally:
            _CURRENT_TURN.reset(token)


def _attribute_value(value: Any) -> Any:
    if isinstance(value, str | bool | int | float):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def _record_on_span(span: Span, kind: str, fields: dict[str, Any]) -> None:
    if kind in _SPAN_KINDS:
        return
    if kind in _ROOT_MESSAGE_ROLES:
        key, role = _ROOT_MESSAGE_ROLES[kind]
        message = json.dumps(
            [{"role": role, "content": fields[key]}], ensure_ascii=False
        )
        span.set_attributes(
            {name: _attribute_value(v) for name, v in {**fields, key: message}.items()}
        )
        return
    span.add_event(
        kind,
        {name: _attribute_value(v) for name, v in fields.items() if v is not None},
    )
    span.set_attribute(
        f"{MIRROR_PREFIX}{kind}",
        json.dumps(fields, default=str, ensure_ascii=False),
    )


def emit(kind: str, **fields: Any) -> None:
    scope = _CURRENT_TURN.get()
    if scope is None:
        return
    scope.tracer.event(kind, session_id=scope.session_id, turn=scope.turn, **fields)
    if scope.span is not None:
        _record_on_span(scope.span, kind, fields)
