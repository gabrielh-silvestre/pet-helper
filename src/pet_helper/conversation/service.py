import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph

from pet_helper.catalog import Catalog
from pet_helper.conversation.graph import HANDOFF_REPLY, build_graph, handoff_state
from pet_helper.conversation.state import EscalationReason
from pet_helper.conversation.tools import ToolRegistry, build_tool_registry
from pet_helper.gateway import ModelGateway, build_gateway
from pet_helper.guard import Guard, extract_cited_titles, normalize_source_title
from pet_helper.knowledge import PolicyHit, PolicyIndex
from pet_helper.orders import MockOrderApi, OrderService

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


def _default_registry(
    policies: PolicyIndex | None,
    catalog: Catalog | None,
    orders: OrderService | None,
) -> ToolRegistry:
    services = (policies, catalog, orders)
    if all(service is None for service in services):
        return ToolRegistry()
    if policies is None or catalog is None or orders is None:
        raise ValueError(
            "policies, catalog and orders must be given together, or pass a registry"
        )
    return build_tool_registry(policies=policies, catalog=catalog, orders=orders)


class Assistant:
    def __init__(
        self,
        gateway: ModelGateway,
        guard: Guard,
        *,
        policies: PolicyIndex | None = None,
        catalog: Catalog | None = None,
        orders: OrderService | None = None,
        registry: ToolRegistry | None = None,
    ):
        # registry replaces the default tools: it is not merged with the services
        if registry is None:
            registry = _default_registry(policies, catalog, orders)
        self._graph = build_graph(gateway, guard, registry.tools)

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph

    def turn(self, session_id: str, text: str) -> Reply:
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": RECURSION_LIMIT,
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


def _resolve_kit_dir() -> Path:
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


def _load_kit(kit_dir: Path) -> tuple[PolicyIndex, Catalog, OrderService]:
    policies_dir, catalog_path, orders_path = _validate_kit_paths(kit_dir)
    return (
        PolicyIndex.load(policies_dir),
        Catalog(catalog_path),
        OrderService(MockOrderApi(orders_path)),
    )


def build_assistant(
    gateway: ModelGateway | None = None,
    registry: ToolRegistry | None = None,
) -> Assistant:
    policies, catalog, orders = _load_kit(_resolve_kit_dir())
    selected_gateway = gateway if gateway is not None else build_gateway()
    return Assistant(
        selected_gateway,
        Guard(catalog),
        policies=policies,
        catalog=catalog,
        orders=orders,
        registry=registry,
    )
