import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from opentelemetry import trace

from pet_helper.catalog import Catalog
from pet_helper.conversation import KitError, build_assistant
from pet_helper.gateway import FakeGateway
from pet_helper.guard import Guard
from pet_helper.orders.api import FAILURE_ENV
from pet_helper.trace import Tracer, emit, turn_scope

KIT_DIR = Path(__file__).resolve().parents[2] / "kit"
SESSION_ID = "session-trace"
DOMAIN_KINDS = {"turn.start", "guard.input", "retrieval", "guard.output", "turn.end"}
OWNER_TEXT = "Meu CPF é 000.000.001-91."
OTHER_TEXT = "Meu CPF é 000.000.002-72."
POLICY_REPLY = "Você pode devolver em 7 dias.\nFonte: Troca e devolução"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in (
        "PET_HELPER_KIT_DIR",
        "PET_HELPER_TODAY",
        "PET_HELPER_TRACE_CONTENT",
        FAILURE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def _tool_call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": "call_1"}]
    )


def _tracer(trace_dir: Path) -> Tracer:
    return Tracer(trace_dir, mask=Guard(Catalog(KIT_DIR / "catalog.csv")).mask)


def _events(trace_dir: Path, session_id: str = SESSION_ID) -> list[dict]:
    lines = (trace_dir / f"{session_id}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _kinds(events: list[dict]) -> list[str]:
    return [event["event"] for event in events]


def _event(events: list[dict], kind: str) -> dict:
    return next(event for event in events if event["event"] == kind)


def _policy_turn(trace_dir: Path, text: str = "Como devolvo uma ração?") -> list[dict]:
    gateway = FakeGateway(
        [
            _tool_call("search_policies", {"query": "devolução de ração"}),
            POLICY_REPLY,
        ]
    )
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(trace_dir), kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, text)

    return _events(trace_dir)


def _order_result(order_number: str, text: str, **build_kwargs: Any) -> dict:
    gateway = FakeGateway(
        [_tool_call("get_order", {"order_number": order_number}), "Certo."]
    )
    assistant = build_assistant(gateway=gateway, kit_dir=KIT_DIR, **build_kwargs)

    assistant.turn(SESSION_ID, text)

    return json.loads(gateway.calls[1][-1].content)


def test_it_should_emit_the_domain_events_of_a_policy_turn_in_order(tmp_path):
    events = _policy_turn(tmp_path)

    assert [kind for kind in _kinds(events) if kind in DOMAIN_KINDS] == [
        "turn.start",
        "guard.input",
        "retrieval",
        "guard.output",
        "turn.end",
    ]


def test_it_should_not_write_the_raw_cpf_anywhere_in_the_trace(tmp_path):
    _policy_turn(tmp_path, text=f"{OWNER_TEXT} Como devolvo uma ração?")

    written = (tmp_path / f"{SESSION_ID}.jsonl").read_text(encoding="utf-8")
    assert "000.000.001-91" not in written
    assert "00000000191" not in written


def test_it_should_write_the_cpf_pseudonym_in_turn_start(tmp_path):
    events = _policy_turn(tmp_path, text=f"{OWNER_TEXT} Como devolvo uma ração?")

    assert "cpf#" in json.dumps(_event(events, "turn.start"))


def test_it_should_record_the_retrieval_query(tmp_path):
    events = _policy_turn(tmp_path)

    retrieval = _event(events, "retrieval")
    assert retrieval["gen_ai.retrieval.query.text"] == "devolução de ração"


def test_it_should_record_the_source_and_score_of_each_retrieval_hit(tmp_path):
    events = _policy_turn(tmp_path)

    first_hit = _event(events, "retrieval")["pet_helper.hits"][0]
    assert first_hit["source"] == "troca-e-devolucao.md"
    assert first_hit["score"] > 0


def test_it_should_record_the_cited_sources_in_turn_end(tmp_path):
    events = _policy_turn(tmp_path)

    assert _event(events, "turn.end")["pet_helper.sources"] == ["troca-e-devolucao.md"]


def test_it_should_write_the_reply_masked_in_turn_end(tmp_path):
    gateway = FakeGateway(["Escreva para maria.silva@example.com, por favor."])
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, "Qual o contato?")

    written = (tmp_path / f"{SESSION_ID}.jsonl").read_text(encoding="utf-8")
    assert "maria.silva@example.com" not in written


def test_it_should_not_emit_escalation_on_a_policy_turn(tmp_path):
    events = _policy_turn(tmp_path)

    assert "escalation" not in _kinds(events)


def test_it_should_emit_escalation_with_its_reason_on_an_escalated_turn(tmp_path):
    gateway = FakeGateway(
        [_tool_call("escalate_to_human", {"reason": "explicit_request"})]
    )
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, "Quero falar com um atendente.")

    escalation = _event(_events(tmp_path), "escalation")
    assert escalation["pet_helper.escalation_reason"] == "explicit_request"


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
    gateway = FakeGateway(
        [_tool_call("get_order", {"order_number": order_number}), "Certo."]
    )
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, f"{text} Consulte o pedido {order_number}.")

    reasons = [
        event["pet_helper.reason"]
        for event in _events(tmp_path)
        if "pet_helper.reason" in event
    ]
    assert reasons == [reason]


@pytest.mark.parametrize(
    ("order_number", "text"),
    [("PED-10001", OTHER_TEXT), ("PED-99999", OWNER_TEXT)],
)
def test_it_should_show_the_model_a_plain_not_found_for_a_refused_order_lookup(
    tmp_path, order_number, text
):
    visible = _order_result(order_number, text, tracer=_tracer(tmp_path))

    assert visible == {"status": "not_found"}


@pytest.mark.parametrize(
    ("order_number", "text"),
    [("PED-10001", OTHER_TEXT), ("PED-99999", OWNER_TEXT)],
)
def test_it_should_show_the_model_the_same_order_result_with_and_without_a_tracer(
    tmp_path, order_number, text
):
    traced = _order_result(order_number, text, tracer=_tracer(tmp_path))

    untraced = _order_result(order_number, text)

    assert traced == untraced


def test_it_should_increment_the_turn_for_each_customer_turn(tmp_path):
    gateway = FakeGateway(["Olá!", "De nada!"])
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn(SESSION_ID, "Oi")
    assistant.turn(SESSION_ID, "Obrigado")

    starts = [e for e in _events(tmp_path) if e["event"] == "turn.start"]
    assert [event["turn"] for event in starts] == [1, 2]


def test_it_should_tag_every_event_of_a_turn_with_that_turn_number(tmp_path):
    events = _policy_turn(tmp_path)

    assert {event["turn"] for event in events} == {1}


def test_it_should_keep_the_turn_counters_of_two_sessions_apart(tmp_path):
    gateway = FakeGateway(["Olá A!", "Olá B!", "De novo A!"])
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn("session-a", "Oi A")
    assistant.turn("session-b", "Oi B")
    assistant.turn("session-a", "Oi de novo A")

    turns_a = [
        e["turn"] for e in _events(tmp_path, "session-a") if e["event"] == "turn.start"
    ]
    turns_b = [
        e["turn"] for e in _events(tmp_path, "session-b") if e["event"] == "turn.start"
    ]
    assert (turns_a, turns_b) == ([1, 2], [1])


def test_it_should_write_only_its_own_session_id_in_each_session_file(tmp_path):
    gateway = FakeGateway(["Olá A!", "Olá B!"])
    assistant = build_assistant(
        gateway=gateway, tracer=_tracer(tmp_path), kit_dir=KIT_DIR
    )

    assistant.turn("session-a", "Oi A")
    assistant.turn("session-b", "Oi B")

    assert {e["session_id"] for e in _events(tmp_path, "session-a")} == {"session-a"}
    assert {e["session_id"] for e in _events(tmp_path, "session-b")} == {"session-b"}


def test_it_should_use_the_given_date_for_days_since_delivery():
    result = _order_result("PED-10001", OWNER_TEXT, today=date(2026, 11, 10))

    assert result["order"]["days_since_delivery"] == 10


def test_it_should_prefer_the_given_date_over_the_process_clock(monkeypatch):
    monkeypatch.setenv("PET_HELPER_TODAY", "2026-12-25")

    result = _order_result("PED-10001", OWNER_TEXT, today=date(2026, 11, 10))

    assert result["order"]["days_since_delivery"] == 10


def test_it_should_keep_the_dates_of_two_assistants_apart_in_one_process():
    gateways = [
        FakeGateway([_tool_call("get_order", {"order_number": "PED-10001"}), "Ok."])
        for _ in range(2)
    ]
    early = build_assistant(
        gateway=gateways[0], kit_dir=KIT_DIR, today=date(2026, 11, 10)
    )
    late = build_assistant(
        gateway=gateways[1], kit_dir=KIT_DIR, today=date(2026, 11, 20)
    )

    early.turn(SESSION_ID, OWNER_TEXT)
    late.turn(SESSION_ID, OWNER_TEXT)

    days = [
        json.loads(gateway.calls[1][-1].content)["order"]["days_since_delivery"]
        for gateway in gateways
    ]
    assert days == [10, 20]


def test_it_should_use_the_process_clock_when_no_date_is_given(monkeypatch):
    monkeypatch.setenv("PET_HELPER_TODAY", "2026-11-12")

    result = _order_result("PED-10001", OWNER_TEXT)

    assert result["order"]["days_since_delivery"] == 12


def test_it_should_load_the_kit_from_the_given_directory_over_the_environment(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(tmp_path / "missing"))
    assistant = build_assistant(gateway=FakeGateway(["Olá!"]), kit_dir=KIT_DIR)

    reply = assistant.turn(SESSION_ID, "Oi")

    assert reply.text == "Olá!"


def test_it_should_fail_on_an_incomplete_given_kit_even_when_the_environment_is_good(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PET_HELPER_KIT_DIR", str(KIT_DIR))

    with pytest.raises(KitError):
        build_assistant(gateway=FakeGateway(), kit_dir=tmp_path)


class _EventOnlyPort:
    def __init__(self) -> None:
        self.events: list[str] = []

    def event(self, kind: str, *, session_id: str, turn: int, **fields: Any) -> None:
        self.events.append(kind)


class _ShutdownPort(_EventOnlyPort):
    def __init__(self) -> None:
        super().__init__()
        self.shutdowns = 0

    def shutdown(self) -> None:
        self.shutdowns += 1


def test_it_should_shut_down_the_tracer_port_when_the_assistant_closes():
    port = _ShutdownPort()
    assistant = build_assistant(gateway=FakeGateway([]), tracer=port, kit_dir=KIT_DIR)

    assistant.close()

    assert port.shutdowns == 1


def test_it_should_run_a_turn_without_a_span_when_the_port_only_has_events():
    port = _EventOnlyPort()

    with turn_scope(port, SESSION_ID, 1):
        emit("turn.start", **{"gen_ai.input.messages": "oi"})
        span_is_recording = trace.get_current_span().is_recording()

    assert (port.events, span_is_recording) == (["turn.start"], False)
