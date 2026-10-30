import json
import re
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode

import pet_helper.conversation as conversation
from pet_helper.catalog import Catalog
from pet_helper.catalog.models import Product
from pet_helper.knowledge import PolicyHit, PolicyIndex
from pet_helper.orders import (
    Found,
    NeedsIdentification,
    NotFound,
    OrderService,
    Unavailable,
)
from pet_helper.orders.api import FAILURE_ENV, ApiResponse, MockOrderApi

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
OWNER_CPF = "00000000191"
OTHER_CPF = "00000000272"


class _ToolGraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    session_cpf: str | None


@pytest.fixture(autouse=True)
def _pin_environment(monkeypatch):
    monkeypatch.setenv("PET_HELPER_TODAY", "2026-11-03")
    monkeypatch.delenv(FAILURE_ENV, raising=False)


@pytest.fixture(scope="module")
def kit_services() -> tuple[PolicyIndex, Catalog, OrderService]:
    return (
        PolicyIndex.load(KIT_DIR / "policies"),
        Catalog(KIT_DIR / "catalog.csv"),
        OrderService(MockOrderApi(KIT_DIR / "orders.json")),
    )


def _get_tool(
    name: str,
    *,
    policies: PolicyIndex,
    catalog: Catalog,
    orders: OrderService,
) -> BaseTool:
    registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )
    matching = [item for item in registry.tools if item.name == name]
    assert len(matching) == 1, f"Expected tool {name!r} in registry"
    return matching[0]


def _invoke_in_tool_node(
    target_tool: BaseTool,
    args: dict[str, Any],
    *,
    session_cpf: str | None = None,
) -> ToolMessage:
    builder = StateGraph(_ToolGraphState)
    builder.add_node("tools", ToolNode([target_tool], handle_tool_errors=True))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    state = builder.compile().invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": target_tool.name, "args": args, "id": "call_1"}
                    ],
                )
            ],
            "session_cpf": session_cpf,
        }
    )
    last_message = state["messages"][-1]
    assert isinstance(last_message, ToolMessage)
    return last_message


@pytest.mark.parametrize(
    "tool_name",
    ["search_policies", "get_order", "search_catalog"],
)
def test_it_should_configure_dual_content_artifact_response_format_on_every_data_tool(
    kit_services, tool_name
):
    policies, catalog, orders = kit_services

    target_tool = _get_tool(
        tool_name,
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    assert isinstance(target_tool, BaseTool)
    assert target_tool.response_format == "content_and_artifact"


def test_it_should_return_matching_policy_content_with_hit_artifacts_from_search_policies(
    kit_services,
):
    policies, catalog, orders = kit_services
    search_policies = _get_tool(
        "search_policies",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        search_policies,
        {"query": "devolucao arrependimento"},
    )

    content = str(message.content)
    assert message.status == "success"
    assert "Troca e devolução" in content
    assert "7 dias corridos" in content
    assert isinstance(message.artifact, Sequence)
    assert not isinstance(message.artifact, (str, bytes))
    assert all(isinstance(hit, PolicyHit) for hit in message.artifact)
    assert "troca-e-devolucao.md" in [hit.source for hit in message.artifact]


def test_it_should_return_found_false_when_search_policies_matches_no_document(
    kit_services,
):
    policies, catalog, orders = kit_services
    search_policies = _get_tool(
        "search_policies",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        search_policies,
        {"query": "garantia estendida"},
    )

    assert message.status == "success"
    assert (
        re.search(r'"?found"?\s*:\s*false\b', str(message.content), re.IGNORECASE)
        is not None
    )
    assert list(message.artifact) == []


def test_it_should_exclude_cpf_from_the_model_facing_schema_of_get_order(
    kit_services,
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    parameters = convert_to_openai_tool(get_order)["function"]["parameters"]

    assert set(parameters["properties"]) == {"order_number"}
    assert set(get_order.tool_call_schema.model_fields) == {"order_number"}


def test_it_should_declare_session_cpf_via_injected_state_on_get_order(
    kit_services,
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    injected_fields = [
        meta.field
        for field in get_order.get_input_schema().model_fields.values()
        for meta in field.metadata
        if isinstance(meta, InjectedState)
    ]

    assert injected_fields == ["session_cpf"]


def test_it_should_ignore_a_model_supplied_cpf_argument_in_favor_of_session_cpf(
    kit_services,
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        get_order,
        {"order_number": "PED-10003", "cpf": OTHER_CPF},
        session_cpf=OWNER_CPF,
    )

    assert message.artifact == NotFound()
    assert "PX-0001" not in str(message.content)
    assert "94.80" not in str(message.content)


def test_it_should_return_needs_identification_from_get_order_without_session_cpf(
    kit_services,
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        get_order,
        {"order_number": "PED-10001", "cpf": OWNER_CPF},
        session_cpf=None,
    )

    assert message.status == "success"
    assert message.artifact == NeedsIdentification()
    assert "PX-0037" not in str(message.content)


def test_it_should_return_indistinguishable_not_found_results_for_404_versus_other_customer(
    kit_services, tmp_path
):
    policies, catalog, kit_orders = kit_services
    empty_orders_path = tmp_path / "orders.json"
    empty_orders_path.write_text(
        json.dumps(
            {
                "reference_date": "2026-11-03",
                "customers": [
                    {
                        "cpf": OWNER_CPF,
                        "name": "Cliente Um",
                        "email": "c1@clientes.example.com",
                    }
                ],
                "orders": [],
            }
        ),
        encoding="utf-8",
    )
    missing_order_tool = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=OrderService(MockOrderApi(empty_orders_path)),
    )
    other_customer_tool = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=kit_orders,
    )

    missing_message = _invoke_in_tool_node(
        missing_order_tool,
        {"order_number": "PED-10003"},
        session_cpf=OWNER_CPF,
    )
    other_customer_message = _invoke_in_tool_node(
        other_customer_tool,
        {"order_number": "PED-10003"},
        session_cpf=OWNER_CPF,
    )

    assert missing_message.artifact == NotFound()
    assert other_customer_message.artifact == NotFound()
    assert other_customer_message.content == missing_message.content


def test_it_should_expose_only_allowlisted_order_view_fields_in_get_order_content(
    kit_services,
):
    policies, catalog, _ = kit_services
    planted_body = {
        "number": "PED-10001",
        "customer_cpf": OWNER_CPF,
        "customer_name": "Tobias Pimpão Fictício",
        "email": "tobias.pimpao@clientes.example.com",
        "address": "Rua das Flores, 404",
        "status": "Entregue",
        "delivered_on": "2026-10-31",
        "region": "Sul",
        "state": "SC",
        "total": "159.80",
        "discount": "15.00",
        "payment_method": "Cartão",
        "items": [
            {
                "sku": "PX-0037",
                "quantity": 1,
                "unit_price": "89.90",
                "category": "acessório",
                "opened": False,
                "warehouse_bin": "CORREDOR-9",
            }
        ],
    }
    planted_api: Any = SimpleNamespace(
        get=lambda _order_number, _cpf: ApiResponse(200, planted_body)
    )
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=OrderService(planted_api),
    )

    message = _invoke_in_tool_node(
        get_order,
        {"order_number": "PED-10001"},
        session_cpf=OWNER_CPF,
    )

    content = str(message.content)
    assert isinstance(message.artifact, Found)
    assert "PED-10001" in content
    assert "Entregue" in content
    assert "PX-0037" in content
    assert "159.80" in content
    assert "Sul" in content
    assert "Cartão" in content
    assert "2026-10-31" in content
    assert OWNER_CPF not in content
    assert "Tobias Pimpão Fictício" not in content
    assert "tobias.pimpao@clientes.example.com" not in content
    assert "Rua das Flores" not in content
    assert "CORREDOR-9" not in content
    assert "89.90" not in content


@pytest.mark.parametrize("order_number", ["PED-50300", "PED-40800"])
def test_it_should_return_unavailable_from_get_order_for_simulated_api_failures(
    kit_services, order_number
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        get_order,
        {"order_number": order_number},
        session_cpf=OWNER_CPF,
    )

    assert message.status == "success"
    assert message.artifact == Unavailable()


def test_it_should_return_matching_products_with_product_artifacts_from_search_catalog(
    kit_services,
):
    policies, catalog, orders = kit_services
    search_catalog = _get_tool(
        "search_catalog",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        search_catalog,
        {"query": "Queria a ração Bigodin de salmão para castrados, pode me passar?"},
    )

    content = str(message.content)
    assert message.status == "success"
    assert "PX-0001" in content
    assert "PX-0002" in content
    assert isinstance(message.artifact, Sequence)
    assert not isinstance(message.artifact, (str, bytes))
    assert all(isinstance(product, Product) for product in message.artifact)
    assert {product.sku for product in message.artifact[:2]} == {"PX-0001", "PX-0002"}


def test_it_should_apply_combined_species_with_category_filters_in_search_catalog(
    kit_services,
):
    policies, catalog, orders = kit_services
    search_catalog = _get_tool(
        "search_catalog",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        search_catalog,
        {"query": "Bigodin", "species": "cão", "category": "acessório"},
    )

    content = str(message.content)
    assert {"query", "species", "category"} <= set(
        search_catalog.tool_call_schema.model_fields
    )
    assert [product.sku for product in message.artifact] == ["PX-0043"]
    assert "PX-0043" in content
    assert "PX-0001" not in content


def test_it_should_surface_out_of_stock_status_in_search_catalog_output(
    kit_services,
):
    policies, catalog, orders = kit_services
    search_catalog = _get_tool(
        "search_catalog",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        search_catalog,
        {"query": "Ração Focinhex Adultos Carne 15 kg"},
    )

    lowered = str(message.content).lower()
    assert message.artifact[0].sku == "PX-0005"
    assert message.artifact[0].in_stock is False
    assert "PX-0005" in str(message.content)
    assert any(
        marker in lowered
        for marker in ("false", "sem estoque", "esgotad", '"stock": 0', "stock=0")
    )


def _enum_values(property_schema: dict[str, Any]) -> set[str]:
    branches = [property_schema, *property_schema.get("anyOf", [])]
    return {value for branch in branches for value in branch.get("enum", [])}


@pytest.mark.parametrize(
    ("field", "expected_values"),
    [
        ("species", {"cão", "gato"}),
        ("category", {"ração", "areia", "brinquedo", "acessório"}),
    ],
)
def test_it_should_expose_the_catalog_filter_values_as_an_enum_in_the_search_catalog_schema(
    kit_services, field, expected_values
):
    policies, catalog, orders = kit_services
    search_catalog = _get_tool(
        "search_catalog",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    properties = convert_to_openai_tool(search_catalog)["function"]["parameters"][
        "properties"
    ]

    assert _enum_values(properties[field]) == expected_values


@pytest.mark.parametrize(
    "filters",
    [{"species": "cachorro"}, {"category": "racao"}],
    ids=["species", "category"],
)
def test_it_should_return_a_validation_error_for_a_catalog_filter_outside_the_enum(
    kit_services, filters
):
    policies, catalog, orders = kit_services
    search_catalog = _get_tool(
        "search_catalog",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(search_catalog, {"query": "ração", **filters})

    assert message.status == "error"


@pytest.mark.parametrize("order_number", ["xPED-10001", "PED-10001y", "xPED-10001y"])
def test_it_should_reject_an_order_number_with_extra_characters_around_a_valid_one(
    kit_services, order_number
):
    policies, catalog, orders = kit_services
    get_order = _get_tool(
        "get_order",
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    message = _invoke_in_tool_node(
        get_order, {"order_number": order_number}, session_cpf=OWNER_CPF
    )

    assert message.status == "error"
