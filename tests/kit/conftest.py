import unicodedata
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"

FOOD_AND_LITTER = frozenset({"ração", "areia"})
FREE_SHIPPING_FLOOR = {"Sul": 149, "Sudeste": 149}
FREE_SHIPPING_FLOOR_ELSEWHERE = 249
REFUND_BY_PAYMENT = {
    "Pix": "refund_pix_5_business_days",
    "Cartão": "refund_card_2_invoices",
}
CANCEL_BY_STATUS = {
    "Pago": "cancel_allowed",
    "Em separação": "cancel_allowed",
    "Enviado": "cancel_denied_return_flow",
    "Entregue": "cancel_denied_return_flow",
}
REGRET_WINDOW_DAYS = 7
DEFECT_WINDOW_DAYS = 30

# expected_outcome -> (required terms, forbidden terms), already folded (no accent, lowercase)
OUTCOME_TERMS = {
    "regret_allowed": (("7 dias",), ("fora do prazo", "somente por defeito")),
    "regret_denied": (("defeito",), ("dentro do prazo",)),
    "defect_allowed": (("30 dias",), ("fora do prazo",)),
    "defect_denied": (("30 dias", "fora do prazo"), ()),
    "refund_pix_5_business_days": (("pix", "5 dias uteis"), ("faturas",)),
    "refund_card_2_invoices": (("cartao", "2 faturas"), ("5 dias uteis",)),
    "free_shipping": (("frete gratis",), ("nao tem frete gratis",)),
    "shipping_charged": (("acima de",), ("ganhou frete gratis",)),
    "cancel_allowed": (("cancel",), ("fluxo de devolucao",)),
    "cancel_denied_return_flow": (("devolucao",), ("foi cancelado",)),
}


def fold(text: str) -> str:
    """NFC, lowercase and accent-free, the comparison form of the lexical net."""
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _regret(order, days, sku):
    if days > REGRET_WINDOW_DAYS:
        return "regret_denied"
    if sku is None:
        opened = any(item.opened for item in order.items)
    else:
        item = next((i for i in order.items if i.sku == sku), None)
        if item is None:
            pytest.fail(f"oracle: {sku} is not an item of {order.number}")
        opened = item.opened and item.category in FOOD_AND_LITTER
    return "regret_denied" if opened else "regret_allowed"


def _rn01(order, reference_date, sku, family):
    if order.status != "Entregue":
        pytest.fail(f"oracle: RN01 only covers delivered orders, got {order.status}")
    days = (reference_date - order.delivered_on).days
    if family == "regret":
        return _regret(order, days, sku)
    if family == "defect":
        return "defect_allowed" if days <= DEFECT_WINDOW_DAYS else "defect_denied"
    pytest.fail(f"oracle: RN01 needs family regret or defect, got {family!r}")


def _rn03(order):
    floor = FREE_SHIPPING_FLOOR.get(order.region, FREE_SHIPPING_FLOOR_ELSEWHERE)
    return "free_shipping" if order.total > floor else "shipping_charged"


def _rn04(order):
    if order.status not in CANCEL_BY_STATUS:
        pytest.fail(f"oracle: RN04 does not apply to a {order.status} order")
    return CANCEL_BY_STATUS[order.status]


@pytest.fixture
def oracle_for():
    """Builds an oracle anchored on a reference date, so it needs no kit file."""

    def build(reference_date):
        def oracle(rule, order, sku=None, family=None):
            if rule == "RN01":
                return _rn01(order, reference_date, sku, family)
            if rule == "RN02":
                return REFUND_BY_PAYMENT[order.payment_method]
            if rule == "RN03":
                return _rn03(order)
            if rule == "RN04":
                return _rn04(order)
            pytest.fail(f"oracle: unknown rule {rule!r}")

        return oracle

    return build


@pytest.fixture
def kit_dir():
    return KIT_DIR


@pytest.fixture
def orders_path(kit_dir):
    return kit_dir / "orders.json"


@pytest.fixture
def order_book(orders_path):
    from pet_helper.orders.models import load_orders

    return load_orders(orders_path)


@pytest.fixture
def products(kit_dir):
    from pet_helper.catalog.models import load_products

    return load_products(kit_dir / "catalog.csv")


@pytest.fixture
def oracle(oracle_for, order_book):
    return oracle_for(order_book.reference_date)


@pytest.fixture
def outcome_terms():
    return OUTCOME_TERMS


@pytest.fixture(name="fold")
def fold_fixture():
    return fold
