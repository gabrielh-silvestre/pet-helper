import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError

from pet_helper.catalog import Catalog
from pet_helper.conversation.graph import HANDOFF_REPLY, build_graph, handoff_state
from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.conversation.state import EscalationReason
from pet_helper.conversation.tools import ToolRegistry, build_tool_registry
from pet_helper.gateway import ModelGateway, build_gateway
from pet_helper.guard import Guard, extract_cited_titles, normalize_source_title
from pet_helper.knowledge import PolicyHit, PolicyIndex
from pet_helper.orders import Clock, MockOrderApi, OrderService
from pet_helper.trace import (
    NullTracer,
    TraceCallbackHandler,
    Tracer,
    TracerPort,
    emit,
    turn_scope,
)

KIT_DIR_ENV = "PET_HELPER_KIT_DIR"
DEFAULT_KIT_DIR = "kit"
# backstop only: well above the tool_rounds cap (3 supersteps per round)
RECURSION_LIMIT = 60


class KitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reply:
    text: str
    sources: tuple[str, ...] = ()
    escalation: EscalationReason | None = None


def _cited_sources(text: str, retrieved: Sequence[PolicyHit]) -> tuple[str, ...]:
    source_by_title = {
        normalize_source_title(hit.title): hit.source for hit in retrieved
    }
    cited = (normalize_source_title(title) for title in extract_cited_titles(text))
    return tuple(
        dict.fromkeys(
            source_by_title[title] for title in cited if title in source_by_title
        )
    )


class Assistant:
    def __init__(
        self,
        gateway: ModelGateway,
        guard: Guard,
        registry: ToolRegistry | None = None,
        tracer: TracerPort | None = None,
        handler_factory: Callable[[], TraceCallbackHandler] = TraceCallbackHandler,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        selected_registry = registry if registry is not None else ToolRegistry()
        self._graph = build_graph(
            gateway, guard, selected_registry.tools, system_prompt=system_prompt
        )
        self._tracer: TracerPort = tracer if tracer is not None else NullTracer()
        self._handler_factory = handler_factory
        self._turns: dict[str, int] = {}

    def snapshot(self, session_id: str) -> Mapping[str, Any]:
        return self._graph.get_state({"configurable": {"thread_id": session_id}}).values

    def escalation_cause(self, session_id: str) -> str | None:
        state = self._graph.get_state({"configurable": {"thread_id": session_id}})
        return state.values.get("escalation_cause")

    def close(self) -> None:
        if isinstance(self._tracer, Tracer):
            self._tracer.shutdown()

    def turn(self, session_id: str, text: str) -> Reply:
        turn_number = self._turns.get(session_id, 0) + 1
        self._turns[session_id] = turn_number
        with turn_scope(self._tracer, session_id, turn_number):
            emit("turn.start", **{"gen_ai.input.messages": text})
            reply = self._run_turn(session_id, text)
            if reply.escalation is not None:
                emit("escalation", **{"pet_helper.escalation_reason": reply.escalation})
            emit(
                "turn.end",
                **{
                    "gen_ai.output.messages": reply.text,
                    "pet_helper.sources": list(reply.sources),
                },
            )
        return reply

    def _run_turn(self, session_id: str, text: str) -> Reply:
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": RECURSION_LIMIT,
            "callbacks": [self._handler_factory()],
        }
        try:
            state = self._graph.invoke({"user_text": text}, config)
        except GraphRecursionError:
            self._graph.update_state(
                config,
                handoff_state(HANDOFF_REPLY, "recursion_limit"),
                as_node="handoff",
            )
            state = self._graph.get_state(config).values
        last_message = state["messages"][-1]
        return Reply(
            text=str(last_message.text),
            sources=_cited_sources(last_message.text, state.get("retrieved", ())),
            escalation=state.get("escalation"),
        )


def _resolve_kit_dir(kit_dir: Path | None) -> Path:
    if kit_dir is not None:
        return kit_dir.resolve()
    configured = os.environ.get(KIT_DIR_ENV) or DEFAULT_KIT_DIR
    return Path(configured).resolve()


def _validate_kit_paths(kit_dir: Path) -> tuple[Path, Path, Path]:
    policies_dir = kit_dir / "policies"
    catalog_path = kit_dir / "catalog.csv"
    orders_path = kit_dir / "orders.json"
    if (
        not kit_dir.is_dir()
        or not policies_dir.is_dir()
        or not any(policies_dir.glob("*.md"))
        or not catalog_path.is_file()
        or not orders_path.is_file()
    ):
        raise KitError(f"Kit directory is missing or incomplete: {kit_dir}")
    return policies_dir, catalog_path, orders_path


def _load_kit(
    kit_dir: Path, today: date | None
) -> tuple[PolicyIndex, Catalog, OrderService]:
    policies_dir, catalog_path, orders_path = _validate_kit_paths(kit_dir)
    orders = OrderService(
        MockOrderApi(orders_path),
        clock=Clock(today),
        trace_hook=lambda reason: emit(
            "order.refused", **{"pet_helper.reason": reason}
        ),
    )
    return PolicyIndex.load(policies_dir), Catalog(catalog_path), orders


def build_kit_registry(
    kit_dir: Path | None = None, today: date | None = None
) -> ToolRegistry:
    policies, catalog, orders = _load_kit(_resolve_kit_dir(kit_dir), today)
    return build_tool_registry(policies=policies, catalog=catalog, orders=orders)


def build_assistant(
    gateway: ModelGateway | None = None,
    registry: ToolRegistry | None = None,
    tracer: TracerPort | None = None,
    kit_dir: Path | None = None,
    today: date | None = None,
    trace_dir: Path | None = None,
    handler_factory: Callable[[], TraceCallbackHandler] = TraceCallbackHandler,
    system_prompt: str | None = None,
) -> Assistant:
    resolved_kit_dir = _resolve_kit_dir(kit_dir)
    if registry is None:
        policies, catalog, orders = _load_kit(resolved_kit_dir, today)
        selected_registry = build_tool_registry(
            policies=policies, catalog=catalog, orders=orders
        )
    else:
        # a given registry already holds the policies and orders; the guard only needs the catalog
        _, catalog_path, _ = _validate_kit_paths(resolved_kit_dir)
        catalog = Catalog(catalog_path)
        selected_registry = registry
    selected_gateway = gateway if gateway is not None else build_gateway()
    guard = Guard(catalog)
    selected_tracer = (
        Tracer(trace_dir, mask=guard.mask)
        if tracer is None and trace_dir is not None
        else tracer
    )
    return Assistant(
        selected_gateway,
        guard,
        selected_registry,
        selected_tracer,
        handler_factory,
        SYSTEM_PROMPT if system_prompt is None else system_prompt,
    )
