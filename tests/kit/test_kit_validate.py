import copy
import csv
import importlib.util
import json
import re
import shutil
import unicodedata
from pathlib import Path

import pytest

from tests.kit.helpers import KIT_DIR

pytestmark = pytest.mark.integration

VALIDATE_PATH = KIT_DIR / "validate.py"


@pytest.fixture(scope="module")
def validate_module():
    spec = importlib.util.spec_from_file_location("kit_validate", VALIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def kit(tmp_path, kit_dir):
    return Path(shutil.copytree(kit_dir, tmp_path / "kit"))


@pytest.fixture
def intact_failures(validate_module, kit):
    return validate_module.validate(kit)


def flagged(failures, area, pattern):
    return any(
        f.startswith(f"{area}: ") and re.search(pattern, f, re.IGNORECASE)
        for f in failures
    )


def first_policy(kit):
    return sorted((kit / "policies").glob("*.md"))[0]


def with_words(count):
    return "# Policy\n\n" + " ".join(["word"] * (count - 2)) + "\n"


def edit_catalog(kit, edit):
    path = kit / "catalog.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        header, *rows = csv.reader(handle)
    header, rows = edit(header, rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([header, *rows])


def padded_to(rows, size):
    extra = [[f"PX-9{i:03d}", *rows[0][1:]] for i in range(max(size - len(rows), 0))]
    return rows[:size] + extra


def edit_orders(kit, edit):
    path = kit / "orders.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def resized_orders(size):
    def edit(data):
        orders = data["orders"]
        extra = []
        for i in range(max(size - len(orders), 0)):
            order = copy.deepcopy(orders[0])
            order["number"] = f"PED-9{i:04d}"
            extra.append(order)
        data["orders"] = orders[:size] + extra

    return edit


def test_it_should_fail_when_a_policy_is_missing(validate_module, kit, intact_failures):
    policies = sorted((kit / "policies").glob("*.md"))
    for policy in policies[: len(policies) - 7]:
        policy.unlink()

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"policy count 7, expected"
    assert not flagged(intact_failures, "policies", pattern)
    assert flagged(failures, "policies", pattern)
    assert exit_code == 1


def test_it_should_fail_when_a_policy_has_149_words(
    validate_module, kit, intact_failures
):
    first_policy(kit).write_text(with_words(149), encoding="utf-8")

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    assert not flagged(intact_failures, "policies", r"\.md words 149,")
    assert flagged(failures, "policies", r"\.md words 149,")
    assert exit_code == 1


def test_it_should_fail_when_a_policy_has_401_words(
    validate_module, kit, intact_failures
):
    first_policy(kit).write_text(with_words(401), encoding="utf-8")

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    assert not flagged(intact_failures, "policies", r"\.md words 401,")
    assert flagged(failures, "policies", r"\.md words 401,")
    assert exit_code == 1


def test_it_should_fail_when_a_policy_has_no_h1(validate_module, kit, intact_failures):
    path = first_policy(kit)
    path.write_text("#" + path.read_text(encoding="utf-8"), encoding="utf-8")

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"first line is not an H1 title"
    assert not flagged(intact_failures, "policies", pattern)
    assert flagged(failures, "policies", pattern)
    assert exit_code == 1


def test_it_should_fail_when_the_catalog_header_changes(
    validate_module, kit, intact_failures
):
    edit_catalog(kit, lambda header, rows: ([*header[:-1], "description"], rows))

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    assert not flagged(intact_failures, "catalog", r"header is not")
    assert flagged(failures, "catalog", r"header is not")
    assert exit_code == 1


def test_it_should_accept_the_catalog_header_in_nfd(validate_module, kit):
    edit_catalog(
        kit,
        lambda header, rows: ([unicodedata.normalize("NFD", h) for h in header], rows),
    )

    failures = validate_module.validate(kit)

    assert not flagged(failures, "catalog", r"header is not")


def test_it_should_fail_when_the_catalog_has_39_products(
    validate_module, kit, intact_failures
):
    edit_catalog(kit, lambda header, rows: (header, padded_to(rows, 39)))

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"product count 39, expected"
    assert not flagged(intact_failures, "catalog", pattern)
    assert flagged(failures, "catalog", pattern)
    assert exit_code == 1


def test_it_should_fail_when_the_catalog_has_61_products(
    validate_module, kit, intact_failures
):
    edit_catalog(kit, lambda header, rows: (header, padded_to(rows, 61)))

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"product count 61, expected"
    assert not flagged(intact_failures, "catalog", pattern)
    assert flagged(failures, "catalog", pattern)
    assert exit_code == 1


def test_it_should_fail_when_there_are_7_orders(validate_module, kit, intact_failures):
    edit_orders(kit, resized_orders(7))

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"order count 7, expected"
    assert not flagged(intact_failures, "orders", pattern)
    assert flagged(failures, "orders", pattern)
    assert exit_code == 1


def test_it_should_fail_when_there_are_11_orders(validate_module, kit, intact_failures):
    edit_orders(kit, resized_orders(11))

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    pattern = r"order count 11, expected"
    assert not flagged(intact_failures, "orders", pattern)
    assert flagged(failures, "orders", pattern)
    assert exit_code == 1


def test_it_should_fail_when_an_order_sku_is_missing_from_the_catalog(
    validate_module, kit, intact_failures
):
    def edit(data):
        data["orders"][0]["items"][0]["sku"] = "PX-9999"

    edit_orders(kit, edit)

    failures = validate_module.validate(kit)
    exit_code = validate_module.main([str(kit)])

    assert not flagged(intact_failures, "orders", r"PX-9999 not in the catalog")
    assert flagged(failures, "orders", r"PX-9999 not in the catalog")
    assert exit_code == 1


def test_it_should_fail_when_a_delivered_order_has_no_delivery_date(
    validate_module, kit
):
    def edit(data):
        del data["orders"][0]["delivered_on"]

    edit_orders(kit, edit)

    failures = validate_module.validate(kit)

    assert flagged(failures, "orders", r"PED-10001 is Entregue without delivered_on")


def test_it_should_suggest_delivered_on_when_the_field_is_misspelled(
    validate_module, kit
):
    def edit(data):
        order = data["orders"][0]
        order["delivered_one"] = order.pop("delivered_on")

    edit_orders(kit, edit)

    failures = validate_module.validate(kit)

    assert flagged(failures, "orders", r"'delivered_one'.*did you mean 'delivered_on'")


def test_it_should_suggest_opened_when_the_item_field_is_misspelled(
    validate_module, kit
):
    def edit(data):
        item = data["orders"][0]["items"][0]
        item["opend"] = item.pop("opened")

    edit_orders(kit, edit)

    failures = validate_module.validate(kit)

    assert flagged(failures, "orders", r"'opend'.*did you mean 'opened'")


def test_it_should_accept_a_legitimate_extra_order_field(validate_module, kit):
    def edit(data):
        data["orders"][0]["gift_note"] = "wrap it"

    edit_orders(kit, edit)

    failures = validate_module.validate(kit)

    assert failures == []


def test_it_should_accept_the_intact_kit(intact_failures):
    assert intact_failures == []


def test_it_should_exit_zero_for_the_intact_kit(validate_module, kit):
    exit_code = validate_module.main([str(kit)])

    assert exit_code == 0


def test_it_should_accept_the_default_kit_directory(validate_module):
    failures = validate_module.validate()

    assert failures == []


def test_it_should_exit_zero_for_the_default_kit_directory(validate_module):
    exit_code = validate_module.main([])

    assert exit_code == 0


def add_policies(count):
    def edit(kit):
        sources = sorted((kit / "policies").glob("*.md"))
        for i, source in enumerate(sources[:count]):
            shutil.copy(source, kit / "policies" / f"extra-{i}.md")

    return edit


def policy_with_words(count):
    return lambda kit: first_policy(kit).write_text(with_words(count), encoding="utf-8")


def catalog_of_size(size):
    def edit(kit):
        data = json.loads((kit / "orders.json").read_text(encoding="utf-8"))
        used = {item["sku"] for order in data["orders"] for item in order["items"]}

        def resize(header, rows):
            # produtos citados por pedidos ficam, senão o corte vira "not in the catalog"
            kept = sorted(rows, key=lambda row: row[0] not in used)[:size]
            return header, padded_to(kept, size)

        edit_catalog(kit, resize)

    return edit


def orders_of_size(size):
    return lambda kit: edit_orders(kit, resized_orders(size))


@pytest.mark.parametrize(
    "edit",
    [
        add_policies(2),
        policy_with_words(150),
        policy_with_words(400),
        catalog_of_size(40),
        catalog_of_size(60),
        orders_of_size(10),
    ],
    ids=[
        "10-policies",
        "150-words",
        "400-words",
        "40-products",
        "60-products",
        "10-orders",
    ],
)
def test_it_should_accept_a_kit_at_the_edge_of_an_allowed_range(
    validate_module, kit, edit
):
    edit(kit)

    failures = validate_module.validate(kit)

    assert failures == []


def test_it_should_fail_when_the_catalog_is_missing(validate_module, kit):
    (kit / "catalog.csv").unlink()

    failures = validate_module.validate(kit)

    assert flagged(failures, "catalog", r"header is not")


def test_it_should_fail_when_the_catalog_is_empty(validate_module, kit):
    (kit / "catalog.csv").write_text("", encoding="utf-8")

    failures = validate_module.validate(kit)

    assert flagged(failures, "catalog", r"header is not")


def test_it_should_fail_when_the_orders_file_is_missing(validate_module, kit):
    (kit / "orders.json").unlink()

    failures = validate_module.validate(kit)

    assert flagged(failures, "orders", r"order count 0, expected")
