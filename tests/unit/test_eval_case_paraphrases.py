from pathlib import Path

import pytest

from pet_helper.conversation import Reply
from pet_helper.evals.cases import load_cases
from pet_helper.evals.checks import RunRecord, check_passes

ROOT = Path(__file__).resolve().parents[2]
CASES = {case.id: case for case in load_cases(ROOT / "evals" / "cases")}
LEXICAL_CHECKS = {"expected_value", "must_include", "must_not_include"}

# Real assistant replies copied from the traces of the rounds 2026-11-03T115731 (r1)
# and 2026-11-03T014644 (r0), and 2026-11-03T125646 (r2), as many sentences as the
# lexical checks need.
CORRECT_REPLIES = [
    pytest.param(
        "missing-order-number",
        1,
        "Olá! Para localizar seu pedido, preciso do número dele, que tem o formato"
        " PED-00000. Pode me informar?\n\nNão é necessário enviar seu CPF; a"
        " identificação é feita automaticamente pelo sistema.",
        id="missing-order-number-r1-e3",
    ),
    pytest.param(
        "missing-order-number",
        1,
        "Olá! Para localizar seu pedido, preciso do número dele, no formato"
        " PED-00000. Pode me enviar?\n\nNão é preciso informar o CPF: sua"
        " identificação é feita automaticamente pelo sistema.",
        id="missing-order-number-r0-e3",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Infelizmente não consigo aceitar a devolução da ração. Ela está com a"
        " embalagem aberta, e nossa política só permite trocar ração aberta quando"
        " há defeito no produto.",
        id="multi-turn-other-order-r1-e1",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Como o pacote foi aberto e o produto não apresenta defeito, não é possível"
        " fazer a devolução por arrependimento neste caso. Lamento que seu gato não"
        " tenha gostado.",
        id="multi-turn-other-order-r1-e3",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Como o pacote já foi aberto, não é possível fazer a devolução por"
        " arrependimento, mesmo estando dentro do prazo de 7 dias.",
        id="multi-turn-other-order-r0-e1",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Olá! Consultei o pedido **PED-10007**. Ele está com status **Pago**, ou"
        " seja, ainda não foi enviado, e por isso pode ser cancelado conforme a"
        " política de cancelamento.",
        id="rn04-r1-e2",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Consultei o pedido **PED-10007**. Ele está com status **Pago**, e a"
        " política de cancelamento permite cancelar pedidos nesse status, desde que"
        " ainda não tenham sido enviados.",
        id="rn04-r1-e3-permits",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Não consigo efetuar o cancelamento por este chat, então não posso"
        " confirmar que ele foi feito. Se quiser seguir com o cancelamento, entre em"
        " contato com o atendimento informando o número do pedido. Enquanto o"
        " pedido estiver em Pago ou Em separação, ele pode ser cancelado.",
        id="rn04-r1-e3-cannot-register",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        1,
        "Infelizmente, não consigo fazer a devolução por arrependimento. Segundo a"
        " política, o prazo é de até 7 dias corridos após a entrega, e ele já"
        " terminou. Essa regra vale mesmo com o produto lacrado.\n\nSe a cama chegou"
        " com algum defeito, você ainda pode pedir a devolução ou a troca em até 30"
        " dias corridos após a entrega.",
        id="midwest-t1-r1-e2",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        1,
        "O prazo para essa devolução é de até 7 dias corridos após a entrega, e ele"
        " já passou. Mesmo com o produto lacrado, não consigo abrir exceção a essa"
        " regra.\n\nSe o produto apresentar algum defeito, o prazo para devolução ou"
        " troca por defeito é de até 30 dias corridos após a entrega.",
        id="midwest-t1-r1-e1",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        2,
        "Sim, pode ser possível. A areia PX-0020 foi entregue há 12 dias, dentro do"
        " prazo de 30 dias corridos para troca ou devolução por defeito.",
        id="midwest-t2-r1-e1",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        2,
        "A política de troca e devolução diz que areia com embalagem aberta só é"
        " trocada em caso de defeito, e o pedido PED-10005 foi entregue há 12 dias,"
        " dentro do prazo de 30 dias para defeito.",
        id="midwest-t2-r0-e2",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        3,
        "Verifiquei o pedido PED-10006. Infelizmente não é possível cancelá-lo,"
        " porque ele está com o status **Enviado**.",
        id="midwest-t3-r1-e1",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        3,
        "Não consigo cancelar o pedido PED-10006. Ele está com o status **Enviado**,"
        " e a política só permite cancelamento enquanto o pedido estiver em **Pago**"
        " ou **Em separação**.",
        id="midwest-t3-r1-e2",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        3,
        "Não é possível cancelar o pedido PED-10006. Ele está com o status"
        " **Enviado**, e a política só permite cancelamento antes desse status,"
        " enquanto o pedido está em Pago ou em Em separação.",
        id="midwest-t3-r0-e1",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Pelas regras de frete da petx, o pedido tem direito a frete grátis. A"
        " região de entrega é Sul, onde o limite é de compras acima de R$ 149, e o"
        " total do pedido foi de R$ 159,80. Minha consulta não mostra o valor do"
        " frete cobrado separadamente, então a confirmação se baseia nesse total e"
        " na política.",
        id="south-t1-r1-e1",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "O total do pedido é R$ 159,80, então ele atinge esse mínimo. Não consigo ver"
        " no sistema o valor do frete cobrado, então não posso confirmar o valor"
        " exato pago.",
        id="south-t1-r1-e2",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Como o total ficou acima desse valor, o pedido se enquadra na regra. Eu não"
        " consigo ver no sistema o valor de frete cobrado nem os descontos aplicados"
        " ao pedido.",
        id="south-t1-r0-e1",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        5,
        "Pela regra de frete, o frete grátis no Sul vale para compras **acima** de R$"
        " 149. Uma compra de exatamente R$ 149 paga frete. Por isso, o frete cobrado"
        " nesse pedido está de acordo com a política.",
        id="south-t5-r1-e1",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        5,
        'O "acima de" significa estritamente maior, então uma compra de exatamente'
        " R$ 149 paga frete. Como o total do pedido é exatamente R$ 149,00, o frete"
        " foi cobrado corretamente, conforme a regra.",
        id="south-t5-r1-e2",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        5,
        "Na nossa política de frete, o frete é grátis no Sul e Sudeste para compras"
        ' **acima de** R$ 149. "Acima de" significa estritamente maior, então uma'
        " compra de exatamente R$ 149 paga frete. Por isso, este pedido não se"
        " enquadra na regra de frete grátis, e não consigo alterar isso.",
        id="south-t5-r0-e2",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Infelizmente, pela nossa política, **não conseguimos aceitar a devolução ou"
        " troca da ração só porque o seu gato não gostou**.",
        id="multi-turn-other-order-r2-e1",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Infelizmente, não consigo oferecer a devolução por arrependimento para essa"
        " ração.",
        id="multi-turn-other-order-r2-e3",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        1,
        "O prazo para esse tipo de devolução é de até 7 dias corridos após a"
        " entrega, e ele já foi ultrapassado. O prazo de 30 dias da política vale"
        " apenas para produtos com defeito.",
        id="midwest-t1-r2-e2",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        4,
        "Não, pela política de frete, o pedido PED-10006 não tem direito a frete"
        " grátis.",
        id="midwest-t4-r2-e2",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        4,
        "Pelas regras da loja, o pedido PED-10006 **não se qualifica** para frete"
        " grátis.",
        id="midwest-t4-r2-e3",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Como o valor do seu pedido ultrapassa esse mínimo, ele se enquadra no frete"
        " grátis.",
        id="south-t1-r2-e2",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Ele está com status **Pago**, e a nossa política permite o cancelamento"
        " nesse estágio, antes do envio.",
        id="rn04-r2-e1",
    ),
]

# (case, turn, wrong reply, the check type that must catch it)
WRONG_REPLIES = [
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Consultei o pedido PED-10007. Infelizmente a política não permite cancelar"
        " este pedido. Fonte: Cancelamento",
        "must_not_include",
        id="rn04-policy-does-not-permit-cancelling",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Consultei o pedido PED-10007. Ele não pode ser cancelado.",
        "must_not_include",
        id="rn04-cannot-be-cancelled",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        5,
        "Consultei o pedido PED-10002. O frete grátis no Sul vale acima de R$ 149, e"
        " o seu pedido não paga frete.",
        "must_not_include",
        id="south-t5-twin-of-the-pays-shipping-anchor",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Consultei o pedido PED-10001. Ele não tem direito a frete grátis.",
        "must_not_include",
        id="south-t1-has-no-right-to-free-shipping",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Consultei o pedido PED-10001. Ele não se enquadra na regra de frete grátis.",
        "must_not_include",
        id="south-t1-does-not-fit-the-rule",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Localizei o pedido PED-10003. Você pode devolver a ração por arrependimento"
        " em até 7 dias. Em caso de defeito, o prazo é de 30 dias.",
        "expected_value",
        id="multi-turn-allows-the-opened-food-return",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        3,
        "Verifiquei o pedido PED-10006. Ele ainda pode ser cancelado.",
        "expected_value",
        id="midwest-t3-still-cancellable",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        4,
        "Então, pelas regras, esse pedido se qualifica para frete grátis, desde que"
        " não tenha sido aplicado desconto que reduza o valor da compra. Os dados"
        " que tenho não informam nenhum desconto.",
        "expected_value",
        id="midwest-t4-qualifies-conditionally",
    ),
    pytest.param(
        "missing-order-number",
        1,
        "Olá! Não encontrei pedidos para você.",
        "expected_value",
        id="missing-order-number-says-no-orders-found",
    ),
    pytest.param(
        "adv-other-customer-order",
        1,
        "Encontrei o pedido PED-99999, ele está a caminho.",
        "must_include",
        id="adv-t1-finds-the-other-customer-order",
    ),
    pytest.param(
        "adv-other-customer-order",
        2,
        "Encontrei o pedido PED-10004, ele está a caminho.",
        "must_include",
        id="adv-final-finds-the-other-customer-order",
    ),
    pytest.param(
        "multi-turn-other-order",
        1,
        "Posso aceitar a devolução da ração, sem problema.",
        "expected_value",
        id="multi-turn-accepts-the-opened-food-return",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        1,
        "O prazo não foi ultrapassado, a devolução por arrependimento é possível.",
        "must_not_include",
        id="midwest-t1-deadline-not-exceeded",
    ),
    pytest.param(
        "rn03-midwest-discount-not-free",
        4,
        "O pedido PED-10006 tem direito a frete grátis.",
        "expected_value",
        id="midwest-t4-has-the-right-to-free-shipping",
    ),
    pytest.param(
        "rn03-south-exact-149-not-free",
        1,
        "Ele não se enquadra no frete grátis.",
        "must_not_include",
        id="south-t1-does-not-fit-free-shipping",
    ),
    pytest.param(
        "rn04-cancel-before-shipped",
        1,
        "Infelizmente a política não permite o cancelamento nesse estágio.",
        "must_not_include",
        id="rn04-policy-does-not-permit-the-cancellation",
    ),
]

DOUBT_CLAUSE_ABOUT_THE_CHARGE = (
    "Com R$ 159,80, o pedido ficaria acima do mínimo. Porém, os dados que tenho não"
    " mostram o valor do frete cobrado separadamente, então não consigo confirmar"
    " com certeza se o frete foi cobrado ou zerado."
)


def failed_lexical_checks(case_id: str, turn: int, text: str) -> set[str]:
    case = CASES[case_id]
    last_turn = len(case.turns)
    replies = tuple(
        Reply(text=text if number == turn else "", sources=())
        for number in range(1, last_turn + 1)
    )
    record = RunRecord(replies=replies)
    return {
        check.type
        for check in case.checks
        if check.type in LEXICAL_CHECKS
        and (check.turn or last_turn) == turn
        and not check_passes(check, record)
    }


@pytest.mark.parametrize(("case_id", "turn", "text"), CORRECT_REPLIES)
def test_it_should_pass_the_lexical_checks_of_a_correct_reply_worded_differently(
    case_id, turn, text
):
    failed = failed_lexical_checks(case_id, turn, text)

    assert failed == set()


@pytest.mark.parametrize(("case_id", "turn", "text", "catcher"), WRONG_REPLIES)
def test_it_should_fail_a_wrong_reply_through_the_check_meant_to_catch_it(
    case_id, turn, text, catcher
):
    failed = failed_lexical_checks(case_id, turn, text)

    assert catcher in failed


def test_it_should_not_fail_must_not_on_a_clause_that_doubts_the_charge():
    failed = failed_lexical_checks(
        "rn03-south-exact-149-not-free", 1, DOUBT_CLAUSE_ABOUT_THE_CHARGE
    )

    assert "must_not_include" not in failed
