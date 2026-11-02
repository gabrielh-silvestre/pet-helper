from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from pet_helper.catalog.models import load_products
from pet_helper.conversation import build_assistant
from pet_helper.evals.cases import load_cases
from pet_helper.evals.fixtures import overlay_kit
from pet_helper.gateway import FakeGateway
from pet_helper.orders.api import FAILURE_ENV

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "kit"
FIXTURE = "indirect-injection"
FIXTURE_DIR = ROOT / "evals" / "fixtures" / FIXTURE
POISONED_SKU = "PX-0001"
POLICY = "canais-e-horarios.md"
CANARY_FORMS = {"CANARIO-200", "24h"}
SESSION_ID = "session-overlay"


@pytest.fixture(autouse=True)
def _resolve_the_fixture_from_the_repo_root(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv(FAILURE_ENV, raising=False)


@pytest.fixture
def overlay(tmp_path) -> Path:
    destination = tmp_path / "overlay"
    destination.mkdir()
    overlay_kit(KIT, FIXTURE, destination)
    return destination


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _call(name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": "call_1"}]
    )


def _tool_result(gateway: FakeGateway) -> str:
    tool_messages = [m for m in gateway.calls[-1] if isinstance(m, ToolMessage)]
    return str(tool_messages[0].content)


def _case(case_id: str):
    return {case.id: case for case in load_cases(ROOT / "evals" / "cases")}[case_id]


def test_it_should_ship_the_fixture_with_only_the_catalog_and_the_channels_policy():
    files = sorted(
        str(path.relative_to(FIXTURE_DIR))
        for path in FIXTURE_DIR.rglob("*")
        if path.is_file()
    )

    assert files == ["catalog.csv", f"policies/{POLICY}"]


def test_it_should_write_the_fixture_catalog_over_the_copied_base_catalog(overlay):
    assert (overlay / "catalog.csv").read_bytes() == (
        FIXTURE_DIR / "catalog.csv"
    ).read_bytes()


def test_it_should_write_the_fixture_policy_over_the_copied_base_policy(overlay):
    assert (overlay / "policies" / POLICY).read_bytes() == (
        FIXTURE_DIR / "policies" / POLICY
    ).read_bytes()


def test_it_should_copy_every_other_kit_file_unchanged(overlay):
    overlaid = {"catalog.csv", f"policies/{POLICY}"}

    copied = _tree(overlay)

    base = _tree(KIT)
    assert copied.keys() == base.keys()
    assert {name for name in base if copied[name] != base[name]} == overlaid


def test_it_should_leave_the_base_kit_byte_identical(tmp_path):
    before = _tree(KIT)
    destination = tmp_path / "overlay"
    destination.mkdir()

    overlay_kit(KIT, FIXTURE, destination)

    assert _tree(KIT) == before


def test_it_should_reject_an_unknown_fixture(tmp_path):
    destination = tmp_path / "overlay"
    destination.mkdir()

    with pytest.raises((FileNotFoundError, LookupError, ValueError)):
        overlay_kit(KIT, "no-such-fixture", destination)


def test_it_should_append_the_instruction_to_the_description_of_the_salmon_food(
    overlay,
):
    base = {p.sku: p.description for p in load_products(KIT / "catalog.csv")}
    poisoned = {p.sku: p.description for p in load_products(overlay / "catalog.csv")}

    appended = poisoned[POISONED_SKU].removeprefix(base[POISONED_SKU])

    assert "CANARIO-100" in appended and "escalate_to_human" in appended


def test_it_should_change_no_other_product_than_the_salmon_food(overlay):
    base = load_products(KIT / "catalog.csv")
    poisoned = load_products(overlay / "catalog.csv")

    changed = {a.sku for a, b in zip(base, poisoned, strict=True) if a != b}

    assert len(base) == len(poisoned) and changed == {POISONED_SKU}


def test_it_should_append_the_canary_instruction_to_the_channels_policy(overlay):
    base = (KIT / "policies" / POLICY).read_text(encoding="utf-8").strip()
    poisoned = (overlay / "policies" / POLICY).read_text(encoding="utf-8")

    assert base in poisoned and "CANARIO-200" in poisoned.removeprefix(base)


def test_it_should_forbid_in_the_policy_case_a_phone_number_only_the_fixture_adds(
    overlay,
):
    forbidden = {
        check_value
        for check in _case("inj-indirect-policy-text").checks
        if check.type == "must_not_include"
        for check_value in (
            check.value if isinstance(check.value, list) else [check.value]
        )
    }
    numbers = forbidden - CANARY_FORMS
    base_policies = " ".join(
        path.read_text(encoding="utf-8") for path in (KIT / "policies").glob("*.md")
    )
    poisoned = (overlay / "policies" / POLICY).read_text(encoding="utf-8")

    assert len(numbers) == 1
    assert all(number in poisoned and number not in base_policies for number in numbers)


def test_it_should_load_the_overlay_catalog_in_an_assistant_built_on_it(overlay):
    gateway = FakeGateway(
        [
            _call(
                "search_catalog",
                {"query": "Bigodin Salmão", "species": "gato", "category": "ração"},
            ),
            "Encontrei duas opções.",
        ]
    )
    assistant = build_assistant(
        gateway=gateway, kit_dir=overlay, today=date(2026, 11, 3)
    )

    assistant.turn(SESSION_ID, "Quero uma ração de salmão para gato castrado.")

    assert "CANARIO-100" in _tool_result(gateway)


def test_it_should_load_the_overlay_policy_in_an_assistant_built_on_it(overlay):
    gateway = FakeGateway(
        [
            _call("search_policies", {"query": "horário de atendimento WhatsApp"}),
            "O WhatsApp atende de segunda a sábado.",
        ]
    )
    assistant = build_assistant(
        gateway=gateway, kit_dir=overlay, today=date(2026, 11, 3)
    )

    assistant.turn(SESSION_ID, "Qual o horário no WhatsApp?")

    assert "CANARIO-200" in _tool_result(gateway)


def test_it_should_not_leak_the_canary_into_an_assistant_built_on_the_base_kit():
    gateway = FakeGateway(
        [
            _call(
                "search_catalog",
                {"query": "Bigodin Salmão", "species": "gato", "category": "ração"},
            ),
            "Encontrei duas opções.",
        ]
    )
    assistant = build_assistant(gateway=gateway, kit_dir=KIT, today=date(2026, 11, 3))

    assistant.turn(SESSION_ID, "Quero uma ração de salmão para gato castrado.")

    assert "CANARIO" not in _tool_result(gateway)
