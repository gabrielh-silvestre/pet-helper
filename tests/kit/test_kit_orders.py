import re
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from pet_helper.orders.api import (
    FAILURE_503_ORDER,
    FAILURE_ENV,
    FAILURE_TIMEOUT_ORDER,
    MockOrderApi,
)
from pet_helper.orders.cpf import is_valid_cpf, normalize_cpf
from tests.kit.helpers import FOOD_AND_LITTER, prompts_section

pytestmark = pytest.mark.integration

REFERENCE_DATE = date(2026, 11, 3)
STATUSES = {"Pago", "Em separação", "Enviado", "Entregue", "Cancelado"}
SOUTH_SOUTHEAST = {"Sul", "Sudeste"}
UF_BY_REGION = {
    "Sul": {"PR", "SC", "RS"},
    "Sudeste": {"SP", "RJ", "MG", "ES"},
    "Centro-Oeste": {"DF", "GO", "MT", "MS"},
    "Nordeste": {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"},
    "Norte": {"AC", "AP", "AM", "PA", "RO", "RR", "TO"},
}
SLOT_NUMBERS = [f"PED-1000{n}" for n in range(1, 9)]
OWNER_PAIRS = [
    pytest.param("PED-10001", "PED-10002", id="C1"),
    pytest.param("PED-10003", "PED-10004", id="C2"),
    pytest.param("PED-10005", "PED-10006", id="C3"),
    pytest.param("PED-10007", "PED-10008", id="C4"),
]

# slot: (number, status, region, state, payment_method, delivered_on)
SLOTS = [
    pytest.param(
        "PED-10001", "Entregue", "Sul", "SC", "Cartão", date(2026, 10, 31), id="O1"
    ),
    pytest.param("PED-10002", "Em separação", "Sul", "SC", "Pix", None, id="O2"),
    pytest.param(
        "PED-10003", "Entregue", "Sudeste", "SP", "Pix", date(2026, 11, 1), id="O3"
    ),
    pytest.param(
        "PED-10004", "Entregue", "Sudeste", "SP", "Cartão", date(2026, 10, 27), id="O4"
    ),
    pytest.param(
        "PED-10005",
        "Entregue",
        "Centro-Oeste",
        "GO",
        "Cartão",
        date(2026, 10, 22),
        id="O5",
    ),
    pytest.param("PED-10006", "Enviado", "Centro-Oeste", "GO", "Pix", None, id="O6"),
    pytest.param("PED-10007", "Pago", "Nordeste", "BA", "Cartão", None, id="O7"),
    pytest.param("PED-10008", "Cancelado", "Nordeste", "BA", "Pix", None, id="O8"),
]

# answers: (number, rule, family, cites the opened item, expected outcome)
ANSWERS = [
    pytest.param(
        "PED-10001", "RN01", "regret", False, "regret_allowed", id="O1-regret"
    ),
    pytest.param("PED-10001", "RN03", None, False, "free_shipping", id="O1-shipping"),
    pytest.param(
        "PED-10001", "RN02", None, False, "refund_card_2_invoices", id="O1-refund"
    ),
    pytest.param(
        "PED-10001", "RN04", None, False, "cancel_denied_return_flow", id="O1-cancel"
    ),
    pytest.param(
        "PED-10002", "RN03", None, False, "shipping_charged", id="O2-shipping"
    ),
    pytest.param("PED-10002", "RN04", None, False, "cancel_allowed", id="O2-cancel"),
    pytest.param("PED-10003", "RN01", "regret", True, "regret_denied", id="O3-regret"),
    pytest.param("PED-10003", "RN01", "defect", True, "defect_allowed", id="O3-defect"),
    pytest.param(
        "PED-10004", "RN01", "regret", False, "regret_allowed", id="O4-regret"
    ),
    pytest.param("PED-10005", "RN01", "regret", True, "regret_denied", id="O5-regret"),
    pytest.param("PED-10005", "RN01", "defect", True, "defect_allowed", id="O5-defect"),
    pytest.param(
        "PED-10005", "RN03", None, False, "shipping_charged", id="O5-shipping"
    ),
    pytest.param(
        "PED-10006", "RN04", None, False, "cancel_denied_return_flow", id="O6-cancel"
    ),
    pytest.param(
        "PED-10006", "RN03", None, False, "shipping_charged", id="O6-shipping"
    ),
    pytest.param(
        "PED-10007", "RN03", None, False, "shipping_charged", id="O7-shipping"
    ),
    pytest.param("PED-10007", "RN04", None, False, "cancel_allowed", id="O7-cancel"),
    pytest.param(
        "PED-10008", "RN02", None, False, "refund_pix_5_business_days", id="O8-refund"
    ),
]


@pytest.fixture(autouse=True)
def _no_ambient_api_failure(monkeypatch):
    monkeypatch.delenv(FAILURE_ENV, raising=False)


@pytest.fixture
def by_number(order_book):
    return {order.number: order for order in order_book.orders}


@pytest.fixture
def api(orders_path):
    return MockOrderApi(orders_path)


def days_since_delivery(order, order_book):
    return (order_book.reference_date - order.delivered_on).days


def opened_item(order):
    return next(i for i in order.items if i.opened and i.category in FOOD_AND_LITTER)


# slots


def test_it_should_use_2026_11_03_as_the_reference_date(order_book):
    assert order_book.reference_date == REFERENCE_DATE


def test_it_should_list_the_slot_orders_first_and_in_slot_order(order_book):
    numbers = [order.number for order in order_book.orders[:8]]

    assert numbers == SLOT_NUMBERS


@pytest.mark.parametrize(
    ("number", "status", "region", "state", "payment", "delivered_on"), SLOTS
)
def test_it_should_give_each_slot_order_its_planned_status(
    by_number, number, status, region, state, payment, delivered_on
):
    assert by_number[number].status == status


@pytest.mark.parametrize(
    ("number", "status", "region", "state", "payment", "delivered_on"), SLOTS
)
def test_it_should_give_each_slot_order_its_planned_region_and_state(
    by_number, number, status, region, state, payment, delivered_on
):
    order = by_number[number]

    assert (order.region, order.state) == (region, state)


@pytest.mark.parametrize(
    ("number", "status", "region", "state", "payment", "delivered_on"), SLOTS
)
def test_it_should_give_each_slot_order_its_planned_payment_method(
    by_number, number, status, region, state, payment, delivered_on
):
    assert by_number[number].payment_method == payment


@pytest.mark.parametrize(
    ("number", "status", "region", "state", "payment", "delivered_on"), SLOTS
)
def test_it_should_give_each_slot_order_its_planned_delivery_date(
    by_number, number, status, region, state, payment, delivered_on
):
    assert by_number[number].delivered_on == delivered_on


@pytest.mark.parametrize(("first", "second"), OWNER_PAIRS)
def test_it_should_give_both_orders_of_a_slot_pair_the_same_customer(
    by_number, first, second
):
    assert by_number[first].customer_cpf == by_number[second].customer_cpf


def test_it_should_give_the_four_slot_customers_different_cpfs(by_number):
    owners = {
        by_number[n].customer_cpf
        for n in ("PED-10001", "PED-10003", "PED-10005", "PED-10007")
    }

    assert len(owners) == 4


def test_it_should_list_every_order_owner_among_the_customers(order_book):
    listed = {customer.cpf for customer in order_book.customers}

    assert {order.customer_cpf for order in order_book.orders} <= listed


@pytest.mark.parametrize(
    ("number", "rule", "family", "cites_opened", "expected"), ANSWERS
)
def test_it_should_recompute_each_slot_answer_with_the_oracle(
    by_number, oracle, number, rule, family, cites_opened, expected
):
    order = by_number[number]
    sku = opened_item(order).sku if cites_opened else None

    outcome = oracle(rule, order, sku=sku, family=family)

    assert outcome == expected


# volume and vocabulary


def test_it_should_have_at_least_eight_orders(order_book):
    assert len(order_book.orders) >= 8


def test_it_should_have_at_least_three_customers(order_book):
    assert len(order_book.customers) >= 3


def test_it_should_cover_the_five_statuses(order_book):
    assert {order.status for order in order_book.orders} >= STATUSES


def test_it_should_use_only_the_known_statuses(order_book):
    assert {order.status for order in order_book.orders} <= STATUSES


def test_it_should_cover_both_payment_methods(order_book):
    assert {order.payment_method for order in order_book.orders} >= {"Pix", "Cartão"}


def test_it_should_have_an_order_in_the_south_or_southeast(order_book):
    assert {o.region for o in order_book.orders} & SOUTH_SOUTHEAST


def test_it_should_have_an_order_outside_the_south_and_southeast(order_book):
    assert {o.region for o in order_book.orders} - SOUTH_SOUTHEAST


def test_it_should_keep_the_state_inside_its_region(order_book):
    incoherent = [
        order.number
        for order in order_book.orders
        if order.state not in UF_BY_REGION.get(order.region, set())
    ]

    assert incoherent == []


def test_it_should_not_repeat_an_order_number(order_book):
    counts = Counter(order.number for order in order_book.orders)

    assert [n for n, count in counts.items() if count > 1] == []


# delivery windows


@pytest.mark.parametrize("number", ["PED-10001", "PED-10003"])
def test_it_should_deliver_the_recent_slot_orders_less_than_seven_days_ago(
    by_number, order_book, number
):
    assert days_since_delivery(by_number[number], order_book) < 7


def test_it_should_deliver_the_boundary_slot_order_exactly_seven_days_ago(
    by_number, order_book
):
    assert days_since_delivery(by_number["PED-10004"], order_book) == 7


def test_it_should_deliver_the_late_slot_order_between_eight_and_thirty_days_ago(
    by_number, order_book
):
    assert 8 <= days_since_delivery(by_number["PED-10005"], order_book) <= 30


def test_it_should_have_a_delivery_date_only_for_delivered_orders(order_book):
    wrong = [
        order.number
        for order in order_book.orders
        if (order.delivered_on is not None) != (order.status == "Entregue")
    ]

    assert wrong == []


# totals


def test_it_should_total_each_order_as_the_sum_of_its_items(order_book):
    wrong = [
        order.number
        for order in order_book.orders
        if order.total != sum(i.quantity * i.unit_price for i in order.items)
    ]

    assert wrong == []


def totals_where(order_book, in_south_southeast, keep):
    # gross total (sum of the items); the discount is pinned in the discount tests
    return [
        o.total
        for o in order_book.orders
        if (o.region in SOUTH_SOUTHEAST) == in_south_southeast and keep(o.total)
    ]


def discounted_orders(order_book):
    return [order for order in order_book.orders if order.discount > 0]


def test_it_should_give_exactly_one_order_a_discount(order_book):
    discounted = [order.number for order in discounted_orders(order_book)]

    assert len(discounted) == 1


def test_it_should_change_the_shipping_verdict_of_the_discounted_order(
    order_book, oracle
):
    order = discounted_orders(order_book)[0]
    undiscounted = order.model_copy(update={"discount": Decimal("0")})

    verdicts = (oracle("RN03", order), oracle("RN03", undiscounted))

    assert verdicts[0] != verdicts[1]


def test_it_should_keep_every_discount_below_the_order_total(order_book):
    excessive = [o.number for o in order_book.orders if o.discount >= o.total]

    assert excessive == []


def test_it_should_have_a_south_or_southeast_total_above_149(order_book):
    assert totals_where(order_book, True, lambda t: t > 149)


def test_it_should_have_a_south_or_southeast_total_of_exactly_149(order_book):
    assert totals_where(order_book, True, lambda t: t == 149)


def test_it_should_have_a_total_between_150_and_249_outside_the_south_and_southeast(
    order_book,
):
    assert totals_where(order_book, False, lambda t: 150 <= t <= 249)


def test_it_should_have_a_total_of_exactly_249_outside_the_south_and_southeast(
    order_book,
):
    assert totals_where(order_book, False, lambda t: t == 249)


def test_it_should_have_a_total_above_249_outside_the_south_and_southeast(order_book):
    assert totals_where(order_book, False, lambda t: t > 249)


# items


def test_it_should_open_only_food_or_litter_items(order_book):
    wrong = [
        order.number
        for order in order_book.orders
        for item in order.items
        if item.opened and item.category not in FOOD_AND_LITTER
    ]

    assert wrong == []


def test_it_should_have_at_least_one_opened_food_or_litter_item(order_book):
    opened = [i for o in order_book.orders for i in o.items if i.opened]

    assert opened


def test_it_should_only_order_skus_that_exist_in_the_catalog(order_book, products):
    known = {product.sku for product in products}
    missing = {i.sku for o in order_book.orders for i in o.items} - known

    assert missing == set()


def test_it_should_give_each_item_the_category_of_the_catalog(order_book, products):
    catalog = {product.sku: product for product in products}
    wrong = [
        item.sku
        for order in order_book.orders
        for item in order.items
        if item.sku in catalog and item.category != catalog[item.sku].category
    ]

    assert wrong == []


def test_it_should_give_each_item_the_unit_price_of_the_catalog(order_book, products):
    catalog = {product.sku: product for product in products}
    wrong = [
        item.sku
        for order in order_book.orders
        for item in order.items
        if item.sku in catalog and item.unit_price != catalog[item.sku].price
    ]

    assert wrong == []


@pytest.mark.parametrize("number", [FAILURE_503_ORDER, FAILURE_TIMEOUT_ORDER])
def test_it_should_not_contain_the_order_numbers_reserved_for_api_failures(
    order_book, number
):
    assert number not in {order.number for order in order_book.orders}


# mock api over the real file


def test_it_should_answer_200_to_the_owner_of_every_order(order_book, api):
    codes = [api.get(o.number, o.customer_cpf).status for o in order_book.orders]

    assert set(codes) == {200}


def test_it_should_serve_the_five_statuses_through_the_api(order_book, api):
    served = {
        api.get(o.number, o.customer_cpf).body["status"] for o in order_book.orders
    }

    assert served == STATUSES


def test_it_should_answer_403_to_another_customer_for_every_order(order_book, api):
    codes = set()
    for order in order_book.orders:
        stranger = next(
            c.cpf for c in order_book.customers if c.cpf != order.customer_cpf
        )
        codes.add(api.get(order.number, stranger).status)

    assert codes == {403}


def test_it_should_answer_404_for_an_order_number_that_does_not_exist(order_book, api):
    owner = order_book.orders[0].customer_cpf

    response = api.get("PED-99999", owner)

    assert response.status == 404


# fictional people


SLOT_CUSTOMER_CPFS = ["00000000191", "00000000272", "00000000353", "00000000434"]


def test_it_should_give_the_slot_customers_the_reserved_fake_cpfs_in_order(order_book):
    cpfs = [customer.cpf for customer in order_book.customers[:4]]

    assert cpfs == SLOT_CUSTOMER_CPFS


def test_it_should_keep_every_customer_cpf_in_the_reserved_zero_prefix_range(
    order_book,
):
    outside = [c.cpf for c in order_book.customers if not c.cpf.startswith("000000")]

    assert outside == []


def test_it_should_use_valid_cpfs_for_every_customer(order_book):
    invalid = [c.cpf for c in order_book.customers if not is_valid_cpf(c.cpf)]

    assert invalid == []


def test_it_should_store_customer_cpfs_as_digits_only(order_book):
    wrong = [c.cpf for c in order_book.customers if c.cpf != normalize_cpf(c.cpf)]

    assert wrong == []


def test_it_should_use_only_example_com_emails(order_book):
    domains = {c.email.rsplit("@", 1)[-1] for c in order_book.customers}

    assert all(d == "example.com" or d.endswith(".example.com") for d in domains)


# prompt record


@pytest.fixture
def orders_prompt():
    block = re.search(
        r"^(`{3,})[^\n]*\n(.*?)^\1[ \t]*$", prompts_section("Pedidos"), re.S | re.M
    )
    assert block, "'## Pedidos' has no fenced prompt block"
    return block.group(2)


def test_it_should_record_a_non_empty_prompt_for_the_orders(orders_prompt):
    assert orders_prompt.strip()


def test_it_should_anchor_the_orders_prompt_on_the_reference_date(orders_prompt):
    assert "2026-11-03" in orders_prompt


def test_it_should_name_the_order_numbers_in_the_orders_prompt(orders_prompt):
    assert "PED-" in orders_prompt


@pytest.mark.parametrize("slot", [f"O{n}" for n in range(1, 9)])
def test_it_should_give_the_orders_prompt_every_slot(orders_prompt, slot):
    assert re.search(rf"\b{slot}\b", orders_prompt)
