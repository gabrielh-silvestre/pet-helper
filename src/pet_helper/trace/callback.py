import functools
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.messages.ai import UsageMetadata, add_usage
from langchain_core.outputs import ChatGenerationChunk, LLMResult

from pet_helper.trace.cost import UnknownPriceError, cost_usd
from pet_helper.trace.tracer import emit

logger = logging.getLogger(__name__)

_FALLBACK_ACTIVE: ContextVar[bool] = ContextVar("fallback_active", default=False)


class BudgetExceeded(Exception):
    pass


@contextmanager
def fallback_scope() -> Iterator[None]:
    token = _FALLBACK_ACTIVE.set(True)
    try:
        yield
    finally:
        _FALLBACK_ACTIVE.reset(token)


@dataclass
class _ModelRun:
    provider: str
    model: str
    started: float
    fallback: bool
    first_chunk: float | None = None
    streamed: bool = False
    usage: UsageMetadata | None = None


@dataclass
class _ToolRun:
    name: str
    arguments: Any
    started: float


def _isolated(method: Callable[..., None]) -> Callable[..., None]:
    # LangChain swallows handler errors unless raise_error is set, so the handler lets
    # through only the two errors that must stop a round and logs every other failure
    @functools.wraps(method)
    def wrapper(self: "TraceCallbackHandler", *args: Any, **kwargs: Any) -> None:
        try:
            method(self, *args, **kwargs)
        except (BudgetExceeded, UnknownPriceError):
            raise
        except Exception:
            logger.exception("Trace callback %s failed", method.__name__)

    return wrapper


def _call_hook(hook: Callable[..., None] | None, *args: Any) -> None:
    if hook is None:
        return
    try:
        hook(*args)
    except BudgetExceeded:
        raise
    except Exception:
        logger.exception("Trace callback hook failed")


def _span_ids(run_id: UUID, parent_run_id: UUID | None) -> dict[str, str | None]:
    return {
        "span_id": str(run_id),
        "parent_span_id": str(parent_run_id) if parent_run_id else None,
    }


class TraceCallbackHandler(BaseCallbackHandler):
    raise_error = True

    def __init__(
        self,
        *,
        reserve: Decimal = Decimal(0),
        before_call: Callable[[Decimal], None] | None = None,
        on_cost: Callable[[Decimal], None] | None = None,
        on_error: Callable[[UsageMetadata | None], None] | None = None,
        strict: bool = False,
    ):
        self._reserve = reserve
        self._before_call = before_call
        self._on_cost = on_cost
        self._on_error = on_error
        self._strict = strict
        self._model_runs: dict[UUID, _ModelRun] = {}
        self._tool_runs: dict[UUID, _ToolRun] = {}

    @_isolated
    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        details = metadata or {}
        self._model_runs[run_id] = _ModelRun(
            provider=details.get("ls_provider", ""),
            model=details.get("ls_model_name", ""),
            started=time.perf_counter(),
            fallback=_FALLBACK_ACTIVE.get(),
        )
        try:
            _call_hook(self._before_call, self._reserve)
        except BudgetExceeded:
            # a refused call never starts, so no end or error callback will clear it
            del self._model_runs[run_id]
            raise

    @_isolated
    def on_llm_new_token(
        self,
        token: str,
        *,
        chunk: Any = None,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        run = self._model_runs.get(run_id)
        if run is None or not isinstance(chunk, ChatGenerationChunk):
            return
        run.streamed = True
        message = chunk.message
        if isinstance(message, AIMessage) and message.usage_metadata:
            # usage comes in the last chunk and a failed call hands the handler no partial result
            run.usage = add_usage(run.usage, message.usage_metadata)
        if run.first_chunk is None and message.text:
            run.first_chunk = time.perf_counter() - run.started

    @_isolated
    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        run = self._model_runs.pop(run_id, None)
        if run is None:
            return
        message = response.generations[0][0].message
        metadata = message.response_metadata if isinstance(message, AIMessage) else {}
        usage = (
            message.usage_metadata if isinstance(message, AIMessage) else None
        ) or run.usage
        unknown_price: UnknownPriceError | None = None
        cost: Decimal | None = None
        if usage:
            try:
                cost = cost_usd(run.provider, run.model, usage, strict=self._strict)
            except UnknownPriceError as error:
                unknown_price = error
        try:
            if cost is None:
                _call_hook(self._on_error, None)
            else:
                _call_hook(self._on_cost, cost)
        finally:
            self._emit_model_call(
                run,
                run_id,
                parent_run_id,
                usage=usage,
                cost=cost,
                price_unknown=bool(usage) and cost is None,
                response_model=metadata.get("model_name"),
                finish_reason=metadata.get("stop_reason")
                or metadata.get("done_reason"),
            )
        if unknown_price is not None:
            raise unknown_price

    @_isolated
    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        run = self._model_runs.pop(run_id, None)
        if run is None:
            return
        try:
            _call_hook(self._on_error, run.usage)
        finally:
            cost = cost_usd(run.provider, run.model, run.usage) if run.usage else None
            self._emit_model_call(
                run,
                run_id,
                parent_run_id,
                usage=run.usage,
                cost=cost,
                price_unknown=bool(run.usage) and cost is None,
                error_type=type(error).__name__,
            )

    def _emit_model_call(
        self,
        run: _ModelRun,
        run_id: UUID,
        parent_run_id: UUID | None,
        *,
        usage: UsageMetadata | None,
        cost: Decimal | None,
        price_unknown: bool,
        response_model: str | None = None,
        finish_reason: str | None = None,
        error_type: str | None = None,
    ) -> None:
        details = (usage or {}).get("input_token_details") or {}
        fields: dict[str, Any] = {
            **_span_ids(run_id, parent_run_id),
            "gen_ai.provider.name": run.provider,
            "gen_ai.request.model": run.model,
            "gen_ai.response.model": response_model,
            "gen_ai.response.time_to_first_chunk": run.first_chunk,
            "duration_ms": (time.perf_counter() - run.started) * 1000,
            "gen_ai.usage.input_tokens": (usage or {}).get("input_tokens"),
            "gen_ai.usage.output_tokens": (usage or {}).get("output_tokens"),
            "gen_ai.usage.cache_read.input_tokens": details.get("cache_read"),
            "gen_ai.usage.cache_creation.input_tokens": details.get("cache_creation"),
            "gen_ai.response.finish_reasons": [finish_reason] if finish_reason else [],
            "pet_helper.fallback": run.fallback,
            "pet_helper.streaming": run.streamed,
            "pet_helper.cost_usd": float(cost) if cost is not None else None,
            "pet_helper.price_unknown": price_unknown,
        }
        if error_type is not None:
            fields["error.type"] = error_type
        emit("model.call", **fields)

    @_isolated
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._tool_runs[run_id] = _ToolRun(
            name=kwargs.get("name") or serialized.get("name", ""),
            arguments=inputs if inputs is not None else input_str,
            started=time.perf_counter(),
        )

    @_isolated
    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._emit_tool_call(run_id, parent_run_id, "ok")

    @_isolated
    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._emit_tool_call(run_id, parent_run_id, "error", str(error))

    def _emit_tool_call(
        self,
        run_id: UUID,
        parent_run_id: UUID | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        run = self._tool_runs.pop(run_id, None)
        if run is None:
            return
        fields: dict[str, Any] = {
            **_span_ids(run_id, parent_run_id),
            "gen_ai.tool.name": run.name,
            "gen_ai.tool.call.arguments": run.arguments,
            "duration_ms": (time.perf_counter() - run.started) * 1000,
            "pet_helper.status": status,
        }
        if error_message is not None:
            fields["error.message"] = error_message
        emit("tool.call", **fields)
