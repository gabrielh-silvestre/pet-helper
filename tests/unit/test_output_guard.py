from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import pet_helper.conversation as conversation
from pet_helper.conversation import Assistant
from pet_helper.conversation.graph import HANDOFF_REPLY
from pet_helper.gateway import FakeGateway
from pet_helper.orders.api import FAILURE_ENV

REPO_KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SESSION_ID = "session-output-guard"
CUSTOMER_TEXT = "Quais produtos vocês recomendam?"
CLEAN_REPLY = "Recomendo a ração PX-0001."
INVALID_SKU = "PX-9999"
THINKING_BLOCKS = {
    "thinking": {"type": "thinking", "thinking": "Vou pensar.", "signature": "sig"},
    "redacted_thinking": {"type": "redacted_thinking", "data": "opaque"},
}
INVALID_REPLIES = {
    "invented_sku": f"Recomendo o produto {INVALID_SKU}.",
    "unretrieved_source": "Pode devolver em até 7 dias.\nFonte: Reembolso",
    "unknown_source": "Pode devolver em até 7 dias.\nFonte: Política de Teletransporte",
}


@pytest.fixture(autouse=True)
def _point_at_the_repo_kit(monkeypatch):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(REPO_KIT_DIR))
    monkeypatch.delenv(FAILURE_ENV, raising=False)


def _assistant(script: list[Any]) -> tuple[Assistant, FakeGateway]:
    gateway = FakeGateway(script)
    return conversation.build_assistant(gateway=gateway), gateway


def _state(assistant: Assistant) -> Mapping[str, Any]:
    return assistant.snapshot(SESSION_ID)


def _dialogue(assistant: Assistant) -> list[tuple[str, str]]:
    return [(message.type, message.text) for message in _state(assistant)["messages"]]


def _search_policies_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_policies",
                "args": {"query": "frete gratis"},
                "id": "call_1",
            }
        ],
    )


def _with_thinking(text: str, kind: str = "thinking") -> AIMessage:
    return AIMessage(content=[THINKING_BLOCKS[kind], {"type": "text", "text": text}])


def _search_policies_call_with_thinking() -> AIMessage:
    tool_use = {
        "type": "tool_use",
        "id": "call_1",
        "name": "search_policies",
        "input": {"query": "frete gratis"},
    }
    return AIMessage(
        content=[THINKING_BLOCKS["thinking"], tool_use],
        tool_calls=_search_policies_call().tool_calls,
    )


def _thinking_blocks(message: AIMessage) -> list[Any]:
    return [
        block
        for block in message.content
        if isinstance(block, dict) and block["type"] in THINKING_BLOCKS
    ]


def _assistant_messages(assistant: Assistant) -> list[AIMessage]:
    return [m for m in _state(assistant)["messages"] if isinstance(m, AIMessage)]


@pytest.mark.parametrize("invalid_reply", INVALID_REPLIES.values(), ids=INVALID_REPLIES)
def test_it_should_call_the_model_exactly_once_more_after_an_invalid_reply(
    invalid_reply,
):
    assistant, gateway = _assistant([invalid_reply, CLEAN_REPLY])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert len(gateway.calls) == 2


@pytest.mark.parametrize("invalid_reply", INVALID_REPLIES.values(), ids=INVALID_REPLIES)
def test_it_should_return_the_regenerated_reply_when_it_is_valid(invalid_reply):
    assistant, _ = _assistant([invalid_reply, CLEAN_REPLY])

    reply = assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert reply.text == CLEAN_REPLY
    assert reply.escalation is None


@pytest.mark.parametrize("invalid_reply", INVALID_REPLIES.values(), ids=INVALID_REPLIES)
def test_it_should_send_the_invalid_reply_and_a_corrective_message_on_the_regeneration_call(
    invalid_reply,
):
    assistant, gateway = _assistant([invalid_reply, CLEAN_REPLY])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    first_call, regeneration_call = gateway.calls
    assert len(regeneration_call) == len(first_call) + 2
    assert regeneration_call[-2].text == invalid_reply


def test_it_should_name_the_invalid_sku_in_the_corrective_message():
    assistant, gateway = _assistant([INVALID_REPLIES["invented_sku"], CLEAN_REPLY])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert INVALID_SKU in gateway.calls[1][-1].text


@pytest.mark.parametrize("invalid_reply", INVALID_REPLIES.values(), ids=INVALID_REPLIES)
def test_it_should_keep_only_the_customer_message_and_the_clean_reply_in_the_history_after_a_regeneration(
    invalid_reply,
):
    assistant, _ = _assistant([invalid_reply, CLEAN_REPLY])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert _dialogue(assistant) == [("human", CUSTOMER_TEXT), ("ai", CLEAN_REPLY)]


def test_it_should_remove_nothing_but_the_invalid_reply_and_the_corrective_message_from_earlier_history():
    assistant, _ = _assistant(
        ["Olá! Como posso ajudar?", INVALID_REPLIES["invented_sku"], CLEAN_REPLY]
    )
    assistant.turn(SESSION_ID, "Oi")
    history_before = _state(assistant)["messages"]

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    history_after = _state(assistant)["messages"]
    assert history_after[: len(history_before)] == history_before
    assert [(m.type, m.text) for m in history_after[len(history_before) :]] == [
        ("human", CUSTOMER_TEXT),
        ("ai", CLEAN_REPLY),
    ]


def test_it_should_leave_the_invalid_reply_and_the_corrective_message_out_of_the_next_turn_context():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, gateway = _assistant([invalid_reply, CLEAN_REPLY, "De nada!"])
    assistant.turn(SESSION_ID, CUSTOMER_TEXT)
    corrective_text = gateway.calls[1][-1].text

    assistant.turn(SESSION_ID, "Obrigado")

    next_turn_texts = [message.text for message in gateway.calls[2]]
    assert invalid_reply not in next_turn_texts
    assert corrective_text not in next_turn_texts


def test_it_should_accept_a_policy_retrieved_in_an_earlier_turn_of_the_session():
    assistant, _ = _assistant(
        [
            _search_policies_call(),
            "Frete grátis acima de R$ 199.\nFonte: Frete",
            INVALID_REPLIES["unretrieved_source"],
            "O frete grátis continua valendo.\nFonte: Frete",
        ]
    )
    assistant.turn(SESSION_ID, "Qual é a regra de frete grátis?")

    reply = assistant.turn(SESSION_ID, "E o prazo?")

    assert reply.text == "O frete grátis continua valendo.\nFonte: Frete"
    assert reply.escalation is None


def test_it_should_not_reject_a_sku_the_customer_typed_in_the_session():
    assistant, _ = _assistant(
        [
            f"O {INVALID_SKU} não existe, mas o PX-9998 sim.",
            f"O {INVALID_SKU} não existe no catálogo.",
        ]
    )

    reply = assistant.turn(SESSION_ID, f"O {INVALID_SKU} existe?")

    assert reply.text == f"O {INVALID_SKU} não existe no catálogo."
    assert reply.escalation is None


def test_it_should_leave_the_sku_the_customer_typed_out_of_the_corrective_message():
    assistant, gateway = _assistant(
        [
            f"O {INVALID_SKU} não existe, mas o PX-9998 sim.",
            f"O {INVALID_SKU} não existe no catálogo.",
        ]
    )

    assistant.turn(SESSION_ID, f"O {INVALID_SKU} existe?")

    corrective_text = gateway.calls[1][-1].text
    assert "PX-9998" in corrective_text
    assert INVALID_SKU not in corrective_text


def test_it_should_not_call_the_model_again_when_the_first_reply_is_valid():
    assistant, gateway = _assistant([CLEAN_REPLY])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert len(gateway.calls) == 1


@pytest.mark.parametrize("invalid_reply", INVALID_REPLIES.values(), ids=INVALID_REPLIES)
def test_it_should_escalate_with_repeated_failure_when_the_regenerated_reply_is_still_invalid(
    invalid_reply,
):
    assistant, _ = _assistant([invalid_reply, invalid_reply])

    reply = assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert reply.escalation == "repeated_failure"


def test_it_should_record_output_guard_as_the_escalation_cause_when_the_regeneration_is_still_invalid():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, _ = _assistant([invalid_reply, invalid_reply])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert _state(assistant)["escalation_cause"] == "output_guard"


def test_it_should_end_the_session_when_the_regeneration_is_still_invalid():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, _ = _assistant([invalid_reply, invalid_reply])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert _state(assistant)["ended"] is True


def test_it_should_reply_with_the_handoff_text_when_the_regeneration_is_still_invalid():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, _ = _assistant([invalid_reply, invalid_reply])

    reply = assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert reply.text == HANDOFF_REPLY


def test_it_should_not_regenerate_a_second_time_when_the_regeneration_is_still_invalid():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, gateway = _assistant([invalid_reply, invalid_reply])

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert len(gateway.calls) == 2


def test_it_should_allow_one_regeneration_in_each_turn():
    invalid_reply = INVALID_REPLIES["invented_sku"]
    assistant, gateway = _assistant([invalid_reply, CLEAN_REPLY] * 2)

    replies = [
        assistant.turn(SESSION_ID, CUSTOMER_TEXT),
        assistant.turn(SESSION_ID, "E outro produto?"),
    ]

    assert [reply.text for reply in replies] == [CLEAN_REPLY, CLEAN_REPLY]
    assert len(gateway.calls) == 4


@pytest.mark.parametrize("kind", THINKING_BLOCKS)
def test_it_should_drop_the_thinking_blocks_of_a_regenerated_reply_from_the_history(
    kind,
):
    assistant, _ = _assistant(
        [
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _with_thinking(CLEAN_REPLY, kind),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    (regenerated,) = _assistant_messages(assistant)
    assert regenerated.text == CLEAN_REPLY
    assert _thinking_blocks(regenerated) == []


def test_it_should_drop_the_thinking_blocks_of_every_assistant_message_after_the_corrective_message():
    assistant, _ = _assistant(
        [
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _search_policies_call_with_thinking(),
            _with_thinking(CLEAN_REPLY),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    tool_loop_message, final_message = _assistant_messages(assistant)
    assert _thinking_blocks(tool_loop_message) == []
    assert _thinking_blocks(final_message) == []


def test_it_should_keep_the_tool_calls_and_the_tool_use_block_of_a_message_stripped_of_thinking():
    assistant, _ = _assistant(
        [
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _search_policies_call_with_thinking(),
            _with_thinking(CLEAN_REPLY),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    tool_loop_message, _ = _assistant_messages(assistant)
    assert tool_loop_message.tool_calls == _search_policies_call().tool_calls
    assert [block["type"] for block in tool_loop_message.content] == ["tool_use"]


def test_it_should_keep_the_message_order_after_stripping_thinking_from_the_tool_loop():
    assistant, _ = _assistant(
        [
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _search_policies_call_with_thinking(),
            _with_thinking(CLEAN_REPLY),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    assert [message.type for message in _state(assistant)["messages"]] == [
        "human",
        "ai",
        "tool",
        "ai",
    ]


def test_it_should_keep_the_thinking_of_the_tool_loop_until_the_regeneration_ends():
    assistant, gateway = _assistant(
        [
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _search_policies_call_with_thinking(),
            _with_thinking(CLEAN_REPLY),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    tool_loop_message_seen_by_the_model = gateway.calls[2][-2]
    assert _thinking_blocks(tool_loop_message_seen_by_the_model) != []


def test_it_should_keep_the_thinking_of_a_message_that_precedes_the_invalid_reply_in_the_same_turn():
    assistant, _ = _assistant(
        [
            _search_policies_call_with_thinking(),
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _with_thinking(CLEAN_REPLY),
        ]
    )

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    earlier_message, _ = _assistant_messages(assistant)
    assert _thinking_blocks(earlier_message) != []


def test_it_should_keep_the_thinking_of_the_earlier_turns_of_the_session():
    assistant, _ = _assistant(
        [
            _with_thinking("Olá! Como posso ajudar?"),
            _with_thinking(INVALID_REPLIES["invented_sku"]),
            _with_thinking(CLEAN_REPLY),
        ]
    )
    assistant.turn(SESSION_ID, "Oi")

    assistant.turn(SESSION_ID, CUSTOMER_TEXT)

    first_turn_message, _ = _assistant_messages(assistant)
    assert _thinking_blocks(first_turn_message) != []
