from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

REFERENCE_DATE = date(2026, 11, 3)

KIND_FAILURE = pytest.fail.Exception


def item(sku="PX-0001", category="brinquedo", opened=False, unit_price="50.00"):
    return SimpleNamespace(
        sku=sku,
        category=category,
        opened=opened,
        quantity=1,
        unit_price=Decimal(unit_price),
    )


def delivered(days_ago, items=None, **overrides):
    return build_order(
        status="Entregue",
        delivered_on=REFERENCE_DATE - timedelta(days=days_ago),
        items=items,
        **overrides,
    )


def build_order(items=None, **overrides):
    fields = {
        "number": "PED-10001",
        "status": "Pago",
        "delivered_on": None,
        "region": "Sul",
        "payment_method": "Pix",
        "total": Decimal("50.00"),
        "items": items if items is not None else [item()],
    }
    return SimpleNamespace(**{**fields, **overrides})


@pytest.fixture
def oracle(oracle_for):
    return oracle_for(REFERENCE_DATE)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [(0, "regret_allowed"), (7, "regret_allowed"), (8, "regret_denied")],
)
def test_it_should_allow_regret_up_to_seven_days_after_delivery(
    oracle, days_ago, expected
):
    order = delivered(days_ago)

    outcome = oracle("RN01", order, family="regret")

    assert outcome == expected


@pytest.mark.parametrize("category", ["ração", "areia"])
def test_it_should_deny_regret_for_a_cited_opened_food_or_litter_item(oracle, category):
    order = delivered(2, [item("PX-0002", category, opened=True)])

    outcome = oracle("RN01", order, sku="PX-0002", family="regret")

    assert outcome == "regret_denied"


def test_it_should_allow_regret_for_a_cited_item_that_was_not_opened(oracle):
    opened = item("PX-0002", "ração", opened=True)
    sealed = item("PX-0003", "brinquedo")
    order = delivered(2, [opened, sealed])

    outcome = oracle("RN01", order, sku="PX-0003", family="regret")

    assert outcome == "regret_allowed"


def test_it_should_deny_whole_order_regret_when_any_item_is_opened(oracle):
    order = delivered(2, [item("PX-0002", "areia", opened=True), item("PX-0003")])

    outcome = oracle("RN01", order, family="regret")

    assert outcome == "regret_denied"


def test_it_should_allow_whole_order_regret_when_nothing_is_opened(oracle):
    order = delivered(2, [item("PX-0002", "ração"), item("PX-0003")])

    outcome = oracle("RN01", order, family="regret")

    assert outcome == "regret_allowed"


@pytest.mark.parametrize(
    ("days_ago", "expected"), [(12, "defect_allowed"), (30, "defect_allowed")]
)
def test_it_should_allow_a_defect_claim_up_to_thirty_days_after_delivery(
    oracle, days_ago, expected
):
    order = delivered(days_ago)

    outcome = oracle("RN01", order, family="defect")

    assert outcome == expected


def test_it_should_deny_a_defect_claim_after_thirty_days(oracle):
    order = delivered(31)

    outcome = oracle("RN01", order, family="defect")

    assert outcome == "defect_denied"


def test_it_should_allow_a_defect_claim_on_an_opened_item(oracle):
    order = delivered(5, [item("PX-0002", "ração", opened=True)])

    outcome = oracle("RN01", order, sku="PX-0002", family="defect")

    assert outcome == "defect_allowed"


@pytest.mark.parametrize("status", ["Pago", "Em separação", "Enviado", "Cancelado"])
def test_it_should_fail_rn01_for_an_order_that_was_not_delivered(oracle, status):
    order = build_order(status=status)

    with pytest.raises(KIND_FAILURE):
        oracle("RN01", order, family="regret")


def test_it_should_fail_rn01_without_a_family(oracle):
    order = delivered(2)

    with pytest.raises(KIND_FAILURE):
        oracle("RN01", order)


def test_it_should_fail_rn01_for_a_sku_that_is_not_in_the_order(oracle):
    order = delivered(2)

    with pytest.raises(KIND_FAILURE):
        oracle("RN01", order, sku="PX-9999", family="regret")


@pytest.mark.parametrize(
    ("payment_method", "expected"),
    [
        ("Pix", "refund_pix_5_business_days"),
        ("Cartão", "refund_card_2_invoices"),
    ],
)
def test_it_should_pick_the_refund_by_payment_method(oracle, payment_method, expected):
    order = build_order(status="Cancelado", payment_method=payment_method)

    outcome = oracle("RN02", order)

    assert outcome == expected


@pytest.mark.parametrize(
    ("region", "total", "expected"),
    [
        ("Sul", "149.01", "free_shipping"),
        ("Sul", "149.00", "shipping_charged"),
        ("Sudeste", "150.00", "free_shipping"),
        ("Sudeste", "100.00", "shipping_charged"),
        ("Centro-Oeste", "200.00", "shipping_charged"),
        ("Nordeste", "249.00", "shipping_charged"),
        ("Norte", "249.01", "free_shipping"),
    ],
)
def test_it_should_charge_shipping_unless_the_total_is_above_the_regional_floor(
    oracle, region, total, expected
):
    order = build_order(region=region, total=Decimal(total))

    outcome = oracle("RN03", order)

    assert outcome == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Pago", "cancel_allowed"),
        ("Em separação", "cancel_allowed"),
        ("Enviado", "cancel_denied_return_flow"),
        ("Entregue", "cancel_denied_return_flow"),
    ],
)
def test_it_should_allow_cancelling_only_before_the_order_is_shipped(
    oracle, status, expected
):
    order = build_order(status=status)

    outcome = oracle("RN04", order)

    assert outcome == expected


def test_it_should_fail_rn04_for_a_cancelled_order(oracle):
    order = build_order(status="Cancelado")

    with pytest.raises(KIND_FAILURE):
        oracle("RN04", order)


def test_it_should_fail_for_an_unknown_rule(oracle):
    order = build_order()

    with pytest.raises(KIND_FAILURE):
        oracle("RN99", order)
