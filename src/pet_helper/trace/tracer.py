import json
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

CONTENT_ENV = "PET_HELPER_TRACE_CONTENT"
MAX_FIELD_CHARS = 10_000

_FREE_TEXT_FIELDS = frozenset(
    {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.retrieval.query.text",
        "gen_ai.tool.call.arguments",
    }
)
# opaque ids from the runtime: masking could mangle a digit run that looks like a phone
_SPAN_ID_FIELDS = ("span_id", "parent_span_id")
# names chosen by us or by the provider, never customer text: a dated model id such as
# claude-haiku-4-5-20251001 would otherwise lose its date to the phone mask
_OPAQUE_FIELDS = frozenset(
    {
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.tool.name",
        "error.type",
    }
)


class TracerPort(Protocol):
    def event(
        self, kind: str, *, session_id: str, turn: int, **fields: Any
    ) -> None: ...


class NullTracer:
    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        pass


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_map_strings(item, transform) for item in value]
    return value


def _truncate(text: str) -> str:
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return f"{text[:MAX_FIELD_CHARS]} [truncated {len(text) - MAX_FIELD_CHARS} chars]"


def _length_of(value: Any) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))


class Tracer:
    def __init__(self, directory: Path, *, mask: Callable[[str], str]):
        if mask is None:
            raise ValueError("Tracer needs a mask function")
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._mask = mask
        self._with_content = os.environ.get(CONTENT_ENV) != "0"
        self._lock = threading.Lock()

    def _sink_value(self, name: str, value: Any) -> Any:
        if not self._with_content and name in _FREE_TEXT_FIELDS:
            return _length_of(value)
        if name in _OPAQUE_FIELDS:
            return _map_strings(value, _truncate)
        return _map_strings(value, lambda text: _truncate(self._mask(text)))

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


_CURRENT_TURN: ContextVar[_TurnScope | None] = ContextVar("current_turn", default=None)


@contextmanager
def turn_scope(tracer: TracerPort, session_id: str, turn: int):
    token = _CURRENT_TURN.set(_TurnScope(tracer, session_id, turn))
    try:
        yield
    finally:
        _CURRENT_TURN.reset(token)


def emit(kind: str, **fields: Any) -> None:
    scope = _CURRENT_TURN.get()
    if scope is not None:
        scope.tracer.event(kind, session_id=scope.session_id, turn=scope.turn, **fields)
