import json
from datetime import date
from decimal import Decimal

import pytest

from pet_helper.orders import (
    Clock,
    Cpf,
    Found,
    NeedsIdentification,
    NotFound,
    OrderService,
    OrderView,
    Unavailable,
)
from pet_helper.orders.api import FAILURE_ENV, ApiResponse, MockOrderApi

OWNER_CPF = Cpf("00000000191")
OTHER_CPF = Cpf("00000000272")
TODAY_ENV = "PET_HELPER_TODAY"


class _FakeOrderApi:
    def __init__(self, outcomes: list[ApiResponse | Exception]):
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def get(self, order_number: str, customer_cpf: str) -> ApiResponse:
        self.calls.append((order_number, customer_cpf))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FixedClock:
    def __init__(self, current: date):
        self._current = current

    def today(self) -> date:
        return self._current


def _order_body(**extra) -> dict:
    return {
        "number": "PED-10001",
        "customer_cpf": OWNER_CPF,
        "status": "Entregue",
        "delivered_on": "2026-10-31",
        "region": "Sul",
        "state": "SC",
        "total": "159.80",
        "discount": "10.00",
        "payment_method": "Cartão",
        "items": [
            {
                "sku": "PX-0037",
                "quantity": 2,
                "unit_price": "79.90",
                "category": "acessório",
                "opened": True,
                "warehouse_bin": "B-12",
            }
        ],
        **extra,
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(FAILURE_ENV, raising=False)
    monkeypatch.delenv(TODAY_ENV, raising=False)


@pytest.fixture
def mock_api(tmp_path) -> MockOrderApi:
    book = {
        "reference_date": "2026-11-03",
        "customers": [
            {"cpf": OWNER_CPF, "name": "Ana Souza", "email": "ana@example.com"},
            {"cpf": OTHER_CPF, "name": "Bruno Lima", "email": "bruno@example.com"},
        ],
        "orders": [
            _order_body(),
            _order_body(
                number="PED-10002",
                status="Em separação",
                delivered_on=None,
                payment_method="Pix",
            ),
        ],
    }
    orders_path = tmp_path / "orders.json"
    orders_path.write_text(json.dumps(book), encoding="utf-8")
    return MockOrderApi(orders_path)


def test_it_should_return_needs_identification_without_calling_the_port_when_cpf_is_none():
    api = _FakeOrderApi([])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(None, "PED-10001")

    assert result == NeedsIdentification()
    assert api.calls == []


def test_it_should_expose_only_the_allowlisted_fields_on_a_found_order_view():
    planted_body = _order_body(
        customer_name="Ana Souza",
        address="Rua das Acácias, 123, Florianópolis",
        email="ana@example.com",
    )
    api = _FakeOrderApi([ApiResponse(200, planted_body)])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-10001")

    assert isinstance(result, Found)
    assert isinstance(result.order, OrderView)
    assert result.order.model_dump() == {
        "number": "PED-10001",
        "status": "Entregue",
        "items": [
            {
                "sku": "PX-0037",
                "quantity": 2,
                "category": "acessório",
                "opened": True,
            }
        ],
        "total": Decimal("159.80"),
        "region": "Sul",
        "payment_method": "Cartão",
        "delivered_on": date(2026, 10, 31),
        "days_since_delivery": 3,
    }


def test_it_should_return_a_found_order_for_the_owner_over_the_mock_order_api(mock_api):
    service = OrderService(mock_api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-10001")

    assert isinstance(result, Found)
    assert result.order.number == "PED-10001"


def test_it_should_return_indistinguishable_not_found_values_for_404_and_403(mock_api):
    service = OrderService(mock_api, clock=_FixedClock(date(2026, 11, 3)))

    missing = service.lookup(OWNER_CPF, "PED-99999")
    forbidden = service.lookup(OTHER_CPF, "PED-10001")

    assert missing == NotFound()
    assert forbidden == NotFound()
    assert missing == forbidden
    assert vars(missing) == {}
    assert vars(forbidden) == {}


@pytest.mark.parametrize(
    ("cpf", "order_number", "expected_reason"),
    [
        (OWNER_CPF, "PED-99999", "not_found"),
        (OTHER_CPF, "PED-10001", "other_customer"),
    ],
)
def test_it_should_report_the_internal_not_found_reason_to_the_trace_hook(
    mock_api, cpf, order_number, expected_reason
):
    reasons: list[str] = []
    service = OrderService(
        mock_api,
        clock=_FixedClock(date(2026, 11, 3)),
        trace_hook=reasons.append,
    )

    result = service.lookup(cpf, order_number)

    assert result == NotFound()
    assert reasons == [expected_reason]


def test_it_should_recover_from_a_single_timeout_by_retrying_once():
    api = _FakeOrderApi([TimeoutError("PED-10001"), ApiResponse(200, _order_body())])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-10001")

    assert isinstance(result, Found)
    assert len(api.calls) == 2


def test_it_should_return_unavailable_after_two_consecutive_timeouts():
    api = _FakeOrderApi([TimeoutError("PED-40800"), TimeoutError("PED-40800")])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-40800")

    assert result == Unavailable()
    assert len(api.calls) == 2


def test_it_should_return_unavailable_on_503_without_retrying():
    api = _FakeOrderApi([ApiResponse(503, {"error": "unavailable"})])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-50300")

    assert result == Unavailable()
    assert len(api.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 429, 302])
def test_it_should_return_unavailable_on_an_unexpected_non_200_status(status):
    api = _FakeOrderApi([ApiResponse(status, _order_body())])
    service = OrderService(api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-10001")

    assert result == Unavailable()


@pytest.mark.parametrize("order_number", ["PED-50300", "PED-40800"])
def test_it_should_map_mock_api_failures_to_unavailable(mock_api, order_number):
    service = OrderService(mock_api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, order_number)

    assert result == Unavailable()


def test_it_should_read_the_system_date_from_clock_when_today_env_is_unset():
    clock = Clock()

    today = clock.today()

    assert today == date.today()


def test_it_should_override_the_system_date_from_pet_helper_today(
    mock_api, monkeypatch
):
    monkeypatch.setenv(TODAY_ENV, "2026-11-12")
    service = OrderService(mock_api)

    result = service.lookup(OWNER_CPF, "PED-10001")

    assert isinstance(result, Found)
    assert result.order.days_since_delivery == 12


def test_it_should_leave_days_since_delivery_none_when_order_has_no_delivery_date(
    mock_api,
):
    service = OrderService(mock_api, clock=_FixedClock(date(2026, 11, 3)))

    result = service.lookup(OWNER_CPF, "PED-10002")

    assert isinstance(result, Found)
    assert result.order.delivered_on is None
    assert result.order.days_since_delivery is None
