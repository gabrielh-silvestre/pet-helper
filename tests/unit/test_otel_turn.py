"""The turn root span, the domain events on it and the model span fed by the handler."""

import json

import pytest

from pet_helper.conversation import build_assistant
from pet_helper.gateway import FakeGateway
from pet_helper.trace import Tracer
from tests.unit.otel_fixtures import (
    KIT_DIR,
    PII_TEXT,
    POLICY_REPLY,
    RAW_PII,
    SESSION_ID,
    all_strings,
    attribute,
    descends_from,
    event,
    event_names,
    file_lines,
    finish,
    mask,
    model_spans,
    roots,
    run_turns,
    span_file,
    traced,
)
from tests.unit.otel_support import ScriptedGateway, tool_call

pytest_plugins = ["tests.unit.otel_fixtures"]
pytestmark = pytest.mark.usefixtures("_otel_clean")

OWNER_TEXT = "Meu CPF é 000.000.001-91."
OTHER_TEXT = "Meu CPF é 000.000.002-72."


def _policy_gateway() -> ScriptedGateway:
    return ScriptedGateway(
        [tool_call("search_policies", {"query": "devolução de ração"}), POLICY_REPLY]
    )


def _policy_turn(tmp_path, text: str = "Como devolvo uma ração?"):
    return run_turns(tmp_path, _policy_gateway(), text)


# turn root span


def test_it_should_open_one_root_span_per_turn(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá!", "De nada!"]), "Oi", "Obrigado")

    assert len(roots(spans)) == 2


def test_it_should_tag_the_root_span_with_the_session_and_the_turn_number(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá!", "De nada!"]), "Oi", "Obrigado")

    assert [
        (attribute(root, "session_id"), attribute(root, "turn"))
        for root in roots(spans)
    ] == [(SESSION_ID, 1), (SESSION_ID, 2)]


def test_it_should_record_the_customer_message_and_the_reply_on_the_root_span(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Quero ajuda")

    (root,) = roots(spans)
    assert (
        "Quero ajuda" in attribute(root, "gen_ai.input.messages"),
        "Olá, tudo bem!" in attribute(root, "gen_ai.output.messages"),
    ) == (True, True)


def test_it_should_mask_the_customer_message_on_the_root_span(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Certo."]), f"Oi, {PII_TEXT}")

    (root,) = roots(spans)
    assert [pii for pii in RAW_PII if pii in "\n".join(all_strings(root))] == []


def test_it_should_keep_only_the_sizes_on_the_root_span_when_content_is_off(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PET_HELPER_TRACE_CONTENT", "0")

    spans = run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Quero ajuda")

    (root,) = roots(spans)
    assert "Quero ajuda" not in "\n".join(all_strings(root))


def test_it_should_end_the_root_span_when_the_turn_is_interrupted(tmp_path):
    tracer, memory = traced(tmp_path)
    assistant = build_assistant(
        gateway=FakeGateway([KeyboardInterrupt()]), tracer=tracer, kit_dir=KIT_DIR
    )

    with pytest.raises(KeyboardInterrupt):
        assistant.turn(SESSION_ID, "Olá")

    assert len(roots(finish(tracer, memory))) == 1


# domain events


def test_it_should_record_the_domain_events_of_a_policy_turn_on_the_root_span(tmp_path):
    (root,) = roots(_policy_turn(tmp_path))

    names = event_names(root)
    assert [
        kind for kind in ("guard.input", "retrieval", "guard.output") if kind in names
    ] == [
        "guard.input",
        "retrieval",
        "guard.output",
    ]


def test_it_should_carry_the_event_fields_as_event_attributes(tmp_path):
    (root,) = roots(_policy_turn(tmp_path))

    retrieval = event(root, "retrieval")
    assert retrieval.attributes["gen_ai.retrieval.query.text"] == "devolução de ração"


def test_it_should_mirror_a_domain_event_in_a_langsmith_metadata_attribute(tmp_path):
    (root,) = roots(_policy_turn(tmp_path))

    assert "devolução de ração" in attribute(root, "langsmith.metadata.retrieval")


def test_it_should_mask_the_pii_of_a_domain_event_and_of_its_mirror(tmp_path):
    gateway = ScriptedGateway(
        [
            tool_call("search_policies", {"query": PII_TEXT}),
            "Seus dados ficam protegidos.\nFonte: Privacidade e dados pessoais",
        ]
    )

    (root,) = roots(run_turns(tmp_path, gateway, "Como devolvo uma ração?"))

    assert [pii for pii in RAW_PII if pii in "\n".join(all_strings(root))] == []


@pytest.mark.parametrize("kind", ["model.call", "tool.call"])
def test_it_should_not_add_model_and_tool_calls_as_root_events(tmp_path, kind):
    (root,) = roots(_policy_turn(tmp_path))

    assert kind not in event_names(root)


def test_it_should_record_the_escalation_with_its_reason(tmp_path):
    gateway = ScriptedGateway(
        [tool_call("escalate_to_human", {"reason": "explicit_request"})]
    )

    (root,) = roots(run_turns(tmp_path, gateway, "Quero falar com um atendente."))

    assert (
        event(root, "escalation").attributes["pet_helper.escalation_reason"]
        == "explicit_request"
    )


@pytest.mark.parametrize(
    ("order_number", "text", "reason"),
    [
        ("PED-10001", OTHER_TEXT, "other_customer"),
        ("PED-99999", OWNER_TEXT, "not_found"),
    ],
)
def test_it_should_record_the_internal_reason_of_a_refused_order_lookup(
    tmp_path, order_number, text, reason
):
    gateway = ScriptedGateway(
        [tool_call("get_order", {"order_number": order_number}), "Certo."]
    )

    (root,) = roots(
        run_turns(tmp_path, gateway, f"{text} Consulte o pedido {order_number}.")
    )

    assert event(root, "order.refused").attributes["pet_helper.reason"] == reason


def test_it_should_not_carry_the_events_of_a_turn_into_the_next_one(tmp_path):
    gateway = ScriptedGateway(
        [
            tool_call("search_policies", {"query": "devolução de ração"}),
            POLICY_REPLY,
            "De nada!",
        ]
    )

    first, second = roots(run_turns(tmp_path, gateway, "Como devolvo?", "Obrigado"))

    assert ("retrieval" in event_names(first), "retrieval" in event_names(second)) == (
        True,
        False,
    )


# model span


def test_it_should_create_one_model_span_per_model_call(tmp_path):
    spans = _policy_turn(tmp_path)

    assert len(model_spans(spans)) == 2


def test_it_should_create_the_model_spans_under_the_root_span(tmp_path):
    spans = _policy_turn(tmp_path)

    (root,) = roots(spans)
    assert [descends_from(span, root, spans) for span in model_spans(spans)] == [
        True,
        True,
    ]


def test_it_should_name_the_model_of_the_call_on_the_model_span(tmp_path):
    spans = _policy_turn(tmp_path)

    assert {attribute(span, "gen_ai.request.model") for span in model_spans(spans)} == {
        "claude-haiku-5-5"
    }


def test_it_should_record_the_input_and_output_tokens_on_the_model_span(tmp_path):
    spans = _policy_turn(tmp_path)

    assert {
        (
            attribute(span, "gen_ai.usage.input_tokens"),
            attribute(span, "gen_ai.usage.output_tokens"),
        )
        for span in model_spans(spans)
    } == {(40, 12)}


def test_it_should_record_the_cost_and_its_mirror_on_the_model_span(tmp_path):
    spans = _policy_turn(tmp_path)

    span = model_spans(spans)[0]
    assert attribute(span, "pet_helper.cost_usd") > 0
    assert attribute(span, "langsmith.metadata.cost_usd") == attribute(
        span, "pet_helper.cost_usd"
    )
    assert attribute(span, "pet_helper.price_unknown") is False


def test_it_should_flag_a_streamed_call_on_the_model_span(tmp_path):
    spans = _policy_turn(tmp_path)

    assert attribute(model_spans(spans)[0], "pet_helper.streaming") is True


def test_it_should_record_the_cache_tokens_on_the_model_span(tmp_path):
    spans = _policy_turn(tmp_path)

    span = model_spans(spans)[0]
    assert (
        attribute(span, "gen_ai.usage.cache_read.input_tokens"),
        attribute(span, "gen_ai.usage.cache_creation.input_tokens"),
    ) == (5, 10)


# time to first token


def test_it_should_record_the_time_to_first_token_of_a_streamed_text_reply(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Oi")

    assert attribute(model_spans(spans)[0], "gen_ai.server.time_to_first_token") > 0


def test_it_should_mirror_the_time_to_first_token_in_langsmith_metadata(tmp_path):
    spans = run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Oi")

    span = model_spans(spans)[0]
    assert attribute(span, "langsmith.metadata.ttft") == attribute(
        span, "gen_ai.server.time_to_first_token"
    )


def test_it_should_leave_the_time_to_first_token_out_without_streaming(tmp_path):
    spans = run_turns(
        tmp_path, ScriptedGateway(["Olá, tudo bem!"], streaming=False), "Oi"
    )

    assert attribute(model_spans(spans)[0], "gen_ai.server.time_to_first_token") is None


def test_it_should_keep_the_first_chunk_time_in_the_jsonl_sink(tmp_path):
    run_turns(tmp_path, ScriptedGateway(["Olá, tudo bem!"]), "Oi")

    (call,) = [
        line
        for line in file_lines(tmp_path / f"{SESSION_ID}.jsonl")
        if line["event"] == "model.call"
    ]
    assert call["gen_ai.response.time_to_first_chunk"] > 0


# whole turn


def test_it_should_leave_every_span_of_a_turn_under_its_root_span(tmp_path):
    spans = _policy_turn(tmp_path)

    (root,) = roots(spans)
    others = [span for span in spans if span is not root]
    assert (len(others) > 0, all(descends_from(s, root, spans) for s in others)) == (
        True,
        True,
    )


def test_it_should_leave_no_raw_pii_in_any_span_of_a_turn(tmp_path):
    gateway = ScriptedGateway(
        [tool_call("search_policies", {"query": PII_TEXT}), f"Certo. {PII_TEXT}"]
    )

    spans = run_turns(tmp_path, gateway, f"Oi, {PII_TEXT}")

    written = "\n".join(text for span in spans for text in all_strings(span))
    assert [pii for pii in RAW_PII if pii in written] == []


# the turn messages on the root span

PLANTED_WORD = "SEGREDO"


def _root_line(tmp_path, text: str, *, masking=None) -> dict:
    tracer = Tracer(tmp_path, mask=masking or mask())
    assistant = build_assistant(
        gateway=ScriptedGateway(["Olá!"]), tracer=tracer, kit_dir=KIT_DIR
    )
    assistant.turn(SESSION_ID, text)
    tracer.shutdown()
    return next(
        line for line in file_lines(span_file(tmp_path)) if line["parent_id"] is None
    )


def test_it_should_not_leave_raw_pii_in_the_input_message_of_the_span_file(tmp_path):
    root = _root_line(tmp_path, f"Oi, {PII_TEXT}")

    message = root["attributes"]["gen_ai.input.messages"]
    assert [pii for pii in RAW_PII if pii in message] == []


def test_it_should_mask_the_input_message_of_the_span_file_once(tmp_path):
    def mark(text: str) -> str:
        return text.replace(PLANTED_WORD, f"[{PLANTED_WORD}-MASKED]")

    root = _root_line(tmp_path, f"meu código {PLANTED_WORD} aqui", masking=mark)

    (message,) = json.loads(root["attributes"]["gen_ai.input.messages"])
    assert message["content"] == f"meu código [{PLANTED_WORD}-MASKED] aqui"
