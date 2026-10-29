import json
import re
from datetime import date
from decimal import Decimal

import pytest
from pet_helper.catalog.models import SKU_PATTERN
from pet_helper.orders.models import (
    ORDER_NUMBER_PATTERN,
    Customer,
    Order,
    OrderBook,
    OrderItem,
    load_orders,
)


def build_book() -> dict:
    return {
        "reference_date": "2026-11-03",
        "customers": [
            {"cpf": "52998224725", "name": "Ana Souza", "email": "ana@example.com"},
        ],
        "orders": [
            {
                "number": "PED-10001",
                "customer_cpf": "52998224725",
                "status": "Entregue",
                "delivered_on": "2026-10-31",
                "region": "Sul",
                "state": "SC",
                "total": 189.9,
                "payment_method": "Cartão",
                "items": [
                    {
                        "sku": "PX-0042",
                        "quantity": 2,
                        "unit_price": 94.95,
                        "category": "brinquedo",
                    }
                ],
            }
        ],
    }


def write_book(tmp_path, book: dict):
    path = tmp_path / "orders.json"
    path.write_text(json.dumps(book), encoding="utf-8")
    return path


@pytest.fixture
def book_path(tmp_path):
    return write_book(tmp_path, build_book())


def test_it_should_load_an_order_book_from_a_json_file(book_path):
    book = load_orders(book_path)

    assert isinstance(book, OrderBook)
    assert [type(o) for o in book.orders] == [Order]


def test_it_should_parse_the_reference_date_as_a_date(book_path):
    book = load_orders(book_path)

    assert book.reference_date == date(2026, 11, 3)


def test_it_should_load_the_customers_of_the_book(book_path):
    book = load_orders(book_path)

    assert book.customers == [
        Customer(cpf="52998224725", name="Ana Souza", email="ana@example.com")
    ]


def test_it_should_parse_delivered_on_as_a_date(book_path):
    order = load_orders(book_path).orders[0]

    assert order.delivered_on == date(2026, 10, 31)


def test_it_should_accept_an_order_that_was_not_delivered(tmp_path):
    book = build_book()
    book["orders"][0]["status"] = "Pago"
    book["orders"][0]["delivered_on"] = None

    order = load_orders(write_book(tmp_path, book)).orders[0]

    assert order.delivered_on is None


def test_it_should_parse_money_as_decimal(book_path):
    order = load_orders(book_path).orders[0]

    assert order.total == Decimal("189.90")
    assert order.items[0].unit_price == Decimal("94.95")


def test_it_should_parse_the_item_quantity_as_int(book_path):
    item = load_orders(book_path).orders[0].items[0]

    assert item.quantity == 2
    assert isinstance(item.quantity, int)


def test_it_should_default_an_item_to_not_opened(book_path):
    item = load_orders(book_path).orders[0].items[0]

    assert item.opened is False


def test_it_should_read_the_opened_flag_of_an_item(tmp_path):
    book = build_book()
    book["orders"][0]["items"][0]["opened"] = True

    item = load_orders(write_book(tmp_path, book)).orders[0].items[0]

    assert item.opened is True


def test_it_should_keep_an_extra_order_field_in_model_dump(tmp_path):
    book = build_book()
    book["orders"][0]["gift_message"] = "happy birthday"

    order = load_orders(write_book(tmp_path, book)).orders[0]

    assert order.model_dump()["gift_message"] == "happy birthday"


def test_it_should_keep_an_extra_item_field_in_model_dump(tmp_path):
    book = build_book()
    book["orders"][0]["items"][0]["lot"] = "L-77"

    item = load_orders(write_book(tmp_path, book)).orders[0].items[0]

    assert item.model_dump()["lot"] == "L-77"


def test_it_should_not_validate_the_closed_vocabularies_of_the_kit(tmp_path):
    book = build_book()
    book["orders"][0]["status"] = "Extraviado"

    order = load_orders(write_book(tmp_path, book)).orders[0]

    assert order.status == "Extraviado"


def test_it_should_build_an_order_item_with_the_documented_fields():
    item = OrderItem(
        sku="PX-0042", quantity=1, unit_price=Decimal("10.00"), category="areia"
    )

    assert item.opened is False


def test_it_should_match_an_order_number_with_the_order_number_pattern():
    assert re.fullmatch(ORDER_NUMBER_PATTERN, "PED-10001")


def test_it_should_not_match_a_sku_with_the_order_number_pattern():
    assert not re.search(ORDER_NUMBER_PATTERN, "PX-0042")


def test_it_should_not_match_an_order_number_with_the_sku_pattern():
    assert not re.search(SKU_PATTERN, "PED-10001")
