from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool, tool

import pet_helper.conversation as conversation
from pet_helper.catalog import Catalog
from pet_helper.conversation import Assistant
from pet_helper.gateway import FakeGateway, OllamaGateway
from pet_helper.guard import Guard
from pet_helper.knowledge import PolicyIndex
from pet_helper.orders import OrderService
from pet_helper.orders.api import MockOrderApi

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
ALLOWED_BUILTIN_TOOLS = {
    "search_policies",
    "get_order",
    "search_catalog",
    "escalate_to_human",
}


@tool
def lookup_pickup_locker(postal_code: str) -> str:
    """Look up pickup locker availability for a postal code."""
    return f"Locker Paulista available for {postal_code}"


@pytest.fixture(scope="module")
def kit_services() -> tuple[PolicyIndex, Catalog, OrderService, Guard]:
    catalog = Catalog(KIT_DIR / "catalog.csv")
    return (
        PolicyIndex.load(KIT_DIR / "policies"),
        catalog,
        OrderService(MockOrderApi(KIT_DIR / "orders.json")),
        Guard(catalog),
    )


def test_it_should_include_only_read_only_builtin_tools_in_the_default_registry(
    kit_services,
):
    policies, catalog, orders, _ = kit_services

    registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    registered_names = {item.name for item in registry.tools}
    assert all(isinstance(item, BaseTool) for item in registry.tools)
    assert {"search_policies", "get_order", "search_catalog"} <= registered_names
    assert registered_names <= ALLOWED_BUILTIN_TOOLS


def test_it_should_add_a_dummy_tool_with_a_single_registration_call(kit_services):
    policies, catalog, orders, _ = kit_services
    registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    registry.register(lookup_pickup_locker)

    assert "lookup_pickup_locker" in [item.name for item in registry.tools]


def test_it_should_expose_a_registered_dummy_tool_to_the_gateway_model_binding(
    kit_services,
):
    policies, catalog, orders, guard = kit_services
    registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )
    registry.register(lookup_pickup_locker)
    bound_tool_sets: list[list[Any]] = []
    fake_chat_model: Any = SimpleNamespace(
        model="fake-local",
        bind_tools=lambda tools: (
            bound_tool_sets.append(list(tools)),
            RunnableLambda(lambda _messages: AIMessage("Armário disponível.")),
        )[1],
    )
    gateway = OllamaGateway(chat_model=fake_chat_model)
    assistant = Assistant(
        gateway,
        guard,
        policies=policies,
        catalog=catalog,
        orders=orders,
        registry=registry,
    )

    assistant.turn("session-bind-dummy", "Tem armário no CEP 01310-100?")

    assert len(bound_tool_sets) == 1
    bound_names = {item.name for item in bound_tool_sets[0]}
    assert {
        "search_policies",
        "get_order",
        "search_catalog",
        "lookup_pickup_locker",
    } <= bound_names


def test_it_should_deliver_a_registered_dummy_tool_output_to_the_next_model_call(
    kit_services,
):
    policies, catalog, orders, guard = kit_services
    registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )
    registry.register(lookup_pickup_locker)
    gateway = FakeGateway(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup_pickup_locker",
                        "args": {"postal_code": "01310-100"},
                        "id": "call_locker_1",
                    }
                ],
            ),
            "O armário Paulista atende o CEP 01310-100.",
        ]
    )
    assistant = Assistant(
        gateway,
        guard,
        policies=policies,
        catalog=catalog,
        orders=orders,
        registry=registry,
    )

    reply = assistant.turn("session-run-dummy", "Tem armário no CEP 01310-100?")

    tool_messages = [
        message for message in gateway.calls[1] if isinstance(message, ToolMessage)
    ]
    assert reply.text == "O armário Paulista atende o CEP 01310-100."
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "lookup_pickup_locker"
    assert "Locker Paulista available for 01310-100" in str(tool_messages[0].content)


def test_it_should_accept_a_custom_registry_in_build_assistant(monkeypatch):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(KIT_DIR))
    registry = conversation.ToolRegistry()
    registry.register(lookup_pickup_locker)
    gateway = FakeGateway(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup_pickup_locker",
                        "args": {"postal_code": "22041-001"},
                        "id": "call_locker_2",
                    }
                ],
            ),
            "Armário confirmado para 22041-001.",
        ]
    )
    assistant = conversation.build_assistant(gateway=gateway, registry=registry)

    reply = assistant.turn("session-build-assistant-registry", "Consulte 22041-001")

    assert reply.text == "Armário confirmado para 22041-001."


def test_it_should_keep_separate_tool_registry_instances_isolated(kit_services):
    policies, catalog, orders, _ = kit_services
    first_registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )
    second_registry = conversation.build_tool_registry(
        policies=policies,
        catalog=catalog,
        orders=orders,
    )

    first_registry.register(lookup_pickup_locker)

    assert "lookup_pickup_locker" in {item.name for item in first_registry.tools}
    assert "lookup_pickup_locker" not in {item.name for item in second_registry.tools}
