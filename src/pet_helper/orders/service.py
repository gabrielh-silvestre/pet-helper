import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, NewType, Protocol

from pydantic import BaseModel, ConfigDict

from pet_helper.orders.api import ApiResponse, OrderApi

Cpf = NewType("Cpf", str)

TODAY_ENV = "PET_HELPER_TODAY"

_NOT_FOUND_REASONS = {
    404: "not_found",
    403: "other_customer",
}


class ClockPort(Protocol):
    def today(self) -> date: ...


class Clock:
    def today(self) -> date:
        override = os.environ.get(TODAY_ENV)
        return date.fromisoformat(override) if override else date.today()


class OrderItemView(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sku: str
    quantity: int
    category: str
    opened: bool = False


class OrderView(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    number: str
    status: str
    items: list[OrderItemView]
    total: Decimal
    region: str
    payment_method: str
    delivered_on: date | None = None
    days_since_delivery: int | None = None


@dataclass(frozen=True)
class Found:
    order: OrderView


@dataclass(frozen=True)
class NotFound:
    pass


@dataclass(frozen=True)
class NeedsIdentification:
    pass


@dataclass(frozen=True)
class Unavailable:
    pass


type OrderLookupResult = Found | NotFound | NeedsIdentification | Unavailable


def _build_order_view(body: dict[str, Any], today: date) -> OrderView:
    parsed = OrderView.model_validate(body)
    days_since_delivery = (
        (today - parsed.delivered_on).days if parsed.delivered_on is not None else None
    )
    return parsed.model_copy(update={"days_since_delivery": days_since_delivery})


class OrderService:
    def __init__(
        self,
        api: OrderApi,
        clock: ClockPort | None = None,
        trace_hook: Callable[[str], None] | None = None,
    ):
        self._api = api
        self._clock: ClockPort = clock if clock is not None else Clock()
        self._trace_hook = trace_hook

    def _fetch_with_timeout_retry(
        self, order_number: str, customer: Cpf
    ) -> ApiResponse | None:
        for _ in range(2):
            try:
                return self._api.get(order_number, customer)
            except TimeoutError:
                continue
        return None

    def lookup(self, customer: Cpf | None, order_number: str) -> OrderLookupResult:
        if customer is None:
            return NeedsIdentification()

        response = self._fetch_with_timeout_retry(order_number, customer)
        if response is None or response.status >= 500 or response.body is None:
            return Unavailable()

        not_found_reason = _NOT_FOUND_REASONS.get(response.status)
        if not_found_reason is not None:
            if self._trace_hook is not None:
                self._trace_hook(not_found_reason)
            return NotFound()

        if response.status != 200:
            return Unavailable()

        return Found(_build_order_view(response.body, self._clock.today()))
