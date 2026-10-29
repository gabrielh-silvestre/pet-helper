from importlib.metadata import entry_points

import pytest

import pet_helper.cli
from pet_helper.cli import main
from pet_helper.gateway import DEFAULT_REPLY, FakeGateway

SCRIPTED_REPLY = "scripted by the test"


def _run_chat(monkeypatch, lines, gateway, exhausted=EOFError):
    """Feeds `lines` to input() and raises `exhausted` when they run out."""
    pending = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(pending)
        except StopIteration:
            raise exhausted from None

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(pet_helper.cli, "default_gateway", lambda: gateway)
    try:
        return main(["chat"])
    except KeyboardInterrupt:
        # an escaped interrupt would abort the whole pytest session
        pytest.fail("KeyboardInterrupt escaped main()")


def test_it_should_print_the_reply_for_each_line_with_text(monkeypatch, capsys):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])
    assert SCRIPTED_REPLY != DEFAULT_REPLY

    _run_chat(monkeypatch, ["hello", "again"], gateway)

    assert capsys.readouterr().out == f"{SCRIPTED_REPLY}\n{SCRIPTED_REPLY}\n\n"


def test_it_should_send_the_stripped_line_to_the_gateway(monkeypatch):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    _run_chat(monkeypatch, ["  hello  "], gateway)

    assert [(m.type, m.content) for m in gateway.calls[0]] == [("human", "hello")]


def test_it_should_skip_blank_lines_without_calling_the_gateway(monkeypatch, capsys):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    _run_chat(monkeypatch, ["", "   \t "], gateway)

    assert gateway.calls == []
    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_zero_on_end_of_input(monkeypatch):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    code = _run_chat(monkeypatch, [], gateway)

    assert code == 0


def test_it_should_print_a_newline_on_end_of_input(monkeypatch, capsys):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    _run_chat(monkeypatch, [], gateway)

    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_130_on_keyboard_interrupt(monkeypatch):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    code = _run_chat(monkeypatch, [], gateway, exhausted=KeyboardInterrupt)

    assert code == 130


def test_it_should_print_a_newline_on_keyboard_interrupt(monkeypatch, capsys):
    gateway = FakeGateway(replies=[SCRIPTED_REPLY])

    _run_chat(monkeypatch, [], gateway, exhausted=KeyboardInterrupt)

    assert capsys.readouterr().out == "\n"


def test_it_should_exit_with_usage_error_when_no_command_is_given():
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2


def test_it_should_register_the_pet_helper_console_script():
    (entry_point,) = entry_points(group="console_scripts", name="pet-helper")

    assert entry_point.load() is main
