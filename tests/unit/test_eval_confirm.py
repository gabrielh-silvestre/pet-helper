import io

import pytest
from pet_helper.evals.confirm import confirm

PROMPT = "This round may cost up to US$ 0.30. Continue? [yes/no] "


def _answer(monkeypatch, typed: str) -> bool:
    monkeypatch.setattr("sys.stdin", io.StringIO(typed))
    return confirm(PROMPT)


@pytest.mark.parametrize("typed", ["yes\n", "y\n", "YES\n", "  yes  \n"])
def test_it_should_confirm_on_an_explicit_yes(monkeypatch, typed):
    assert _answer(monkeypatch, typed) is True


@pytest.mark.parametrize("typed", ["\n", "no\n", "n\n", "maybe\n", "ok\n", "yess\n"])
def test_it_should_refuse_on_an_empty_line_or_any_other_text(monkeypatch, typed):
    assert _answer(monkeypatch, typed) is False


def test_it_should_refuse_at_end_of_input(monkeypatch):
    assert _answer(monkeypatch, "") is False


def test_it_should_show_the_prompt_before_reading(monkeypatch, capsys):
    _answer(monkeypatch, "yes\n")

    captured = capsys.readouterr()
    assert PROMPT.strip() in captured.out + captured.err
