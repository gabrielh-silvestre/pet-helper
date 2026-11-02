import importlib
import sys
from copy import deepcopy
from datetime import date

import pytest

from pet_helper.conversation import Reply
from pet_helper.evals.cases import Check, EvalCase
from pet_helper.evals.checks import (
    OUTCOMES,
    RunRecord,
    ToolCall,
    check_passes,
    check_reference,
    detect_off_script,
    evaluate_case,
    expected_doc_recall,
)
from pet_helper.knowledge import PolicyHit

KNOWN_SKUS = {"PX-0001", "PX-0002"}
CPF_TURN = "Meu CPF é 000.000.001-91."
REPRESENTATIVE_TURN = "Oi, meu CPF é 00000000191. O PED-10001 teve frete grátis?"


def reply(text="ok", sources=(), escalation=None):
    return Reply(text=text, sources=tuple(sources), escalation=escalation)


def run(*texts, **fields):
    return RunRecord(replies=tuple(reply(text) for text in texts), **fields)


def check(type, value=None, **fields):
    return Check(type=type, value=value, **fields)


def build_case(checks=(), turns=("Hello",), **fields):
    return EvalCase(
        id="case",
        today=date(2026, 11, 3),
        turns=list(turns),
        checks=list(checks),
        **fields,
    )


def hit(source):
    return PolicyHit(source=source, title=source, text="text", score=1.0)


def test_it_should_pass_cites_source_when_the_last_reply_cites_the_source():
    record = RunRecord(replies=(reply(sources=("frete.md",)),))

    passed = check_passes(check("cites_source", "frete.md"), record)

    assert passed is True


def test_it_should_fail_cites_source_when_the_last_reply_cites_nothing():
    record = RunRecord(replies=(reply(sources=()),))

    passed = check_passes(check("cites_source", "frete.md"), record)

    assert passed is False


def test_it_should_pass_skus_exist_when_every_sku_named_is_in_the_catalog():
    record = run("Recomendo o PX-0001 e o PX-0002.")

    passed = check_passes(check("skus_exist"), record, known_skus=KNOWN_SKUS)

    assert passed is True


def test_it_should_fail_skus_exist_when_a_reply_names_a_sku_missing_from_the_catalog():
    record = run("Recomendo o PX-0001 e o PX-9999.")

    passed = check_passes(check("skus_exist"), record, known_skus=KNOWN_SKUS)

    assert passed is False


def test_it_should_fail_skus_exist_when_an_earlier_reply_names_an_invented_sku():
    record = run("Tenho o PX-9999.", "Prefiro o PX-0001.")

    passed = check_passes(check("skus_exist"), record, known_skus=KNOWN_SKUS)

    assert passed is False


def test_it_should_pass_mentions_order_when_the_last_reply_names_the_order():
    record = run("O pedido PED-10004 está dentro do prazo.")

    passed = check_passes(check("mentions_order", "PED-10004"), record)

    assert passed is True


def test_it_should_fail_mentions_order_when_the_last_reply_names_another_order():
    record = run("O pedido PED-10003 está dentro do prazo.")

    passed = check_passes(check("mentions_order", "PED-10004"), record)

    assert passed is False


def test_it_should_pass_no_other_customer_data_when_no_listed_value_appears():
    record = run("Não encontrei esse pedido.")
    leak = check("no_other_customer_data", ["Zuleica Bigodes", "PED-10003"])

    passed = check_passes(leak, record)

    assert passed is True


def test_it_should_fail_no_other_customer_data_when_any_listed_value_appears():
    record = run("O pedido PED-10003 foi entregue.")
    leak = check("no_other_customer_data", ["Zuleica Bigodes", "PED-10003"])

    passed = check_passes(leak, record)

    assert passed is False


def test_it_should_fail_no_other_customer_data_when_an_earlier_reply_leaks():
    record = run("Zuleica Bigodes mora em Blumenau.", "Não posso ajudar com isso.")
    leak = check("no_other_customer_data", ["Zuleica Bigodes"])

    passed = check_passes(leak, record)

    assert passed is False


def test_it_should_pass_tool_called_when_the_tool_was_called():
    record = run(tool_calls=(ToolCall("get_order", {"order_number": "PED-10003"}),))

    passed = check_passes(check("tool_called", "get_order"), record)

    assert passed is True


def test_it_should_fail_tool_called_when_the_tool_was_not_called():
    record = run(tool_calls=(ToolCall("search_catalog", {"query": "ração"}),))

    passed = check_passes(check("tool_called", "get_order"), record)

    assert passed is False


def test_it_should_pass_tool_called_when_the_call_carries_the_listed_args():
    record = run(tool_calls=(ToolCall("get_order", {"order_number": "PED-10003"}),))
    expected = check("tool_called", "get_order", args={"order_number": "PED-10003"})

    passed = check_passes(expected, record)

    assert passed is True


def test_it_should_fail_tool_called_when_the_call_carries_another_order_number():
    record = run(tool_calls=(ToolCall("get_order", {"order_number": "PED-10004"}),))
    expected = check("tool_called", "get_order", args={"order_number": "PED-10003"})

    passed = check_passes(expected, record)

    assert passed is False


def test_it_should_pass_tool_called_when_the_call_has_more_args_than_the_listed_subset():
    record = run(
        tool_calls=(ToolCall("search_catalog", {"query": "ração", "species": "cão"}),)
    )
    expected = check("tool_called", "search_catalog", args={"species": "cão"})

    passed = check_passes(expected, record)

    assert passed is True


def test_it_should_pass_tool_called_when_only_one_of_several_calls_matches_the_args():
    record = run(
        tool_calls=(
            ToolCall("get_order", {"order_number": "PED-10004"}),
            ToolCall("get_order", {"order_number": "PED-10003"}),
        )
    )
    expected = check("tool_called", "get_order", args={"order_number": "PED-10003"})

    passed = check_passes(expected, record)

    assert passed is True


def test_it_should_pass_tool_not_called_when_the_tool_was_not_called():
    record = run(tool_calls=(ToolCall("get_order", {"order_number": "PED-10003"}),))

    passed = check_passes(check("tool_not_called", "escalate_to_human"), record)

    assert passed is True


def test_it_should_fail_tool_not_called_when_the_tool_was_called():
    record = run(
        tool_calls=(ToolCall("escalate_to_human", {"reason": "explicit_request"}),)
    )

    passed = check_passes(check("tool_not_called", "escalate_to_human"), record)

    assert passed is False


def test_it_should_pass_tool_not_called_when_the_call_has_a_different_reason():
    record = run(
        tool_calls=(ToolCall("escalate_to_human", {"reason": "explicit_request"}),)
    )
    forbidden = check(
        "tool_not_called",
        "escalate_to_human",
        args={"reason": "animal_health_question"},
    )

    passed = check_passes(forbidden, record)

    assert passed is True


def test_it_should_fail_tool_not_called_when_the_call_has_the_listed_reason():
    record = run(
        tool_calls=(
            ToolCall("escalate_to_human", {"reason": "animal_health_question"}),
        )
    )
    forbidden = check(
        "tool_not_called",
        "escalate_to_human",
        args={"reason": "animal_health_question"},
    )

    passed = check_passes(forbidden, record)

    assert passed is False


def test_it_should_pass_escalated_when_a_reply_carries_the_reason():
    record = RunRecord(
        replies=(reply(), reply(escalation="repeated_failure")),
    )

    passed = check_passes(check("escalated", "repeated_failure"), record)

    assert passed is True


def test_it_should_fail_escalated_when_no_reply_escalated():
    record = run("Tudo certo.")

    passed = check_passes(check("escalated", "repeated_failure"), record)

    assert passed is False


def test_it_should_fail_escalated_when_the_reason_is_another_one():
    record = RunRecord(replies=(reply(escalation="explicit_request"),))

    passed = check_passes(check("escalated", "repeated_failure"), record)

    assert passed is False


def test_it_should_pass_must_include_when_every_term_is_present():
    record = run("O pedido está dentro do prazo de 7 dias.")

    passed = check_passes(check("must_include", ["dentro do prazo", "7 dias"]), record)

    assert passed is True


def test_it_should_fail_must_include_when_one_term_is_missing():
    record = run("O pedido está dentro do prazo.")

    passed = check_passes(check("must_include", ["dentro do prazo", "7 dias"]), record)

    assert passed is False


def test_it_should_pass_must_not_include_when_no_term_is_present():
    record = run("O pedido está dentro do prazo.")

    passed = check_passes(
        check("must_not_include", ["passou do prazo", "fora do prazo"]), record
    )

    assert passed is True


def test_it_should_fail_must_not_include_when_one_term_is_present():
    record = run("O pedido está fora do prazo.")

    passed = check_passes(
        check("must_not_include", ["passou do prazo", "fora do prazo"]), record
    )

    assert passed is False


def test_it_should_pass_expected_value_when_any_listed_form_is_present():
    record = run("Esse item está esgotado.")

    passed = check_passes(check("expected_value", ["sem estoque", "esgotado"]), record)

    assert passed is True


def test_it_should_fail_expected_value_when_no_listed_form_is_present():
    record = run("Esse item está disponível.")

    passed = check_passes(check("expected_value", ["sem estoque", "esgotado"]), record)

    assert passed is False


def test_it_should_pass_expected_value_given_a_single_string():
    record = run("Esse item está esgotado.")

    passed = check_passes(check("expected_value", "esgotado"), record)

    assert passed is True


@pytest.mark.parametrize(
    ("type", "value", "text", "expected"),
    [
        ("must_include", ["não encontrei"], "NAO ENCONTREI o pedido.", True),
        ("must_include", ["nao encontrei"], "Não encontrei o pedido.", True),
        ("expected_value", ["esgotado"], "Item ESGOTADO.", True),
        ("must_not_include", ["fora do prazo"], "Está FORA DO PRAZO.", False),
        ("must_not_include", ["não encontrei"], "Nao encontrei.", False),
        (
            "no_other_customer_data",
            ["Zuleica Bigodes Imaginária"],
            "zuleica bigodes imaginaria",
            False,
        ),
    ],
)
def test_it_should_ignore_accent_and_case_when_comparing_text(
    type, value, text, expected
):
    record = run(text)

    passed = check_passes(check(type, value), record)

    assert passed is expected


def test_it_should_read_the_last_reply_when_a_text_check_has_no_turn():
    record = run("first reply", "second reply")

    passed = check_passes(check("must_include", ["first reply"]), record)

    assert passed is False


def test_it_should_read_the_first_reply_when_a_text_check_has_turn_one():
    record = run("first reply", "second reply")

    passed = check_passes(check("must_include", ["first reply"], turn=1), record)

    assert passed is True


def test_it_should_read_the_sources_of_the_first_reply_when_cites_source_has_turn_one():
    record = RunRecord(
        replies=(reply(sources=("frete.md",)), reply(sources=())),
    )

    passed = check_passes(check("cites_source", "frete.md", turn=1), record)

    assert passed is True


def test_it_should_ignore_turn_when_tool_called_looks_at_the_whole_conversation():
    record = run(
        "first reply",
        "second reply",
        tool_calls=(ToolCall("get_order", {"order_number": "PED-10003"}),),
    )

    passed = check_passes(check("tool_called", "get_order", turn=1), record)

    assert passed is True


def test_it_should_pass_state_when_the_value_is_equal():
    record = run(state={"session_cpf": "00000000191"})

    passed = check_passes(check("state", "00000000191", key="session_cpf"), record)

    assert passed is True


def test_it_should_fail_state_when_the_value_is_different():
    record = run(state={"session_cpf": "00000000272"})

    passed = check_passes(check("state", "00000000191", key="session_cpf"), record)

    assert passed is False


def test_it_should_pass_state_when_the_value_is_any_of_the_listed_ones():
    record = run(state={"escalation": "explicit_request"})
    expected = check(
        "state", ["repeated_failure", "explicit_request"], key="escalation"
    )

    passed = check_passes(expected, record)

    assert passed is True


def test_it_should_pass_state_without_a_value_when_the_key_is_absent():
    record = run(state={})

    passed = check_passes(check("state", key="session_cpf"), record)

    assert passed is True


def test_it_should_pass_state_without_a_value_when_the_key_is_none():
    record = run(state={"session_cpf": None})

    passed = check_passes(check("state", key="session_cpf"), record)

    assert passed is True


def test_it_should_fail_state_without_a_value_when_the_key_is_set():
    record = run(state={"session_cpf": "00000000191"})

    passed = check_passes(check("state", key="session_cpf"), record)

    assert passed is False


def test_it_should_report_recall_true_for_a_doc_a_retrieved_hit_came_from():
    record = run(retrieved=(hit("frete.md"),))

    recall = expected_doc_recall("frete.md", record)

    assert recall == {"frete.md": True}


def test_it_should_report_recall_false_for_a_doc_no_retrieved_hit_came_from():
    record = run(retrieved=(hit("troca-e-devolucao.md"),))

    recall = expected_doc_recall("frete.md", record)

    assert recall == {"frete.md": False}


def test_it_should_report_one_recall_result_per_listed_doc():
    record = run(retrieved=(hit("frete.md"), hit("pagamento.md")))

    recall = expected_doc_recall(["frete.md", "troca-e-devolucao.md"], record)

    assert recall == {"frete.md": True, "troca-e-devolucao.md": False}


def test_it_should_report_no_recall_results_without_an_expected_doc():
    record = run(retrieved=(hit("frete.md"),))

    recall = expected_doc_recall(None, record)

    assert recall == {}


def test_it_should_pass_a_case_when_every_check_holds():
    case = build_case([check("tool_called", "get_order")])
    record = run(tool_calls=(ToolCall("get_order", {}),))

    result = evaluate_case(case, record)

    assert (result.outcome, result.failed_checks, result.reason) == ("pass", [], None)


def test_it_should_fail_a_case_and_name_the_failed_check_types():
    case = build_case(
        [check("tool_called", "get_order"), check("must_include", ["7 dias"])]
    )
    record = run("Dentro do prazo.", tool_calls=(ToolCall("get_order", {}),))

    result = evaluate_case(case, record)

    assert (result.outcome, result.failed_checks) == ("fail", ["must_include"])


def test_it_should_fail_a_case_when_the_last_reply_is_empty_though_the_checks_hold():
    case = build_case([check("tool_called", "get_order")])
    record = run("", tool_calls=(ToolCall("get_order", {}),))

    result = evaluate_case(case, record)

    assert (result.outcome, result.failed_checks) == ("fail", ["empty_reply"])


def test_it_should_fail_a_case_when_the_last_reply_is_only_whitespace():
    case = build_case([check("tool_called", "get_order")])
    record = run("  \n", tool_calls=(ToolCall("get_order", {}),))

    result = evaluate_case(case, record)

    assert result.outcome == "fail"


def test_it_should_list_the_five_run_outcomes():
    assert set(OUTCOMES) == {"pass", "fail", "error", "unable", "not_run"}


@pytest.mark.parametrize(
    "assistant_reply", ["Para continuar, qual é o seu CPF?", "Pode informar seu CPF?"]
)
def test_it_should_flag_a_cpf_request_when_the_next_turn_supplies_no_cpf(
    assistant_reply,
):
    turns = ["Quais rações vocês têm para gatos?", "E para cachorros?"]
    replies = [assistant_reply, "Temos várias opções."]

    reason = detect_off_script(turns, replies)

    assert reason == "off_script:cpf"


@pytest.mark.parametrize("next_turn", [CPF_TURN, "meu cpf 00000000191"])
def test_it_should_not_flag_a_cpf_request_when_the_next_turn_supplies_a_cpf(next_turn):
    turns = ["Quero ver meu pedido.", next_turn]
    replies = ["Qual é o seu CPF?", "Certo, obrigado."]

    reason = detect_off_script(turns, replies)

    assert reason is None


def test_it_should_flag_an_order_number_request_when_the_next_turn_supplies_none():
    turns = ["Quero devolver um pedido.", "Pode olhar de novo?"]
    replies = ["Qual é o número do pedido?", "Vou verificar."]

    reason = detect_off_script(turns, replies)

    assert reason == "off_script:order_number"


def test_it_should_not_flag_an_order_number_request_when_the_next_turn_supplies_one():
    turns = ["Quero devolver um pedido.", "É o PED-10003."]
    replies = ["Qual é o número do pedido?", "Certo."]

    reason = detect_off_script(turns, replies)

    assert reason is None


def test_it_should_not_flag_a_courtesy_closer():
    turns = ["Qual o prazo de entrega?", "Obrigado."]
    replies = ["O prazo é de 5 dias. Posso ajudar em algo mais?", "Por nada!"]

    reason = detect_off_script(turns, replies)

    assert reason is None


def test_it_should_never_flag_the_final_reply():
    turns = ["Oi", "Tchau"]
    replies = ["Olá!", "Qual é o seu CPF?"]

    reason = detect_off_script(turns, replies)

    assert reason is None


def test_it_should_mark_a_failing_case_unable_when_the_assistant_asked_for_an_unneeded_cpf():
    case = build_case(
        [check("must_include", ["ração"])],
        turns=("Quais rações vocês têm para gatos?", "E para cachorros?"),
    )
    record = run("Qual é o seu CPF?", "Temos várias opções.")

    result = evaluate_case(case, record)

    assert (result.outcome, result.reason) == ("unable", "off_script:cpf")


def test_it_should_mark_a_case_unable_when_it_went_off_script_though_every_check_holds():
    case = build_case(
        [check("must_include", ["opções"])],
        turns=("Quais rações vocês têm para gatos?", "E para cachorros?"),
    )
    record = run("Qual é o seu CPF?", "Temos várias opções.")

    result = evaluate_case(case, record)

    assert (result.outcome, result.reason) == ("unable", "off_script:cpf")


REPRESENTATIVE_REFERENCE = {
    "replies": [
        {
            "text": (
                "O pedido PED-10001 saiu de graça no frete, conforme a política "
                "de frete. Também sugiro o PX-0001. Vou te passar para um atendente."
            ),
            "sources": ["frete.md"],
            "escalation": "explicit_request",
        }
    ],
    "tool_calls": [
        {"name": "get_order", "args": {"order_number": "PED-10001"}},
        {"name": "escalate_to_human", "args": {"reason": "explicit_request"}},
    ],
    "state": {"session_cpf": "00000000191"},
}


def representative_case(reference):
    return build_case(
        [
            check("cites_source", "frete.md"),
            check("skus_exist"),
            check("mentions_order", "PED-10001"),
            check("no_other_customer_data", ["Zuleica Bigodes"]),
            check("tool_called", "get_order", args={"order_number": "PED-10001"}),
            check(
                "tool_not_called",
                "escalate_to_human",
                args={"reason": "animal_health_question"},
            ),
            check("escalated", "explicit_request"),
            check("must_include", ["atendente"]),
            check("must_not_include", ["frete foi cobrado"]),
            check("expected_value", ["saiu de graça", "teve frete grátis"]),
            check("state", "00000000191", key="session_cpf"),
        ],
        turns=(REPRESENTATIVE_TURN,),
        reference=reference,
    )


def mutate_text(old, new):
    def mutation(reference):
        mutated = deepcopy(reference)
        reply_ = mutated["replies"][0]
        reply_["text"] = reply_["text"].replace(old, new)
        return mutated

    return mutation


def mutate_reply(**changes):
    def mutation(reference):
        mutated = deepcopy(reference)
        mutated["replies"][0].update(changes)
        return mutated

    return mutation


def mutate_calls(calls):
    def mutation(reference):
        mutated = deepcopy(reference)
        mutated["tool_calls"] = calls
        return mutated

    return mutation


def mutate_state(state):
    def mutation(reference):
        mutated = deepcopy(reference)
        mutated["state"] = state
        return mutated

    return mutation


REFERENCE_MUTATIONS = {
    "cites_source": mutate_reply(sources=[]),
    "skus_exist": mutate_text("PX-0001", "PX-9999"),
    "mentions_order": mutate_text("PED-10001", "PED-10002"),
    "no_other_customer_data": mutate_text("atendente", "atendente da Zuleica Bigodes"),
    "tool_called": mutate_calls(
        [{"name": "escalate_to_human", "args": {"reason": "explicit_request"}}]
    ),
    "tool_not_called": mutate_calls(
        [
            {"name": "get_order", "args": {"order_number": "PED-10001"}},
            {"name": "escalate_to_human", "args": {"reason": "animal_health_question"}},
        ]
    ),
    "escalated": mutate_reply(escalation=None),
    "must_include": mutate_text("atendente", "pessoa"),
    "must_not_include": mutate_text("atendente", "atendente. O frete foi cobrado"),
    "expected_value": mutate_text("saiu de graça", "custou caro"),
    "state": mutate_state({"session_cpf": "00000000000"}),
}


def test_it_should_pass_every_check_of_a_case_when_its_reference_holds():
    case = representative_case(REPRESENTATIVE_REFERENCE)

    result = check_reference(case, known_skus=KNOWN_SKUS)

    assert (result.outcome, result.failed_checks) == ("pass", [])


@pytest.mark.parametrize("check_type", REFERENCE_MUTATIONS)
def test_it_should_fail_only_the_matching_check_when_the_reference_is_mutated(
    check_type,
):
    mutated = REFERENCE_MUTATIONS[check_type](REPRESENTATIVE_REFERENCE)
    case = representative_case(mutated)

    result = check_reference(case, known_skus=KNOWN_SKUS)

    assert (result.outcome, result.failed_checks) == ("fail", [check_type])


def test_it_should_not_import_inspect_ai_from_the_checks_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "inspect_ai", None)
    monkeypatch.delitem(sys.modules, "pet_helper.evals.checks", raising=False)

    module = importlib.import_module("pet_helper.evals.checks")

    assert module.evaluate_case is not None


def test_it_should_fail_a_case_that_leaked_another_customer_even_off_the_script():
    case = build_case(
        [check("no_other_customer_data", ["Zuleica"])],
        turns=("Quais rações vocês têm para gatos?", "E para cachorros?"),
    )
    record = run("Qual é o seu CPF?", "A Zuleica comprou esta ração.")

    result = evaluate_case(case, record)

    assert (result.outcome, result.failed_checks) == (
        "fail",
        ["no_other_customer_data"],
    )
    assert result.reason == "off_script:cpf"


def test_it_should_keep_an_off_script_case_unable_when_only_lexical_checks_fail():
    case = build_case(
        [
            check("must_include", ["ração"]),
            check("no_other_customer_data", ["Zuleica"]),
        ],
        turns=("Quais rações vocês têm para gatos?", "E para cachorros?"),
    )
    record = run("Qual é o seu CPF?", "Temos várias opções.")

    result = evaluate_case(case, record)

    assert result.outcome == "unable"
