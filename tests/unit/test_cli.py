import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

import pet_helper.cli
import pet_helper.conversation
from pet_helper.cli import main
from pet_helper.gateway import AnthropicGateway, FakeGateway, OllamaGateway

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SCRIPTED_REPLY = "Olá! Posso ajudar com políticas, pedidos ou produtos."

CLI_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OLLAMA_BASE_URL",
    "PET_HELPER_PROVIDER",
    "PET_HELPER_ASSISTANT_MODEL",
    "PET_HELPER_EFFORT",
    "PET_HELPER_FALLBACK",
    "PET_HELPER_KIT_DIR",
)


@pytest.fixture(autouse=True)
def _configure_cli_env(monkeypatch):
    for name in CLI_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))


def _set_input_lines(monkeypatch, lines, exhausted=EOFError):
    pending = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(pending)
        except StopIteration:
            raise exhausted from None

    monkeypatch.setattr("builtins.input", fake_input)


def _patch_build_assistant(monkeypatch, replacement):
    monkeypatch.setattr(pet_helper.conversation, "build_assistant", replacement)
    for mod_name, mod in list(sys.modules.items()):
        if (
            mod_name == "pet_helper.cli" or mod_name.startswith("pet_helper.cli.")
        ) and hasattr(mod, "build_assistant"):
            monkeypatch.setattr(mod, "build_assistant", replacement)


def _run_chat(monkeypatch, lines, gateway, exhausted=EOFError):
    _set_input_lines(monkeypatch, lines, exhausted=exhausted)
    real_build_assistant = pet_helper.conversation.build_assistant
    _patch_build_assistant(
        monkeypatch,
        lambda: real_build_assistant(gateway=gateway),
    )
    try:
        return main(["chat"])
    except KeyboardInterrupt:
        # Um KeyboardInterrupt que escape de main() abortaria a sessão inteira do pytest.
        pytest.fail("KeyboardInterrupt escaped main()")


def test_it_should_build_the_ollama_gateway_through_build_assistant_without_calling_it(
    monkeypatch,
):
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    _set_input_lines(monkeypatch, [])
    built_assistants: list[object] = []
    created_gateways: list[OllamaGateway] = []
    real_build_assistant = pet_helper.conversation.build_assistant
    real_ollama_init = OllamaGateway.__init__

    def spy_ollama_init(self, *args, **kwargs):
        real_ollama_init(self, *args, **kwargs)
        created_gateways.append(self)

    def fail_on_complete(self, messages, tools=()):
        pytest.fail("OllamaGateway.complete should not be called without a turn")

    def spy_build_assistant(*args, **kwargs):
        assistant = real_build_assistant(*args, **kwargs)
        built_assistants.append(assistant)
        return assistant

    monkeypatch.setattr(OllamaGateway, "__init__", spy_ollama_init)
    monkeypatch.setattr(OllamaGateway, "complete", fail_on_complete)
    _patch_build_assistant(monkeypatch, spy_build_assistant)

    code = main(["chat"])

    assert code == 0
    assert len(built_assistants) == 1
    assert len(created_gateways) == 1


def test_it_should_build_the_anthropic_gateway_in_chat_when_api_key_is_set(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _set_input_lines(monkeypatch, [])
    built_assistants: list[object] = []
    created_gateways: list[AnthropicGateway] = []
    real_build_assistant = pet_helper.conversation.build_assistant
    real_anthropic_init = AnthropicGateway.__init__

    def spy_anthropic_init(self, *args, **kwargs):
        real_anthropic_init(self, *args, **kwargs)
        created_gateways.append(self)

    def spy_build_assistant(*args, **kwargs):
        assistant = real_build_assistant(*args, **kwargs)
        built_assistants.append(assistant)
        return assistant

    monkeypatch.setattr(AnthropicGateway, "__init__", spy_anthropic_init)
    _patch_build_assistant(monkeypatch, spy_build_assistant)

    code = main(["chat"])

    assert code == 0
    assert len(built_assistants) == 1
    assert len(created_gateways) == 1


def test_it_should_print_the_reply_for_each_line_with_text(monkeypatch, capsys):
    gateway = FakeGateway(["Primeira resposta.", "Segunda resposta."])

    code = _run_chat(monkeypatch, ["Olá", "Qual o prazo de frete?"], gateway)

    assert code == 0
    assert capsys.readouterr().out == "Primeira resposta.\nSegunda resposta.\n\n"


def test_it_should_preserve_conversation_history_across_turns_in_chat(monkeypatch):
    gateway = FakeGateway(["Preciso do seu CPF.", "Qual o número do outro pedido?"])

    _run_chat(
        monkeypatch,
        ["Onde está o pedido PED-10001?", "e o outro pedido?"],
        gateway,
    )

    dialogue = [
        (message.type, message.text)
        for message in gateway.calls[1]
        if message.type != "system"
    ]
    assert dialogue == [
        ("human", "Onde está o pedido PED-10001?"),
        ("ai", "Preciso do seu CPF."),
        ("human", "e o outro pedido?"),
    ]


def test_it_should_send_the_stripped_line_to_the_gateway(monkeypatch):
    gateway = FakeGateway([SCRIPTED_REPLY])

    _run_chat(monkeypatch, ["  Olá, preciso de ajuda  "], gateway)

    dialogue = [
        (message.type, message.text)
        for message in gateway.calls[0]
        if message.type != "system"
    ]
    assert dialogue == [("human", "Olá, preciso de ajuda")]


def test_it_should_skip_blank_lines_without_calling_the_gateway(monkeypatch, capsys):
    gateway = FakeGateway([])

    _run_chat(monkeypatch, ["", "   \t "], gateway)

    assert gateway.calls == []
    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_one_printing_one_line_when_the_kit_is_missing(
    tmp_path, monkeypatch, capsys
):
    missing_kit = tmp_path / "missing-kit"
    monkeypatch.setenv("PET_HELPER_PROVIDER", "ollama")
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(missing_kit))
    _set_input_lines(monkeypatch, [])

    code = main(["chat"])

    captured = capsys.readouterr()
    combined = f"{captured.out}{captured.err}"
    non_empty_lines = [line for line in combined.splitlines() if line.strip()]
    assert code == 1
    assert len(non_empty_lines) == 1
    assert "Traceback" not in combined
    assert str(missing_kit.resolve()) in non_empty_lines[0]


def test_it_should_exit_with_zero_on_end_of_input(monkeypatch):
    gateway = FakeGateway([])

    code = _run_chat(monkeypatch, [], gateway)

    assert code == 0


def test_it_should_print_a_newline_on_end_of_input(monkeypatch, capsys):
    gateway = FakeGateway([])

    _run_chat(monkeypatch, [], gateway)

    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_130_on_keyboard_interrupt(monkeypatch):
    gateway = FakeGateway([])

    code = _run_chat(monkeypatch, [], gateway, exhausted=KeyboardInterrupt)

    assert code == 130


def test_it_should_print_a_newline_on_keyboard_interrupt(monkeypatch, capsys):
    gateway = FakeGateway([])

    _run_chat(monkeypatch, [], gateway, exhausted=KeyboardInterrupt)

    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_130_on_keyboard_interrupt_during_a_turn(monkeypatch):
    gateway = FakeGateway([KeyboardInterrupt()])

    code = _run_chat(monkeypatch, ["Olá"], gateway)

    assert code == 130


def test_it_should_print_a_newline_on_keyboard_interrupt_during_a_turn(
    monkeypatch, capsys
):
    gateway = FakeGateway([KeyboardInterrupt()])

    _run_chat(monkeypatch, ["Olá"], gateway)

    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_usage_error_when_no_command_is_given():
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2


def test_it_should_register_the_pet_helper_console_script():
    (entry_point,) = entry_points(group="console_scripts", name="pet-helper")

    assert entry_point.load() is main
