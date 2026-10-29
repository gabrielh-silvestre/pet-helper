import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pet_helper.orders.cpf import normalize_cpf
from pet_helper.orders.models import load_orders

FAILURE_ENV = "PET_HELPER_ORDER_API_FAILURE"
FAILURE_503_ORDER = "PED-50300"
FAILURE_TIMEOUT_ORDER = "PED-40800"


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict | None


class OrderApi(Protocol):
    def get(self, order_number: str, customer_cpf: str) -> ApiResponse: ...


class MockOrderApi:
    def __init__(self, orders_path: Path):
        self._orders = {o.number: o for o in load_orders(orders_path).orders}

    def get(self, order_number: str, customer_cpf: str) -> ApiResponse:
        # read on each call so a test or an evaluator can flip the failure live
        failure = os.environ.get(FAILURE_ENV) or None
        if failure is None:
            failure = {FAILURE_503_ORDER: "503", FAILURE_TIMEOUT_ORDER: "timeout"}.get(
                order_number
            )
        if failure == "503":
            return ApiResponse(503, {"error": "unavailable"})
        if failure == "timeout":
            raise TimeoutError(order_number)
        if failure is not None:
            raise ValueError(f"unknown {FAILURE_ENV} value: {failure!r}")

        order = self._orders.get(order_number)
        if order is None:
            return ApiResponse(404, {"error": "not_found"})
        if normalize_cpf(customer_cpf) != normalize_cpf(order.customer_cpf):
            return ApiResponse(403, {"error": "forbidden"})
        return ApiResponse(200, order.model_dump(mode="json"))
