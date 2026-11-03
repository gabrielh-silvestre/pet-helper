"""The `system_prompt` seam of `build_assistant` (no model, no network).

Assumed surface: `build_assistant(..., system_prompt: str | None = None)`. `None` keeps the module
`SYSTEM_PROMPT` byte for byte; a string replaces it for that assistant only, never the module constant.
"""

from pathlib import Path

from langchain_core.messages import SystemMessage

from pet_helper.conversation import build_assistant, system_prompt_sha256
from pet_helper.conversation.prompt import SYSTEM_PROMPT
from pet_helper.gateway import FakeGateway

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"


def _first_system_message(gateway: FakeGateway) -> SystemMessage:
    message = gateway.calls[0][0]
    assert isinstance(message, SystemMessage)
    return message


def _ask_once(**options) -> FakeGateway:
    gateway = FakeGateway(["Olá!"])
    build_assistant(gateway=gateway, kit_dir=REPO_KIT_DIR, **options).turn("s1", "Olá")
    return gateway


def test_it_should_send_the_module_prompt_when_no_system_prompt_is_given():
    gateway = _ask_once()

    assert _first_system_message(gateway).content == SYSTEM_PROMPT


def test_it_should_send_the_module_prompt_when_the_system_prompt_is_none():
    gateway = _ask_once(system_prompt=None)

    assert _first_system_message(gateway).content == SYSTEM_PROMPT


def test_it_should_send_the_given_system_prompt():
    gateway = _ask_once(system_prompt="Prompt candidato de teste")

    assert _first_system_message(gateway).content == "Prompt candidato de teste"


def test_it_should_keep_the_prompt_hash_unchanged_after_building_with_a_given_prompt():
    before = system_prompt_sha256()

    _ask_once(system_prompt="Prompt candidato de teste")

    assert system_prompt_sha256() == before


def test_it_should_keep_the_module_prompt_for_the_next_assistant_after_a_given_prompt():
    _ask_once(system_prompt="Prompt candidato de teste")

    gateway = _ask_once()

    assert _first_system_message(gateway).content == SYSTEM_PROMPT
