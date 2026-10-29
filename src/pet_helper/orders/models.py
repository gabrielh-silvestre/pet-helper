import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# the SKU pattern is PX-nnnn, so the two never match each other
ORDER_NUMBER_PATTERN = r"PED-\d{5}"


class Customer(BaseModel):
    model_config = ConfigDict(extra="allow")

    cpf: str
    name: str
    email: str


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    sku: str
    quantity: int
    unit_price: Decimal
    category: str
    opened: bool = False


class Order(BaseModel):
    model_config = ConfigDict(extra="allow")

    number: str
    customer_cpf: str
    status: str
    delivered_on: date | None = None
    region: str
    state: str
    total: Decimal
    discount: Decimal = Decimal("0")
    payment_method: str
    items: list[OrderItem]


class OrderBook(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference_date: date
    customers: list[Customer]
    orders: list[Order]


def load_orders(path: Path) -> OrderBook:
    return OrderBook.model_validate(json.loads(path.read_text(encoding="utf-8")))
