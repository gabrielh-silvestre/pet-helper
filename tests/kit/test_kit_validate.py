import copy
import csv
import importlib.util
import json
import re
import shutil
import unicodedata
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

VALIDATE_PATH = Path(__file__).resolve().parents[2] / "kit" / "validate.py"


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
