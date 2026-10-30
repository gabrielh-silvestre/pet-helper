import re
from pathlib import Path

import pytest

GLOSSARY_PATH = Path(__file__).resolve().parents[2] / "GLOSSARY.md"


@pytest.fixture(scope="module")
def escalation_entry() -> str:
    glossary = GLOSSARY_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^\*\*Escalation\*\*.*?(?=\n\n)", glossary, re.DOTALL | re.MULTILINE
    )
    assert match is not None
    return match.group(0)


def test_it_should_say_the_conversation_ends_after_the_handoff_in_the_escalation_entry(
    escalation_entry,
):
    assert re.search(r"conversation (?:\w+ )?ends", escalation_entry)


def test_it_should_not_say_the_assistant_stops_answering_on_that_matter_in_the_escalation_entry(
    escalation_entry,
):
    assert "stops answering on that matter" not in escalation_entry
