import json
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import Field

from pet_helper.catalog import Catalog
from pet_helper.catalog.models import Product
from pet_helper.conversation.state import EscalationReason
from pet_helper.knowledge import PolicyHit, PolicyIndex
from pet_helper.orders import (
    Cpf,
    Found,
    NeedsIdentification,
    NotFound,
    OrderLookupResult,
    OrderService,
    Unavailable,
)
from pet_helper.orders.models import ORDER_NUMBER_PATTERN

_PRODUCT_FIELDS = {
    "sku",
    "name",
    "category",
    "species",
    "price",
    "in_stock",
    "description",
}
_ORDER_STATUS_BY_RESULT: dict[type, str] = {
    NotFound: "not_found",
    NeedsIdentification: "needs_identification",
    Unavailable: "unavailable",
}


def _to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: list[BaseTool] = []

    @property
    def tools(self) -> tuple[BaseTool, ...]:
        return tuple(self._tools)

    def register(self, new_tool: BaseTool) -> None:
        self._tools.append(new_tool)


def _make_search_policies(policies: PolicyIndex) -> BaseTool:
    @tool(response_format="content_and_artifact")
    def search_policies(query: str) -> tuple[str, list[PolicyHit]]:
        """Search the store policies (shipping, returns, refunds, cancellation, ...).

        Returns up to 2 documents with title and text, or found=false when no
        policy matches. Answer only from the returned text and cite the title.
        """
        hits = policies.search(query)
        documents = [{"title": hit.title, "text": hit.text} for hit in hits]
        return _to_json({"found": bool(hits), "documents": documents}), hits

    return search_policies


def _make_get_order(orders: OrderService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    def get_order(
        order_number: Annotated[str, Field(pattern=ORDER_NUMBER_PATTERN)],
        session_cpf: Annotated[str | None, InjectedState("session_cpf")] = None,
    ) -> tuple[str, OrderLookupResult]:
        """Look up the order of the identified customer by its number (PED-00000).

        The customer is identified by the backend; never ask for or pass a CPF
        here. Returns status found, not_found, needs_identification or unavailable.
        """
        result = orders.lookup(Cpf(session_cpf) if session_cpf else None, order_number)
        if isinstance(result, Found):
            payload = {"status": "found", "order": result.order.model_dump(mode="json")}
        else:
            payload = {"status": _ORDER_STATUS_BY_RESULT[type(result)]}
        return _to_json(payload), result

    return get_order


def _make_search_catalog(catalog: Catalog) -> BaseTool:
    @tool(response_format="content_and_artifact")
    def search_catalog(
        query: str,
        species: str | None = None,
        category: str | None = None,
    ) -> tuple[str, list[Product]]:
        """Search the product catalog by name, optionally filtered by species and category.

        Returns SKU, name, category, species, price and whether it is in stock.
        """
        products = catalog.search(query, species=species, category=category)
        listed = [
            product.model_dump(mode="json", include=_PRODUCT_FIELDS)
            for product in products
        ]
        return _to_json({"products": listed}), products

    return search_catalog


def _make_escalate_to_human() -> BaseTool:
    @tool(response_format="content_and_artifact")
    def escalate_to_human(
        reason: Literal[
            "animal_health_question", "strong_negative_sentiment", "explicit_request"
        ],
    ) -> tuple[str, EscalationReason]:
        """Hand the conversation over to a human agent; the conversation ends.

        Call it for a question about a pet's health (animal_health_question),
        a customer who is strongly upset (strong_negative_sentiment) or one who
        asks for a person (explicit_request). The backend writes the reply to
        the customer, so do not write one.
        """
        return _to_json({"status": "escalated"}), reason

    return escalate_to_human


def build_tool_registry(
    *,
    policies: PolicyIndex,
    catalog: Catalog,
    orders: OrderService,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_make_search_policies(policies))
    registry.register(_make_get_order(orders))
    registry.register(_make_search_catalog(catalog))
    registry.register(_make_escalate_to_human())
    return registry
