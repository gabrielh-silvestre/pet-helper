import dataclasses
import json
from decimal import Decimal

import pytest

from pet_helper.orders.api import FAILURE_ENV, ApiResponse, MockOrderApi, OrderApi

OWNER_CPF = "00000000191"
OWNER_CPF_FORMATTED = "000.000.001-91"
OTHER_CPF = "00000000272"
# built from code points because RUF001 rejects non-ASCII digit literals
FULL_WIDTH_OWNER_CPF = "".join(chr(ord(d) + 0xFEE0) for d in OWNER_CPF)
OTHER_CPF_FORMATTED = "000.000.002-72"
REFERENCE_DATE = "2026-11-03"
STATUSES = ["Pago", "Em separação", "Enviado", "Entregue", "Cancelado"]


def _order(number, customer_cpf, status, **extra):
    return {
        "number": number,
        "customer_cpf": customer_cpf,
        "status": status,
        "delivered_on": "2026-10-30" if status == "Entregue" else None,
        "region": "Sul",
        "state": "SC",
        "total": 99.9,
        "payment_method": "Pix",
        "items": [
            {
                "sku": "PX-0001",
                "quantity": 1,
                "unit_price": 99.9,
                "category": "brinquedo",
                "opened": False,
                "item_note": "kept",
            }
        ],
        **extra,
    }


def _write_orders(path, orders):
    customers = [
        {"cpf": OWNER_CPF, "name": "Ana Souza", "email": "ana@example.com"},
        {"cpf": OTHER_CPF, "name": "Bruno Lima", "email": "bruno@example.com"},
    ]
    path.write_text(
        json.dumps(
            {
                "reference_date": REFERENCE_DATE,
                "customers": customers,
                "orders": orders,
            }
        ),
        encoding="utf-8",
    )
    return path


def _status_number(index):
    return f"PED-2000{index}"


@pytest.fixture(autouse=True)
def _no_ambient_failure(monkeypatch):
    monkeypatch.delenv(FAILURE_ENV, raising=False)


@pytest.fixture
def api(tmp_path):
    orders = [
        _order("PED-10001", OWNER_CPF, "Entregue", gift_note="wrap it", discount=12.5)
    ]
    orders += [
        _order(_status_number(i), OWNER_CPF, status)
        for i, status in enumerate(STATUSES)
    ]
    return MockOrderApi(_write_orders(tmp_path / "orders.json", orders))


def test_it_should_return_200_for_the_owner_with_a_formatted_cpf(api):
    response = api.get("PED-10001", OWNER_CPF_FORMATTED)

    assert response.status == 200


def test_it_should_return_200_for_the_owner_with_a_digits_only_cpf(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert response.status == 200


def test_it_should_return_the_requested_order_as_the_body(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert response.body["number"] == "PED-10001"


def test_it_should_preserve_extra_order_fields_in_the_body(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert response.body["gift_note"] == "wrap it"


def test_it_should_preserve_extra_item_fields_in_the_body(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert response.body["items"][0]["item_note"] == "kept"


def test_it_should_return_the_order_discount_in_the_body(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert Decimal(response.body["discount"]) == Decimal("12.5")


def test_it_should_return_a_zero_discount_for_an_order_without_one(api):
    response = api.get(_status_number(0), OWNER_CPF)

    assert Decimal(response.body["discount"]) == Decimal("0")


def test_it_should_not_expose_the_reference_date_in_the_body(api):
    response = api.get("PED-10001", OWNER_CPF)

    assert "reference_date" not in response.body


@pytest.mark.parametrize("index, status", list(enumerate(STATUSES)))
def test_it_should_return_each_order_status_in_a_200_body(api, index, status):
    response = api.get(_status_number(index), OWNER_CPF)

    assert (response.status, response.body["status"]) == (200, status)


def test_it_should_return_404_when_the_order_number_does_not_exist(api):
    response = api.get("PED-99999", OWNER_CPF)

    assert response == ApiResponse(404, {"error": "not_found"})


def test_it_should_return_403_when_the_order_belongs_to_another_customer(api):
    response = api.get("PED-10001", OTHER_CPF)

    assert response == ApiResponse(403, {"error": "forbidden"})


def test_it_should_return_403_for_the_owner_cpf_with_trailing_characters(api):
    response = api.get("PED-10001", OWNER_CPF + "abc")

    assert response == ApiResponse(403, {"error": "forbidden"})


def test_it_should_return_403_for_the_owner_cpf_written_with_full_width_digits(api):
    response = api.get("PED-10001", FULL_WIDTH_OWNER_CPF)

    assert response == ApiResponse(403, {"error": "forbidden"})


def test_it_should_return_403_for_another_customer_with_a_formatted_cpf(api):
    response = api.get("PED-10001", OTHER_CPF_FORMATTED)

    assert response.status == 403


@pytest.mark.parametrize("cpf", [OWNER_CPF, OTHER_CPF])
def test_it_should_return_503_for_the_fixed_unavailable_order(api, cpf):
    response = api.get("PED-50300", cpf)

    assert response == ApiResponse(503, {"error": "unavailable"})


@pytest.mark.parametrize("cpf", [OWNER_CPF, OTHER_CPF])
def test_it_should_raise_timeout_for_the_fixed_timeout_order(api, cpf):
    with pytest.raises(TimeoutError):
        api.get("PED-40800", cpf)


def test_it_should_fail_every_call_with_503_when_the_env_var_is_503(api, monkeypatch):
    monkeypatch.setenv(FAILURE_ENV, "503")

    response = api.get("PED-10001", OWNER_CPF)

    assert response == ApiResponse(503, {"error": "unavailable"})


def test_it_should_fail_every_call_with_timeout_when_the_env_var_is_timeout(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "timeout")

    with pytest.raises(TimeoutError):
        api.get("PED-10001", OWNER_CPF)


def test_it_should_fail_a_missing_order_when_the_env_var_is_set(api, monkeypatch):
    monkeypatch.setenv(FAILURE_ENV, "503")

    response = api.get("PED-99999", OWNER_CPF)

    assert response.status == 503


def test_it_should_let_the_env_var_win_over_the_fixed_unavailable_order(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "timeout")

    with pytest.raises(TimeoutError):
        api.get("PED-50300", OWNER_CPF)


def test_it_should_let_the_env_var_win_over_the_fixed_timeout_order(api, monkeypatch):
    monkeypatch.setenv(FAILURE_ENV, "503")

    response = api.get("PED-40800", OWNER_CPF)

    assert response.status == 503


def test_it_should_read_the_env_var_on_each_call(api, monkeypatch):
    monkeypatch.setenv(FAILURE_ENV, "503")
    api.get("PED-10001", OWNER_CPF)
    monkeypatch.delenv(FAILURE_ENV)

    response = api.get("PED-10001", OWNER_CPF)

    assert response.status == 200


def test_it_should_return_200_for_an_existing_order_when_the_env_var_is_empty(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "")

    response = api.get("PED-10001", OWNER_CPF)

    assert response.status == 200


def test_it_should_return_404_for_a_missing_order_when_the_env_var_is_empty(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "")

    response = api.get("PED-99999", OWNER_CPF)

    assert response == ApiResponse(404, {"error": "not_found"})


def test_it_should_keep_the_fixed_unavailable_order_failing_when_the_env_var_is_empty(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "")

    response = api.get("PED-50300", OWNER_CPF)

    assert response == ApiResponse(503, {"error": "unavailable"})


def test_it_should_raise_value_error_when_the_env_var_has_an_unknown_value(
    api, monkeypatch
):
    monkeypatch.setenv(FAILURE_ENV, "flaky")

    with pytest.raises(ValueError):
        api.get("PED-10001", OWNER_CPF)


def test_it_should_not_allow_changing_an_api_response():
    response = ApiResponse(200, {"number": "PED-10001"})

    with pytest.raises(dataclasses.FrozenInstanceError):
        response.status = 500


def test_it_should_satisfy_the_order_api_protocol(api):
    order_api: OrderApi = api

    response = order_api.get("PED-10001", OWNER_CPF)

    assert isinstance(response, ApiResponse)
