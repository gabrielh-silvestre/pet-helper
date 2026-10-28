import io
import sys

import pytest

from scripts.check_commit_msg import check, main


@pytest.mark.parametrize(
    "message",
    [
        "feat: add order lookup tool\n",
        "fix(rag): cite the policy source\n",
        "refactor!: split the order service\n",
        "docs: add glossary\n\n# Please enter the commit message\n",
        "chore: bump tools\n# ------------------------ >8 ------------------------\ndiff --git a b\n",
    ],
)
def test_it_should_accept_a_single_conventional_subject(message):
    assert check(message) is None


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("", "empty"),
        ("# only comments\n", "empty"),
        ("added order lookup\n", "subject must be"),
        ("style: reformat\n", "subject must be"),
        ("feat:missing space\n", "subject must be"),
        ("feat: " + "x" * 75 + "\n", "the limit is 80"),
        ("feat: add tool\n\nwhy we did it\n", "without a body"),
    ],
)
def test_it_should_reject_an_invalid_message(message, reason):
    assert reason in check(message)


def test_it_should_exit_with_error_and_explain_on_an_invalid_file(
    tmp_path, monkeypatch, capsys
):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("wip\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check", str(msg)])

    assert main() == 1
    assert "commit-msg:" in capsys.readouterr().err


def test_it_should_exit_cleanly_on_a_valid_file(tmp_path, monkeypatch):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("test: cover commit message check\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check", str(msg)])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert main() == 0
