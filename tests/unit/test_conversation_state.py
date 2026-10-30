from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import pytest

import pet_helper.conversation as conversation
from pet_helper.catalog import Catalog
from pet_helper.gateway import FakeGateway
from pet_helper.guard import Guard
from pet_helper.knowledge import PolicyHit

CATALOG_CSV = Path(__file__).resolve().parents[2] / "kit" / "catalog.csv"

REQUIRED_STATE_FIELDS = {
    "messages",
    "user_text",
    "session_cpf",
    "failures",
    "retrieved",
    "escalation",
    "escalation_cause",
    "ended",
    "tool_rounds",
    "regen_used",
    "steps_counted",
    "injection_suspected",
}


def _reducer_for(field_name: str) -> Callable[[Any, Any], Any]:
    hints = get_type_hints(conversation.ConversationState, include_extras=True)
    annotation = hints[field_name]
    metadata = getattr(annotation, "__metadata__", ())
    assert metadata, f"{field_name} has no reducer metadata on ConversationState"
    reducer = metadata[0]
    assert callable(reducer)
    return reducer


def _retrieved_sequence(
    items: list[PolicyHit],
) -> tuple[PolicyHit, ...] | list[PolicyHit]:
    hints = get_type_hints(conversation.ConversationState, include_extras=True)
    value_type = get_args(hints["retrieved"])[0]
    if get_origin(value_type) is tuple:
        return tuple(items)
    return list(items)


def test_it_should_declare_every_required_field_on_conversation_state():
    hints = get_type_hints(conversation.ConversationState, include_extras=True)

    declared_fields = set(hints)

    assert declared_fields >= REQUIRED_STATE_FIELDS


def test_it_should_bind_session_cpf_when_no_cpf_is_set_yet():
    reduce_cpf = _reducer_for("session_cpf")

    bound = reduce_cpf(None, "52998224725")

    assert bound == "52998224725"


def test_it_should_keep_the_first_session_cpf_when_a_different_cpf_arrives():
    reduce_cpf = _reducer_for("session_cpf")

    bound = reduce_cpf("52998224725", "11144477735")

    assert bound == "52998224725"


def test_it_should_preserve_session_cpf_when_a_later_update_carries_none():
    reduce_cpf = _reducer_for("session_cpf")

    bound = reduce_cpf("52998224725", None)

    assert bound == "52998224725"


def test_it_should_form_the_union_of_distinct_retrieved_policy_hits():
    reduce_retrieved = _reducer_for("retrieved")
    frete = PolicyHit(
        source="frete.md",
        title="Frete",
        text="# Frete\n\nFrete grátis acima do mínimo.",
        score=2.1,
    )
    reembolso = PolicyHit(
        source="reembolso.md",
        title="Reembolso",
        text="# Reembolso\n\nEstorno no Pix em cinco dias úteis.",
        score=1.8,
    )

    combined = reduce_retrieved(
        _retrieved_sequence([frete]),
        _retrieved_sequence([reembolso]),
    )

    assert [hit.source for hit in combined] == ["frete.md", "reembolso.md"]


def test_it_should_deduplicate_retrieved_policy_hits_sharing_the_same_source():
    reduce_retrieved = _reducer_for("retrieved")
    first_hit = PolicyHit(
        source="troca-e-devolucao.md",
        title="Troca e devolução",
        text="# Troca e devolução\n\nPrazo de sete dias corridos.",
        score=2.5,
    )
    repeated_hit = PolicyHit(
        source="troca-e-devolucao.md",
        title="Troca e devolução",
        text="# Troca e devolução\n\nPrazo de sete dias corridos.",
        score=1.4,
    )
    second_hit = PolicyHit(
        source="reembolso.md",
        title="Reembolso",
        text="# Reembolso\n\nEstorno na forma original.",
        score=1.9,
    )

    combined = reduce_retrieved(
        _retrieved_sequence([first_hit]),
        _retrieved_sequence([repeated_hit, second_hit]),
    )

    assert [hit.source for hit in combined] == [
        "troca-e-devolucao.md",
        "reembolso.md",
    ]


def test_it_should_set_escalation_on_the_first_reason():
    reduce_escalation = _reducer_for("escalation")

    recorded = reduce_escalation(None, "explicit_request")

    assert recorded == "explicit_request"


@pytest.mark.parametrize("later_update", ["explicit_request", None])
def test_it_should_keep_the_initial_escalation_reason_on_subsequent_updates(
    later_update,
):
    reduce_escalation = _reducer_for("escalation")

    recorded = reduce_escalation("animal_health_question", later_update)

    assert recorded == "animal_health_question"


def test_it_should_set_escalation_cause_once_without_overwriting():
    reduce_cause = _reducer_for("escalation_cause")

    initial = reduce_cause(None, "first_cause")
    after_second = reduce_cause(initial, "second_cause")
    after_none = reduce_cause(after_second, None)

    assert initial == "first_cause"
    assert after_second == "first_cause"
    assert after_none == "first_cause"


def test_it_should_increment_a_step_failure_at_most_once_per_turn():
    failures, steps_counted = conversation.record_step_outcome(
        {"identification": 1},
        frozenset(),
        "order_lookup",
        succeeded=False,
    )

    failures, steps_counted = conversation.record_step_outcome(
        failures,
        steps_counted,
        "order_lookup",
        succeeded=False,
    )

    assert failures["order_lookup"] == 1
    assert failures["identification"] == 1
    assert set(steps_counted) == {"order_lookup"}


def test_it_should_reset_a_step_counter_to_zero_when_a_later_round_succeeds():
    failures, steps_counted = conversation.record_step_outcome(
        {"order_lookup": 1},
        frozenset(),
        "order_lookup",
        succeeded=False,
    )

    failures, steps_counted = conversation.record_step_outcome(
        failures,
        steps_counted,
        "order_lookup",
        succeeded=True,
    )

    assert failures.get("order_lookup", 0) == 0
    assert "order_lookup" in steps_counted


def test_it_should_not_reincrement_a_step_in_the_same_turn_after_a_resetting_success():
    failures, steps_counted = conversation.record_step_outcome(
        {},
        frozenset(),
        "order_lookup",
        succeeded=False,
    )
    failures, steps_counted = conversation.record_step_outcome(
        failures,
        steps_counted,
        "order_lookup",
        succeeded=True,
    )

    failures, steps_counted = conversation.record_step_outcome(
        failures,
        steps_counted,
        "order_lookup",
        succeeded=False,
    )

    assert failures.get("order_lookup", 0) == 0
    assert "order_lookup" in steps_counted


def test_it_should_reset_per_turn_values_in_input_gate_while_preserving_failures():
    guard = Guard(Catalog(CATALOG_CSV))
    graph = conversation.build_graph(
        FakeGateway(["Bom dia! Como posso ajudar?"]), guard
    )
    config = {"configurable": {"thread_id": "turn-reset-session"}}
    graph.update_state(
        config,
        {
            "failures": {"order_lookup": 2},
            "tool_rounds": 3,
            "regen_used": True,
            "steps_counted": frozenset({"order_lookup"}),
            "injection_suspected": True,
        },
    )

    graph.invoke({"user_text": "Bom dia"}, config)

    values = graph.get_state(config).values
    assert values["tool_rounds"] == 0
    assert not values["regen_used"]
    assert set(values["steps_counted"]) == set()
    assert values["injection_suspected"] is False
    assert values["failures"] == {"order_lookup": 2}
