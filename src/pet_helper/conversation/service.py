import os
from dataclasses import dataclass
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from pet_helper.catalog import Catalog
from pet_helper.conversation.graph import build_graph
from pet_helper.conversation.state import EscalationReason
from pet_helper.gateway import ModelGateway, build_gateway
from pet_helper.guard import Guard
from pet_helper.knowledge import PolicyIndex
from pet_helper.orders import OrderService
from pet_helper.orders.api import MockOrderApi

KIT_DIR_ENV = "PET_HELPER_KIT_DIR"
DEFAULT_KIT_DIR = "kit"


class KitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reply:
    text: str
    sources: tuple[str, ...] = ()
    escalation: EscalationReason | None = None


class Assistant:
    def __init__(
        self,
        gateway: ModelGateway,
        guard: Guard,
        *,
        policies: PolicyIndex | None = None,
        catalog: Catalog | None = None,
        orders: OrderService | None = None,
    ):
        self._policies = policies
        self._catalog = catalog
        self._orders = orders
        self._graph = build_graph(gateway, guard)

    @property
    def graph(self) -> CompiledStateGraph:
        return self._graph

    def turn(self, session_id: str, text: str) -> Reply:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state = self._graph.invoke({"user_text": text}, config)
        last_message = state["messages"][-1]
        return Reply(
            text=str(last_message.text),
            sources=(),
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


def build_assistant(gateway: ModelGateway | None = None) -> Assistant:
    policies, catalog, orders = _load_kit(_resolve_kit_dir())
    selected_gateway = gateway if gateway is not None else build_gateway()
    return Assistant(
        selected_gateway,
        Guard(catalog),
        policies=policies,
        catalog=catalog,
        orders=orders,
    )
