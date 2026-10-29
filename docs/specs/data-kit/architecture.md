# Arquitetura: kit de dados da petx

Decisões da cadeira do dono para a unidade, tomadas sobre o [plan.md](plan.md) e a [spec.md](spec.md), com base na [architecture.md](../../architecture.md), nos ADRs [0001](../../adr/0001-order-access-by-cpf-and-order-number.md) e [0002](../../adr/0002-business-rules-in-policies-facts-from-tools.md) e na [cadeira-do-dono.md](../../directives/cadeira-do-dono.md). Nenhuma decisão altera diretriz, premissa, ADR ou a arquitetura aprovada, por isso nenhuma vira ADR. As decisões 5, 7 e 8 ajustam o plano, e os ajustes estão listados no fim.

## 1. Modelos de dados antes dos serviços

### Escolha

Os modelos pydantic permissivos ficam em `src/pet_helper/orders/models.py` e `src/pet_helper/catalog/models.py`, e o `EvalCase`, o `load_cases` e o `to_sample_kwargs` ficam em `src/pet_helper/evals/cases.py`. `Order` e `Customer` nunca saem do pacote `orders`, e o `OrderView` continua sendo a única saída dele (§2 da arquitetura). O `Product` da §4, com `in_stock`, deriva de `stock` no mesmo modelo, sem uma segunda classe. O `to_sample_kwargs` não é interface pública de `evals`, cuja interface segue sendo `run_round` mais as checagens puras da §5, e a unidade de eval pode reescrevê-lo.

### Motivo

A §4 deixa com o executor os arquivos internos de cada módulo. Os `models.py` descrevem o formato do registro do kit, que é a camada do adaptador, não a interface do módulo. Já o `cases.py` é o carregador de casos YAML que a §5 esconde dentro de `evals`.

### Alternativas

- Adiar os modelos para as unidades dos serviços: rejeitada, porque o `validate.py` e os testes de `tests/kit` ficariam sem uma fonte única para o formato.

`origin: simulated-approved`

## 2. Timeout da API mock como exceção

### Escolha

`OrderApi.get(order_number, customer_cpf)` devolve `ApiResponse(status, body)` para 200, 404, 403 e 503 e levanta o `TimeoutError` da biblioteca padrão no timeout. Um adaptador HTTP futuro traduz a exceção da biblioteca dele para `TimeoutError`. O `OrderService` faz uma nova tentativa só em `TimeoutError` e mapeia 503 para `Unavailable` sem nova tentativa (§4).

### Motivo

O timeout do lado do cliente não tem status HTTP, e todo cliente HTTP real levanta exceção nesse caso. Dessa forma, um adaptador HTTP cumpre o mesmo port sem mudança.

### Alternativas

- `ApiResponse` com 504 ou 408: rejeitada, porque mistura transporte com resposta, e o 408 é emitido pelo servidor.
- Exceção própria do projeto: rejeitada, porque prende o port a um tipo nosso sem ganho.

`origin: simulated-approved`

## 3. Validador de CPF em `orders/cpf.py`

### Escolha

A função pura de dígito verificador fica em `src/pet_helper/orders/cpf.py`. O `conversation`, dono do vínculo do Session Customer (§4), a usa pelo tipo `Cpf` de `OrderService.lookup(customer: Cpf | None, ...)`. O `guard` não importa o validador.

### Motivo

A dependência segue de Service para Model, como o MSC pede. O `guard` mascara todo número com formato de CPF, válido ou não (§2), logo não precisa da validação.

### Alternativas

- No `guard`: rejeitada, porque amarra a máscara à validade, contra a §2.
- Num pacote `common`: rejeitada, porque fica fora do mapa de módulos da §4.
- No `conversation`: rejeitada, porque inverteria a dependência para Model→Service.

`origin: simulated-approved`

## 4. Fatos do pedido diante do ADR 0002

### Escolha

- A região fica gravada junto da UF em cada pedido, com teste de coerência entre as duas.
- O total é a soma dos itens, sem frete, e é a base da RN03. Isso consta no `KIT.md` e no `frete.md`.
- Cada item traz categoria, embalagem aberta e `unit_price`, e cada pedido traz o meio de pagamento.
- A `reference_date` é metadado do kit: nunca entra no corpo da resposta da API nem no `OrderView`, e os dias desde a entrega só se calculam pelo `Clock`.

### Motivo

Os fatos de RN01 a RN04 estão todos presentes, e nenhum deles é um veredito. Assim, o modelo aplica a regra da fonte citada, como o ADR 0002 manda.

### Alternativas

- Um campo `shipping_fee` no pedido: rejeitada, porque entregaria ao modelo o veredito da RN03 já pronto.

`origin: simulated-approved`

## 5. Data de referência e `Clock`

### Escolha

A §4 fica como está: `Clock.today()` lê `PET_HELPER_TODAY` ou usa a data do sistema. A data do kit entra pela configuração entregue, não por um padrão no código: o compose e o `.env.example` trazem `PET_HELPER_TODAY=2026-11-03`, o README orienta a banca e a CLI mostra a data efetiva no início da sessão. Esses três pontos ficam para a unidade do `Clock`.

### Motivo

Mudar o padrão do `Clock` alteraria uma decisão registrada na §4 e prenderia o `Clock` ao arquivo do kit, dando uma data falsa fora da demonstração.

### Alternativas

- Padrão igual à `reference_date` do kit: rejeitada pelos motivos acima. Seria uma decisão crítica, com aval do dono.

`origin: simulated-approved`

## 6. Schema dos casos de eval e oráculo

### Escolha

Os campos `behaviors`, `expected_outcome` e `absent_refs` entram como acréscimo em `metadata`. A semântica de `Check(type, value)` é provisória, e `suite` é opcional, já que o caso reservado se define pela pasta (§5). O oráculo de RN01 a RN04 fica só em `tests/kit/conftest.py`.

### Motivo

O acréscimo em `metadata` não toma da unidade de eval a decisão sobre o que ela pontua. Já o oráculo em `src/` convidaria a criar uma tool de elegibilidade, o que vai contra o ADR 0002. Levar uma regra para uma tool exige revisar o ADR.

### Alternativas

- O oráculo em `src/`: rejeitada pelo motivo acima.
- Adiar o schema para a unidade de eval: rejeitada, porque os 25 casos sairiam sem formato validado.

`origin: simulated-approved`

## 7. Layout do kit e divisão entre `validate.py` e CI

### Escolha

O layout é `kit/policies/*.md`, `kit/orders.json`, `kit/catalog.csv` e `kit/validate.py`, este fora do pacote, fora do piso de cobertura e fora do prek e da CI. `pydantic` e `pyyaml` viram dependências diretas. O `kit/validate.py` valida só `kit/`: políticas, catálogo, pedidos e referências cruzadas. As checagens de caso que protegem a pontuação vão para `tests/kit`, na CI: `id` igual ao nome do arquivo e único, `suite` no conjunto, `today` igual à `reference_date`, `Check.type` conhecido, critério de `judge` e motivo de `escalated`. O vocabulário de `behaviors` só passa pelos mínimos de cobertura, e o teto de 25 casos vira orçamento declarado no `KIT.md`.

### Motivo

O desafio pede um "script curto (cerca de 30 linhas)" que valida o kit (premissa `aderencia-ao-desafio`). Porém um caso com erro nessas checagens seria pontuado errado sem aviso. Por isso elas ficam na CI, e nenhuma delas quebra com um caso novo bem escrito na alteração ao vivo. O `deptry` exige que as bibliotecas importadas sejam dependências diretas.

### Alternativas

- As checagens de caso só no `validate.py`, como no plano: rejeitada, porque o script cresce além do pedido e o erro de pontuação passa sem aviso na CI.

`origin: simulated-approved`

## 8. Valores em pt-BR e termos do glossário

### Escolha

O dado traz valores em pt-BR, e os identificadores de código ficam em inglês. Os tokens de `expected_outcome` ficam em `metadata`, onde o modelo não os vê. O comportamento de escalonamento por reclamação passa a se chamar `escalation_strong_negative_sentiment` (caso `esc-strong-negative-sentiment`, com insulto ou frustração intensa). Além disso, o caso 5 (`rn02-card-refund-return`) dá lugar a `esc-ordinary-complaint-no-escalation`, que verifica que uma reclamação comum não escala (`tool_not_called: escalate_to_human`). Os casos continuam 25. O `GLOSSARY.md` ganha os termos Category e Payment Method. Para a futura unidade do catálogo, o filtro de categoria e espécie ignora acento e o schema da tool lista os valores pt-BR num enum.

### Motivo

O `GLOSSARY.md` manda evitar o termo "serious complaint" e define que reclamação comum não escala, e esse comportamento ainda não tinha caso (premissa `comportamento-nasce-como-eval`). O ramo cartão da RN02 segue provado pelo oráculo no slot O1, e a RN02 continua coberta pelo caso 4.

### Alternativas

- Manter `escalation_serious_complaint`: rejeitada por contrariar o glossário.
- Um 26º caso para a reclamação comum: rejeitada, porque estoura o orçamento de 25.

`origin: simulated-approved`

## Fora desta unidade

A falha repetida (Repeated Failure) vira um pytest da unidade `conversation`, com o gateway fake, e não um caso de eval, porque quem decide é o backend.

## Ajustes no plano

Estas decisões prevalecem sobre o [plan.md](plan.md) e o `.omc/plans/data-kit.md` onde os dois divergirem:

1. Decisão 5: sai do seguimento do `Clock` a frase "o padrão da variável deve ser a `reference_date`". No lugar entram o compose e o `.env.example` com a data, o README e a data efetiva mostrada pela CLI.
2. Decisão 7: as checagens de `id`, `suite`, `today`, `Check.type`, `judge` e motivo de `escalated` saem do `validate.py` (US-7) e vão para `tests/kit/test_kit_eval_cases.py` (US-6). O teto de 25 vai para o `KIT.md`.
3. Decisão 8: `escalation_serious_complaint` e `esc-serious-complaint` viram `escalation_strong_negative_sentiment` e `esc-strong-negative-sentiment`. O caso 5 vira `esc-ordinary-complaint-no-escalation`. O `GLOSSARY.md` ganha Category e Payment Method (US-8).
4. Decisão 4: o `KIT.md` e o `frete.md` dizem que o total é a soma dos itens, sem frete.

## Emendas na entrega

Decisões de produto tomadas pela cadeira do dono durante o `/deliver`, com `origin: simulated`, para o dono rever no merge:

1. O `kit/validate.py` ficou só com o que o desafio lista (contagens, colunas e SKU de pedido no catálogo, mais H1 e faixa de palavras das políticas), com 55 linhas. Padrão e duplicidade de SKU seguem na CI, e as checagens de marca saíram.
2. A decisão 8 foi emendada: o caso `esc-ordinary-complaint-no-escalation` foi fundido em `rn03-south-above-149-free`, que abre com uma reclamação comum e verifica que ela não escala. Os casos de RN passaram a cobrir os dois lados de cada regra (arrependimento negado por prazo, reembolso no cartão, frete grátis e R$ 249 exato), e a suíte continua com 25 casos.
3. Escopo provisório dos checks, até a unidade de eval decidir: `must_include`, `must_not_include`, `expected_value`, `mentions_order` e `cites_source` olham só a última resposta do assistente; `skus_exist`, `no_other_customer_data`, `tool_called`, `tool_not_called` e `escalated` olham a conversa inteira. Fica para a unidade de eval a invariante de que uma resposta vazia reprova qualquer caso.
4. O caso `adv-coupon-injection` fica só com checagens negativas: a defesa é de capacidade (nenhuma tool concede desconto), como na §4 da arquitetura. Um critério de juiz para injeção foi rejeitado, porque alteraria a lista fixa de critérios da [loop-melhoria.md](../../directives/loop-melhoria.md).
5. O caso `adv-other-customer-data` passou a cobrir também o segundo CPF na mesma conversa: abre com o CPF de C1, tenta o de C2 e espera que o assistente peça uma conversa nova. A suíte continua com 25 casos.
6. A CI deixou de exigir que todo caso apareça no mapa de armadilhas do `KIT.md` e que a proibição de temas valha para políticas novas, e o `validate.py` aceita de 8 a 10 políticas, para que a alteração ao vivo não quebre nada.
