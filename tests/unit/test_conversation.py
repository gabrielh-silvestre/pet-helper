import dataclasses
import json
from pathlib import Path

import pytest

import pet_helper.conversation as conversation
from pet_helper.catalog import Catalog
from pet_helper.conversation import Assistant, Reply
from pet_helper.gateway import AnthropicGateway, FakeGateway, OllamaGateway
from pet_helper.guard import Guard
from pet_helper.knowledge import PolicyIndex
from pet_helper.orders import OrderService
from pet_helper.orders.api import MockOrderApi

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"

CONVERSATION_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_OLLAMA_MODEL",
    "PET_HELPER_EFFORT",
    "PET_HELPER_FALLBACK",
    "PET_HELPER_KIT_DIR",
)


@pytest.fixture(autouse=True)
def _clean_conversation_env(monkeypatch):
    for name in CONVERSATION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_it_should_return_a_reply_with_plain_string_text_from_assistant_turn(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    assistant = conversation.build_assistant(
        gateway=FakeGateway(["Olá! Como posso ajudar?"])
    )

    reply = assistant.turn("session-1", "Olá")

    assert reply == Reply(
        text="Olá! Como posso ajudar?",
        sources=(),
        escalation=None,
    )
    assert type(reply.text) is str


def test_it_should_keep_reply_instances_immutable():
    reply = Reply(text="Olá")

    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.text = "alterado"  # type: ignore[misc]


def test_it_should_build_assistant_with_the_selected_ollama_gateway_when_no_key_is_set(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    created_gateways: list[OllamaGateway] = []
    real_init = OllamaGateway.__init__

    def spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created_gateways.append(self)

    monkeypatch.setattr(OllamaGateway, "__init__", spy_init)

    assistant = conversation.build_assistant()

    assert isinstance(assistant, Assistant)
    assert len(created_gateways) == 1


def test_it_should_build_assistant_with_the_selected_anthropic_gateway_when_api_key_is_set(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    created_gateways: list[AnthropicGateway] = []
    real_init = AnthropicGateway.__init__

    def spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created_gateways.append(self)

    monkeypatch.setattr(AnthropicGateway, "__init__", spy_init)

    assistant = conversation.build_assistant()

    assert isinstance(assistant, Assistant)
    assert len(created_gateways) == 1


def test_it_should_load_the_default_kit_directory_relative_to_cwd(
    tmp_path, monkeypatch
):
    kit_dir = tmp_path / "kit"
    policies_dir = kit_dir / "policies"
    policies_dir.mkdir(parents=True)
    (policies_dir / "frete.md").write_text(
        "# Frete\n\nFrete grátis acima do mínimo regional.\n",
        encoding="utf-8",
    )
    (policies_dir / "reembolso.md").write_text(
        "# Reembolso\n\nReembolso no Pix em até cinco dias úteis.\n",
        encoding="utf-8",
    )
    (kit_dir / "catalog.csv").write_text(
        "SKU,nome,categoria,espécie,preço,estoque,descrição\n"
        "PX-0001,Ração Teste Salmão 3 kg,ração,gato,89.90,10,Ração para gatos.\n",
        encoding="utf-8",
    )
    (kit_dir / "orders.json").write_text(
        json.dumps(
            {
                "reference_date": "2026-11-03",
                "customers": [
                    {
                        "cpf": "52998224725",
                        "name": "Cliente Teste",
                        "email": "cliente@example.com",
                    }
                ],
                "orders": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    gateway = FakeGateway(["Identificação recebida."])

    reply = conversation.build_assistant(gateway=gateway).turn(
        "session-cwd-kit", "Meu CPF é 529.982.247-25"
    )

    assert reply.text == "Identificação recebida."
    assert any(
        "[CPF informado]" in str(message.content)
        for message in gateway.calls[0]
        if message.type == "human"
    )


def test_it_should_load_the_kit_from_pet_helper_kit_dir_when_cwd_has_no_kit(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    assistant = conversation.build_assistant(
        gateway=FakeGateway(["Kit carregado por variável."])
    )

    reply = assistant.turn("session-env-kit", "Olá")

    assert reply.text == "Kit carregado por variável."


@pytest.mark.parametrize(
    ("configured_kit_dir", "expected_subpath"),
    [
        (None, "kit"),
        ("custom/missing-kit", "custom/missing-kit"),
    ],
)
def test_it_should_raise_a_single_line_error_naming_the_resolved_missing_kit_path(
    tmp_path, monkeypatch, configured_kit_dir, expected_subpath
):
    monkeypatch.chdir(tmp_path)
    if configured_kit_dir is not None:
        monkeypatch.setenv("PET_HELPER_KIT_DIR", configured_kit_dir)
    expected_resolved = str((tmp_path / expected_subpath).resolve())

    with pytest.raises(Exception) as exc_info:
        conversation.build_assistant(gateway=FakeGateway(["ok"]))

    message = str(exc_info.value).strip()
    assert not isinstance(
        exc_info.value, (AttributeError, TypeError, NameError, ImportError)
    )
    assert expected_resolved in message
    assert "\n" not in message


def test_it_should_raise_a_single_line_error_naming_the_resolved_path_for_an_incomplete_kit(
    tmp_path, monkeypatch
):
    empty_kit = tmp_path / "empty-kit"
    empty_kit.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PET_HELPER_KIT_DIR", "empty-kit")

    with pytest.raises(Exception) as exc_info:
        conversation.build_assistant(gateway=FakeGateway(["ok"]))

    message = str(exc_info.value).strip()
    assert not isinstance(
        exc_info.value, (AttributeError, TypeError, NameError, ImportError)
    )
    assert str(empty_kit.resolve()) in message
    assert "\n" not in message


@pytest.mark.parametrize(
    "provided",
    [("policies",), ("catalog", "orders")],
    ids=["policies", "catalog+orders"],
)
def test_it_should_raise_a_clear_error_when_built_with_partial_services_and_no_registry(
    provided,
):
    catalog = Catalog(REPO_KIT_DIR / "catalog.csv")
    services = {
        "policies": PolicyIndex.load(REPO_KIT_DIR / "policies"),
        "catalog": catalog,
        "orders": OrderService(MockOrderApi(REPO_KIT_DIR / "orders.json")),
    }

    with pytest.raises(ValueError, match="registry"):
        Assistant(
            FakeGateway(["ok"]),
            Guard(catalog),
            **{name: services[name] for name in provided},
        )
