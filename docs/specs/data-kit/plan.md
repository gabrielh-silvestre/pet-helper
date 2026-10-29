# Plano: kit de dados da petx

Status: aprovado em 29/10/2026.

Obs: _a [architecture.md](architecture.md) desta unidade ajusta quatro pontos deste plano (seção "Ajustes no plano"), e onde os dois divergem vale a arquitetura._

Spec: [spec.md](./spec.md). Base: [architecture.md](../../architecture.md), [requisitos.md](../../requisitos.md), [fluxo-desenvolvimento.md](../../directives/fluxo-desenvolvimento.md), [loop-melhoria.md](../../directives/loop-melhoria.md), [ADR 0001](../../adr/0001-order-access-by-cpf-and-order-number.md) e [ADR 0002](../../adr/0002-business-rules-in-policies-facts-from-tools.md). Formato: [plano do esqueleto](../walking-skeleton/plan.md). Modo do consenso: SHORT. Iteração 3 do ralplan: Critic APPROVE, com os ajustes pontuais da última passada do Architect já aplicados. A fonte completa do plano está em `.omc/plans/data-kit.md`.

## Resumo RALPLAN-DR

### Princípios

1. Teste primeiro vale também para conteúdo, ou seja, o `test-engineer` escreve as propriedades do kit antes de qualquer geração, e o `executor` gera até o teste passar.
2. Cada parte gerada tem o prompt gravado na íntegra em `kit/PROMPTS.md` antes de ser usado, e é desse arquivo que o gerador o lê.
3. O que o assistente precisa calcular é provadamente calculável: datas absolutas ancoradas em 2026-11-03, total igual à soma dos itens e um teste que recalcula a resposta de cada caso de RN a partir do kit.
4. Os modelos de `src/` são permissivos na estrutura, já as regras de qualidade do kit (CPF válido, e-mail reservado, vocabulários fechados, contagens) ficam em `kit/validate.py` e `tests/kit`, dessa forma a alteração ao vivo (regra ou caso novo) carrega sem mudar código.
5. Esta unidade escreve dado, formato, adapter, script e casos, e nada que consuma o dado entra (`OrderService`, `Catalog`, `PolicyIndex`, `Clock`, harness). Vale o menor mecanismo que fecha o AC, depois de checar a ferramenta existente.

### Os 3 fatores de decisão

1. Coerência e cobertura de armadilhas do kit, que é critério de avaliação do desafio ("coerência, cobertura das armadilhas e revisão crítica do que a IA gerou").
2. Rodar sem travar numa execução autônoma do `/deliver`, com rastro verificável do que foi gerado, reparado e revisado.
3. Não quebrar a alteração ao vivo nem decidir o que as unidades seguintes são donas, logo as contagens exatas ficam só no script manual e os contratos de formato ficam estáveis.

### Decisões e opções

**D1. Como o conteúdo é gerado e revisado numa execução autônoma.**

- **Opção A (escolhida): uma geração por parte, na ordem do dado (catálogo, pedidos, políticas, casos).** A sessão principal grava o prompt da parte em `PROMPTS.md`, o `executor` (sonnet) da história lê essa seção e gera a partir dela, cada reparo vira `### Reparo N` na mesma seção e cada rodada do `critic` (sonnet) vira `### Rodada N` em `## Auditoria do critic`. Um `document-specialist` pesquisa a estrutura de FAQs antes das políticas. A confirmação humana vem pela revisão e pelo merge do PR (D1.1).
  - Prós: o catálogo existe antes dos pedidos e os pedidos antes dos casos, então cada prompt aponta para arquivos reais. O prompt registrado é o que o gerador leu, e não um texto reescrito depois, além de cada parte ter teste vermelho próprio e reparo localizado.
  - Contras: são 4 prompts mais a pesquisa e a auditoria, e o `critic` só vê o kit completo no fim.
- **Opção B: uma geração única do kit inteiro.** Prós: um prompt só. Contras: os pedidos citam SKU que ainda não existe, a saída é grande demais para um agente só e uma falha refaz tudo.
- **Opção C: a sessão principal escreve o kit direto, sem gerador separado.** Prós: sem custo de coordenação. Contras: o desafio pede os prompts usados e a "revisão crítica do que a IA gerou", porém se quem orquestra e revisa também escreve, o autor e o revisor se fundem (o fluxo exige passes separados) e o `PROMPTS.md` viraria texto reconstruído depois, e não o insumo real.
- **D1.1. Confirmação do dono.** Não bloqueia a execução e não depende de commit depois do veredito, isso porque a M3 (`/pr-panel`) só posta o veredito, sem coletar achados do dono nem commitar depois dele. O `### Dono` do `KIT.md` é texto estático que continua verdadeiro depois do merge: "Confirmação humana: revisão e merge deste PR pelo dono. Achados do dono viram comentários no PR e, se pedirem mudança, unidade nova."

**D2. Onde moram o script de validação e o validador de CPF.**

- **Opção A (escolhida): script em `kit/validate.py`, rodado por `uv run python kit/validate.py`, e validador em `src/pet_helper/orders/cpf.py`.**
  - Prós: o script fica junto do dado que o desafio manda entregar em `kit/` e roda só por comando. Ele usa os modelos pydantic de `src/`, então a forma tem uma fonte só, e como `orders` é onde a arquitetura §4 põe o `Cpf` de `OrderService.lookup(customer: Cpf | None)`, o validador já nasce no lugar em que o tipo vai morar.
  - Contras: `kit/` passa a ter código, e o script fica fora do piso de cobertura de `src/` (os testes o exercitam mesmo assim).
- **Opção B: subcomando `pet-helper kit validate` em `src/`.** Prós: conta na cobertura. Contras: cresce a CLI e o `src` por uma checagem que a spec manda ser só por comando.
- **Opção C: `scripts/validate_kit.py` e o CPF num pacote novo `common`.** Prós: nenhum. Contras: o script fica longe do kit, e `common` não está no mapa de módulos da arquitetura §4.
- O `guard` não importa o validador, porque a arquitetura §2 manda mascarar todo número com formato de CPF, válido ou não, e §4 só lista `catalog` como dependência do `guard`. A validade só decide o vínculo do Session Customer, que é da `conversation` via `orders`.

**D3. Formato do caso YAML e o mapa para o `Sample` do Inspect.**

- **Opção A (escolhida): modelo pydantic permissivo `EvalCase` em `src/pet_helper/evals/cases.py`, com os campos da arquitetura §5 mais `behaviors`, `expected_outcome` e `absent_refs`, `load_cases(dir)` por `yaml.safe_load` e a função pura `to_sample_kwargs(case) -> dict`, testada sem importar `inspect-ai`.** O `id` vai para `Sample.id`, os `turns` para `Sample.input` (lista de mensagens de usuário) e o resto vai para `Sample.metadata`.
  - Prós: só `pyyaml` e `pydantic` como dependências. O mapa do AC18 é código testado e não documentação, e `behaviors` e `expected_outcome` deixam cobertura (AC19, AC21) e computabilidade (AC8) virarem teste.
  - Contras: a conversão final em objetos do Inspect só é provada na unidade de eval, já que a função devolve dicionários.
- **Opção B: adicionar `inspect-ai` agora e provar `Sample(**kwargs)`.** Prós: prova de ponta a ponta. Contras: traz uma dependência pré-1.0 e pesada para uma unidade de dado, antes do teste prático do lote que a unidade de eval faz ao fixar a versão.
- **Opção C: validar o YAML com `jsonschema`.** Descartada, porque repete o pydantic que a unidade já usa.

**D4. Como o dado fica calculável contra 2026-11-03.**

- **Opção A (escolhida): datas absolutas, `reference_date` dentro do `kit/orders.json`, `today` de todo caso igual a ela e testes que recalculam a resposta de cada caso de RN a partir do kit (D4.1).** Os dias exatos de cada pedido vão na tabela de slots, calculados aqui para o gerador não fazer aritmética de data.
  - Prós: é o que a spec e o ADR 0002 decidiram, o teste quebra se a data derivar e o assistente lê o dado sem relógio.
  - Contras: o kit só reproduz as respostas esperadas com `today` igual à data de referência (seguimento do `Clock`).
- **Opção B: deslocamentos relativos (`delivered_days_ago`) resolvidos na leitura.** Descartada porque a arquitetura põe o cálculo de `days_since_delivery` no `OrderService` com o `Clock`, a partir de uma data absoluta. Com deslocamentos, o corpo que a API devolve passaria a depender do relógio, e `PET_HELPER_TODAY` mudaria o dado em vez de só o relógio, logo a resposta esperada de um caso (calculada com `today` fixo) deixaria de bater com o que a API devolve em outro dia.
- **D4.1. Prova de computabilidade.** Um teste recalcula, com o `oracle`, o resultado de cada caso de RN a partir do kit e o compara com o `expected_outcome` do caso. O `oracle` é uma fixture de `tests/kit/conftest.py` (cerca de 40 linhas, só no teste, descrita na seção Oráculo), e o vocabulário do `expected_outcome` está na seção Contratos.

## Contratos que os testes fixam

O `test-engineer` escreve os testes antes do código e o `executor` não edita `tests/`, por isso estes nomes e comportamentos são o acordo entre os dois. O resto é do executor. Todos os modelos usam `ConfigDict(extra="allow")` (campo novo no dado sobrevive a `model_dump`) e `populate_by_name=True` onde há alias. Nenhum modelo valida vocabulário fechado, CPF, e-mail nem contagem, porque isso é do `validate.py` e de `tests/kit`.

### `pet_helper.orders.cpf`

- `normalize_cpf(text: str) -> str`, que devolve só os dígitos.
- `is_valid_cpf(text: str) -> bool`, que aceita com ou sem pontuação e exige 11 dígitos, não todos iguais, com os dois dígitos verificadores corretos.

### `pet_helper.catalog.models`

- `SKU_PATTERN`, que é `PX-` e 4 dígitos (ex. `PX-0042`).
- `Product`, com campos em inglês e alias da coluna do CSV: `sku`/`SKU`, `name`/`nome`, `category`/`categoria`, `species`/`espécie`, `price`/`preço` (`Decimal`), `stock`/`estoque` (`int`), `description`/`descrição`. Os demais campos são `str`.
- `load_products(path) -> list[Product]`, que usa `csv.DictReader` em UTF-8, com as chaves do cabeçalho normalizadas em NFC antes de casar com os aliases, e cada linha vira um `Product`. O cabeçalho exato é conferido pelo `validate.py`, e não pelo carregador.
- Valores de `categoria`: `ração`, `areia`, `brinquedo`, `acessório`. Valores de `espécie`: `cão`, `gato`, `ambos`. Os valores são conteúdo voltado ao cliente, então ficam em pt-BR, como os nomes do desafio (as chaves e identificadores de código seguem em inglês). Os mesmos valores de categoria aparecem nos itens de `orders.json`, e o mapa dos cabeçalhos para os campos em inglês vai no `KIT.md`. Fica de fora uma categoria "petisco", já que a RN01 só trata ração e areia abertas e petisco aberto seria um caso sem regra. A marca não tem coluna, ela é uma das 5 marcas fictícias fixas (`Focinhex`, `Bigodin`, `Latidão`, `Miauzito`, `Patacorp`) escrita dentro do `nome`.

### `pet_helper.orders.models`

- `ORDER_NUMBER_PATTERN`, que é `PED-` e 5 dígitos, e não pode casar `SKU_PATTERN`, porque o guard de saída procura SKU por esse padrão.
- `Customer(cpf, name, email)`, `OrderItem(sku, quantity: int, unit_price: Decimal, category, opened: bool = False)`, `Order(number, customer_cpf, status, delivered_on: date | None, region, state, total: Decimal, payment_method, items)`, `OrderBook(reference_date: date, customers, orders)` e `load_orders(path) -> OrderBook`.
- Vocabulários, conferidos no `validate.py` e em `tests/kit`: `status` em `Pago`, `Em separação`, `Enviado`, `Entregue`, `Cancelado` (os nomes do desafio e do glossário), `region` em `Sul`, `Sudeste`, `Centro-Oeste`, `Nordeste`, `Norte` e `payment_method` em `Pix`, `Cartão` (os dois têm de aparecer). O `state` é a UF. São valores que o assistente mostra ao cliente, por isso em pt-BR, já as chaves do JSON seguem em inglês.
- `cpf` só dígitos, e `total` e `unit_price` em reais com duas casas.

### `pet_helper.orders.api`

- `ApiResponse(status: int, body: dict | None)`, dataclass imutável, e `OrderApi`, protocolo com `get(order_number: str, customer_cpf: str) -> ApiResponse`.
- `MockOrderApi(orders_path)`, sem caminho padrão (a raiz de composição nasce na unidade do `OrderService`). O `get` se comporta assim, nesta ordem:
  1. `PET_HELPER_ORDER_API_FAILURE` definida: `503` devolve `ApiResponse(503, {"error": "unavailable"})`, `timeout` levanta `TimeoutError` e outro valor levanta `ValueError`. A variável é lida a cada chamada e vence o número fixo.
  2. `FAILURE_503_ORDER = "PED-50300"` devolve 503, e `FAILURE_TIMEOUT_ORDER = "PED-40800"` levanta `TimeoutError`. Nenhum dos dois existe no `orders.json`.
  3. Pedido inexistente: `ApiResponse(404, {"error": "not_found"})`.
  4. CPF diferente do dono, comparado por `normalize_cpf`: `ApiResponse(403, {"error": "forbidden"})`.
  5. Dono: `ApiResponse(200, <pedido em JSON>)`, com os campos extras preservados.
- Nada simula latência de verdade (sem `sleep`).

### `pet_helper.evals.cases`

- `Check(type: str, value: str | list[str] | None)`. A semântica é provisória e a unidade de eval é a dona. Tipos que os casos deste plano usam: `cites_source`, `skus_exist`, `mentions_order`, `no_other_customer_data`, `tool_called`, `tool_not_called`, `escalated`, `must_include`, `must_not_include`, `expected_value`. O `must_include` exige todos os termos, o `expected_value` aceita qualquer um da lista (formas de escrever o valor calculado), e `must_not_include` e `no_other_customer_data` proíbem todos. O `escalated` recebe o motivo (`animal_health_question`, `strong_negative_sentiment`, `repeated_failure`, `explicit_request`), e `tool_called` e `tool_not_called` recebem uma das 4 tools da arquitetura. A comparação é sem acento e sem caixa. O `value` é obrigatório em todos os tipos, menos `skus_exist` e `cites_source`, porém essa checagem é da unidade de eval, e não da CI.
- `EvalCase(id, suite: str | None = None, rule: str | None = None, today: date, turns: list[str], checks: list[Check] = [], expected_doc: str | None = None, judge: list[str] = [], behaviors: list[str] = [], expected_outcome: str | None = None, absent_refs: list[str] = [])`, com `coerce_numbers_to_str=True` (escalar numérico do YAML, como um CPF sem aspas, vira texto onde o campo é `str`). A `suite` é opcional porque o reservado se define pela pasta (arquitetura §5).
- `load_cases(dir) -> list[EvalCase]`, com um YAML por arquivo, em ordem de nome.
- `to_sample_kwargs(case) -> dict`, que devolve `{"id": case.id, "input": [{"role": "user", "content": t} for t in case.turns], "metadata": {...}}`, com `suite`, `rule`, `today` (ISO), `checks`, `expected_doc`, `judge`, `behaviors`, `expected_outcome` e `absent_refs` em `metadata`.
- Regras de qualidade, só no `validate.py`: `suite` em `capability` ou `regression`, `rule` no formato `RN\d{2}`, `behaviors` não vazio e dentro do vocabulário, `today` igual ao `reference_date`, `id` igual ao nome do arquivo e único. A CI não afirma nada disso, ela mantém mínimos, posse, existência e o oráculo de RN01 a RN04.

### Vocabulários dos casos

- `behaviors` (conferido só no `validate.py`): `rn_calculable`, `injection`, `other_customer_data`, `nonexistent_order`, `dont_know`, `api_failure`, `missing_order_number`, `multi_turn`, `recommendation`, `recommendation_out_of_stock`, `recommendation_similar_names`, `escalation_serious_complaint`, `escalation_explicit_request`, `escalation_animal_health`, `source_citation`.
- `expected_outcome`, obrigatório nos casos com `rule` em `RN01` a `RN04`: RN01 `regret_allowed`, `regret_denied`, `defect_allowed`, `defect_denied`; RN02 `refund_pix_5_business_days`, `refund_card_2_invoices`; RN03 `free_shipping`, `shipping_charged`; RN04 `cancel_allowed`, `cancel_denied_return_flow`. Outro valor de `rule` (uma regra nova da sessão ao vivo) passa pelo oráculo sem checagem de resultado.

### Oráculo de RN01 a RN04

É a fixture `oracle` de `tests/kit/conftest.py` (`tests/` não tem `__init__.py`, então a fixture é como os módulos de teste a compartilham), que existe só como teste e não entra em `src/` (ADR 0002). O `oracle(rule, order, sku=None, family=None) -> str` devolve o `expected_outcome` calculado do pedido do caso, usando o último `PED-` citado nos `turns`, o último SKU citado (se houver) e, em RN01, a `family` (`regret` ou `defect`) tirada do prefixo do `expected_outcome` do caso. Dias desde a entrega = `reference_date` menos `delivered_on`.

- RN01 `regret`: com SKU citado, é permitido se dias <= 7 e o item não é `ração` ou `areia` com `opened`. Sem SKU citado, vale para o pedido inteiro, ou seja, é permitido se dias <= 7 e nenhum item está com `opened`. RN01 `defect`: permitido se dias <= 30, aberto ou não. Pedido que não está `Entregue` faz o oráculo falhar (RN01 só trata pedido entregue).
- RN02: pelo `payment_method`, `Pix` dá `refund_pix_5_business_days` e `Cartão` dá `refund_card_2_invoices`, em qualquer status.
- RN03: `free_shipping` se o total é estritamente maior que 149 em Sul/Sudeste ou que 249 nas demais regiões, senão `shipping_charged`.
- RN04: `Pago` e `Em separação` dão `cancel_allowed`, `Enviado` e `Entregue` dão `cancel_denied_return_flow`, e `Cancelado` faz o oráculo falhar (nenhum caso RN04 cita pedido cancelado).

A rede lexical protege contra um caso escrito com o veredito oposto, porém não substitui o juiz. Seja E o conjunto de textos de `expected_value` e `must_include` do caso, em NFC, minúsculas e sem acento. Cada termo exigido tem de aparecer em ao menos um texto de E, e nenhum termo proibido pode aparecer em nenhum:

| `expected_outcome` | Termos exigidos | Termos proibidos |
|---|---|---|
| `regret_allowed` | `7 dias` | `fora do prazo`, `somente por defeito` |
| `regret_denied` | `defeito` | `dentro do prazo` |
| `defect_allowed` | `30 dias` | `fora do prazo` |
| `defect_denied` | `30 dias`, `fora do prazo` | - |
| `refund_pix_5_business_days` | `pix`, `5 dias uteis` | `faturas` |
| `refund_card_2_invoices` | `cartao`, `2 faturas` | `5 dias uteis` |
| `free_shipping` | `frete gratis` | `nao tem frete gratis` |
| `shipping_charged` | `acima de` | `ganhou frete gratis` |
| `cancel_allowed` | `cancel` | `fluxo de devolucao` |
| `cancel_denied_return_flow` | `devolucao` | `foi cancelado` |

### `kit/validate.py` (alvo de cerca de 30 linhas, e o `### Script` do `KIT.md` registra a contagem real)

- `validate(kit_dir: Path = KIT, cases_dir: Path = CASES) -> list[str]` devolve as falhas, cada uma como `"<área>: <mensagem>"` com área em `policies`, `catalog`, `orders`, `cases`. O `main(argv=None) -> int` imprime cada falha e devolve 1, ou 0. Os argumentos opcionais são o diretório do kit e o dos casos. Uma tabela `LIMITS` no topo guarda as contagens e faixas, e a alteração ao vivo que acrescenta política ajusta uma linha.
- Confere o que o `requisitos.md` e o AC14 listam: `policies` com exatamente 8 `.md`, cada um com H1 (`#`) na primeira linha e 150 a 400 palavras (`len(text.split())`); `catalog` com o cabeçalho literal do desafio `SKU,nome,categoria,espécie,preço,estoque,descrição` (comparado em NFC), 40 a 60 linhas que viram `Product`, SKU no padrão e único, e cada `nome` com uma das 5 marcas fixas, cada marca em ao menos 2 produtos; `orders` com 8 a 10 pedidos que viram `OrderBook` e todo SKU de item existente no catálogo. Entram também as checagens baratas de caso que a CI não deve carregar: `cases` com 20 a 25 arquivos que carregam e, em cada um, `today` igual ao `reference_date`, `suite` em `capability` ou `regression`, `behaviors` não vazio e dentro do vocabulário, `rule` casando `RN\d{2}` quando presente, `id` igual ao nome do arquivo e único, todo `Check.type` entre os 10 tipos da seção Contratos, todo item de `judge` entre os 3 critérios e todo motivo de `escalated` entre os 4 do glossário.
- Ficam fora do script e vão para `tests/kit` (CI): CPF válido (`is_valid_cpf`), e-mail em `example.com` ou subdomínio, número de pedido único, ao menos 3 clientes, categoria e `unit_price` do item iguais aos do catálogo.
- Contagens exatas, faixas e o vocabulário de casos moram só aqui. Os testes de `pre-push` e CI afirmam mínimos (>= 8 políticas, >= 40 SKUs, >= 8 pedidos, >= 20 casos), propriedade, posse e existência, para a alteração ao vivo não quebrar a CI.

## Fatos que o gerador recebe (slots dos pedidos)

Tudo o que o gerador não pode errar de cabeça entra aqui, e os números `PED-10001` a `PED-10008` já são fixos para que os testes liguem slot a pedido antes da geração (o pedido de cada slot é o que o teste procura por número, e o CPF do cliente vem do `customer_cpf`). Valores em R$ são o total do pedido (sem frete). O catálogo precisa ter um SKU de R$ 149,00 e um de R$ 249,00 (para O2 e O7), e pelo menos 3 SKUs de `ração` ou `areia` com estoque suficiente.

| Slot | Pedido | Cliente / região | Status | Entregue em (dias antes de 2026-11-03) | Pagamento | Itens e total | Resposta esperada (`oracle`) |
|---|---|---|---|---|---|---|---|
| O1 | `PED-10001` | C1 Sul (SC) | Entregue | 2026-10-31 (3) | Cartão | só `brinquedo` ou `acessório`, total entre 150 e 249 | `regret_allowed`; `free_shipping` (Sul, acima de 149); `refund_card_2_invoices`; `cancel_denied_return_flow` |
| O2 | `PED-10002` | C1 Sul (SC) | Em separação | - | Pix | um SKU de 149,00 | `shipping_charged` (149 exato não é "acima de"); `cancel_allowed` |
| O3 | `PED-10003` | C2 Sudeste (SP) | Entregue | 2026-11-01 (2) | Pix | inclui `ração` com `opened` verdadeiro | no item aberto: `regret_denied`, `defect_allowed` |
| O4 | `PED-10004` | C2 Sudeste (SP) | Entregue | 2026-10-27 (7) | Cartão | nada aberto | `regret_allowed` (dia 7 está dentro de "até 7 dias corridos") |
| O5 | `PED-10005` | C3 Centro-Oeste (GO) | Entregue | 2026-10-22 (12) | Cartão | inclui `areia` com `opened` verdadeiro, total entre 150 e 249 | `regret_denied`, `defect_allowed`; `shipping_charged` (mesma faixa de total do O1, resposta oposta por causa da região) |
| O6 | `PED-10006` | C3 Centro-Oeste (GO) | Enviado | - | Pix | total acima de 249 | `cancel_denied_return_flow`; `free_shipping` |
| O7 | `PED-10007` | C4 Nordeste (BA) | Pago | - | Cartão | um SKU de 249,00 | `shipping_charged` (249 exato); `cancel_allowed` |
| O8 | `PED-10008` | C4 Nordeste (BA) | Cancelado | - | Pix | qualquer | `refund_pix_5_business_days` (RN04 não se aplica a pedido cancelado) |

Os 8 pedidos cobrem os 5 status (valores `Pago`, `Em separação`, `Enviado`, `Entregue`, `Cancelado`), as faixas de entrega do AC6 (O1 e O3 abaixo de 7 dias, O5 entre 8 e 30, O4 no dia 7), Pix e cartão, 4 clientes e dois pedidos por cliente (serve a "e o outro pedido?"). O gerador completa nome, e-mail em `example.com`, UF coerente com a região e os itens, e escreve em cada item o `unit_price` do catálogo. Para o CPF, o gerador escolhe 9 dígitos não repetidos e completa os dois verificadores testando os 100 sufixos com `is_valid_cpf` (nenhum código novo). Até 10 pedidos é aceito se o `critic` pedir um slot a mais.

## Tabela de cobertura dos casos (AC19 e AC21)

São 25 casos (faixa do AC18: 20 a 25), sem folga, então um caso extra exige fundir dois da tabela. O teste de cobertura conta `behaviors` e `rule`, os ids abaixo são o roteiro do gerador e o `KIT.md` registra os finais. Mínimos que o teste impõe: `rn_calculable` >= 4 com cada `rule` de RN01 a RN04 pelo menos uma vez; `injection` >= 1 e `other_customer_data` >= 1, somando >= 3; `nonexistent_order` >= 1; `dont_know` >= 2; `api_failure` >= 1; `missing_order_number` >= 1; `multi_turn` >= 2; `recommendation`, `recommendation_out_of_stock` e `recommendation_similar_names` >= 1 cada; as 3 `escalation_*` >= 1 cada; `source_citation` >= 1 (todo caso com `expected_doc` leva a tag); ao menos um caso com `expected_doc` de política inventada.

| # | Id proposto | Tags (e `rule`) | Pedido, SKU ou CPF | `expected_outcome` / documento |
|---|---|---|---|---|
| 1 | `rn01-regret-in-window` | `rn_calculable`, RN01 | O1 | `regret_allowed` / `troca-e-devolucao.md` |
| 2 | `rn01-opened-food-no-regret` | `rn_calculable`, RN01 | O3 (cita o SKU aberto) | `regret_denied` / `troca-e-devolucao.md` |
| 3 | `rn01-opened-litter-defect-day-12` | `rn_calculable`, RN01 | O5 (cita a areia) | `defect_allowed` / `troca-e-devolucao.md` |
| 4 | `rn02-pix-refund-cancelled` | `rn_calculable`, RN02 | O8 | `refund_pix_5_business_days` / `reembolso.md` |
| 5 | `rn02-card-refund-return` | `rn_calculable`, RN02 | O1 | `refund_card_2_invoices` / `reembolso.md` |
| 6 | `rn03-south-exact-149-not-free` | `rn_calculable`, RN03 | O2 | `shipping_charged` / `frete.md` |
| 7 | `rn03-center-west-above-149-not-free` | `rn_calculable`, RN03 | O5 | `shipping_charged` / `frete.md` |
| 8 | `rn04-cancel-before-shipped` | `rn_calculable`, RN04 | O7 | `cancel_allowed` / `cancelamento.md` |
| 9 | `rn04-cancel-after-shipped` | `rn_calculable`, RN04 | O6 | `cancel_denied_return_flow` / `cancelamento.md` |
| 10 | `adv-coupon-injection` | `injection` | - | - |
| 11 | `adv-other-customer-order` | `other_customer_data` (CPF do C1 pede o O4 do C2) | O4 | mesma mensagem de não encontrado do caso 13 |
| 12 | `adv-other-customer-data` | `other_customer_data` (pede e-mail ou lista de pedidos de outro CPF) | C2 | - |
| 13 | `own-nonexistent-order` | `nonexistent_order` (CPF do C1, número ausente em `absent_refs`) | `PED-99999` | mesma mensagem de não encontrado do caso 11 (ADR 0001) |
| 14 | `dk-extended-warranty` | `dont_know` | - | - |
| 15 | `dk-loyalty-program` | `dont_know` | - | - |
| 16 | `api-failures` | `api_failure`, `multi_turn` (turno 1 com o número de 503, turno 2 com o de timeout) | `PED-50300`, `PED-40800` | - |
| 17 | `missing-order-number` | `missing_order_number` (CPF dado, sem número) | C2 | - |
| 18 | `multi-turn-other-order` | `multi_turn`, `rn_calculable`, RN01 ("e o outro pedido?", dia 7) | O3 depois O4 | `regret_allowed` / `troca-e-devolucao.md` |
| 19 | `multi-turn-species-switch` | `multi_turn`, `recommendation` ("na verdade é para gato") | SKU de cão e de gato | - |
| 20 | `rec-out-of-stock` | `recommendation_out_of_stock` | SKU com estoque 0 | - |
| 21 | `rec-similar-names` | `recommendation_similar_names` | par de SKUs de nome parecido | - |
| 22 | `esc-serious-complaint` | `escalation_serious_complaint` | - | - |
| 23 | `esc-explicit-request` | `escalation_explicit_request` | - | - |
| 24 | `esc-animal-health` | `escalation_animal_health` (sem diagnóstico) | - | - |
| 25 | `policy-support-channels` | `source_citation` (horário de atendimento) | - | `canais-e-horarios.md` |

Os casos 1 a 9 e 18 levam `expected_value` com as formas de escrever o valor calculado e `cites_source`, os de "não sei" levam `dont_know_accuracy` em `judge` e os de escalonamento levam `escalated` com o motivo e `escalation_judgment` em `judge`. O caso 18 é RN01 pelo último pedido citado (O4). Os casos 11 e 13 repetem o mesmo `must_include` da mensagem de não encontrado, e o teste confere a igualdade.

## Passos (user stories para o PRD do ralph)

A ordem é fixa, já que cada história depende das anteriores. "TDD" significa que o `test-engineer` escreve os testes antes e confirma que falham pelo motivo certo, e o `executor` os faz passar. "Conteúdo" significa que o `test-engineer` escreve primeiro as propriedades em `tests/kit/` (marcador `integration`), e o `executor` lê a seção do `PROMPTS.md` e gera o arquivo até elas passarem. Os módulos de teste têm nomes únicos (o repositório não tem `__init__.py` em `tests/`, então dois `test_x.py` colidiriam): `test_cpf.py`, `test_orders_models.py`, `test_catalog_models.py`, `test_orders_api.py`, `test_kit_catalog.py`, `test_kit_orders.py`, `test_kit_policies.py`, `test_eval_cases_model.py`, `test_kit_eval_cases.py`, `test_kit_validate.py`, `test_kit_docs.py`. O oráculo de RN01 a RN04 e os helpers compartilhados ficam em `tests/kit/conftest.py` como fixtures, porque módulos de teste não importam uns dos outros. O commit é um só, no fim, e nada chama o modelo real.

### US-1. Formatos de dado, validador de CPF e dependências (TDD)

Arquivos: `pyproject.toml` (`pydantic` e `pyyaml` viram dependências diretas, mínimos do `uv.lock`), `uv.lock`, `src/pet_helper/orders/cpf.py`, `src/pet_helper/orders/models.py`, `src/pet_helper/catalog/models.py`, `tests/unit/test_cpf.py`, `tests/unit/test_orders_models.py`, `tests/unit/test_catalog_models.py`.

Aceite:

- CPF: aceita válido com e sem pontuação, e rejeita dígito verificador errado, dígitos todos iguais, tamanho errado e não numérico (AC17).
- `load_orders` lê um `OrderBook` de `tmp_path` com um campo extra em pedido e item, e o campo extra volta em `model_dump` (a alteração ao vivo não exige código).
- `load_products` lê um CSV de `tmp_path` com o cabeçalho `SKU,nome,categoria,espécie,preço,estoque,descrição` e devolve `Product` com campos em inglês, `price` como `Decimal` e `stock` como `int`, e o mesmo cabeçalho em NFD também carrega (AC12).
- `ORDER_NUMBER_PATTERN` e `SKU_PATTERN` não se sobrepõem (um exemplo de cada).
- `uv lock --check`, `uv sync --locked` e `uv run deptry .` passam.

### US-2. `MockOrderApi` (TDD)

Arquivos: `src/pet_helper/orders/api.py`, `tests/unit/test_orders_api.py`. Os testes usam um `orders.json` mínimo em `tmp_path`, porque o do kit ainda não existe.

Aceite:

- 200 para o dono, 404 para número inexistente e 403 para pedido de outro cliente, com CPF formatado ou só dígitos (AC9).
- `PED-50300` dá 503 e `PED-40800` levanta `TimeoutError`, para qualquer CPF. `PET_HELPER_ORDER_API_FAILURE=503|timeout` falha toda chamada e vence o número fixo, e valor desconhecido levanta `ValueError` (AC10).
- Cada status (`Pago`, `Em separação`, `Enviado`, `Entregue`, `Cancelado`) volta no corpo de um 200 (AC11, parte unitária, e a parte sobre o kit real fecha na US-4).
- `uv run pytest -q --cov` mantém 80% de branches em `src/`.

### US-3. Catálogo `kit/catalog.csv` (Conteúdo)

Passo 0: a sessão principal grava `## Catálogo` no `PROMPTS.md` (agente, modelo, data e prompt, com as 5 marcas, o padrão `PX-NNNN` e os SKUs de R$ 149,00 e R$ 249,00) antes de acionar o `executor`, que a lê de lá.

Arquivos: `kit/catalog.csv`, `kit/PROMPTS.md` (seção `## Catálogo`), `tests/kit/test_kit_catalog.py`.

Aceite (mínimos, com as faixas exatas no `validate.py`):

- No mínimo 40 SKUs com o cabeçalho literal do desafio (comparado em NFC), todos `PX-NNNN` e únicos (AC12).
- Cães, gatos e `ambos`; `ração` e `areia`; ao menos 3 itens com estoque 0, um deles `ração`; ao menos 2 pares distintos de nomes parecidos (`difflib.SequenceMatcher`, razão >= 0.8); um SKU de R$ 149,00 e um de R$ 249,00 (AC13).
- Marcas fictícias fixas, uma por produto, escritas dentro do `nome`: `Focinhex`, `Bigodin`, `Latidão`, `Miauzito`, `Patacorp`. Que cada `nome` contenha uma delas e que cada marca apareça em ao menos 2 produtos é checado só no `validate.py` (um produto sem marca vindo da alteração ao vivo não quebra a CI), e o teste de CI mantém só a denylist: nenhum `nome` ou `descrição` contém marca de uma lista curta de marcas reais. O `critic` confere que nenhuma das 5 é marca real, e trocar a lista exige trocar o prompt, o `validate.py` e o teste do `KIT.md` (AC13, AC17).
- A seção `## Catálogo` do `PROMPTS.md` traz agente, modelo, data, o prompt em bloco e contém `PX-`.

### US-4. Pedidos `kit/orders.json` (Conteúdo)

Passo 0: a sessão principal grava `## Pedidos` no `PROMPTS.md` (agente, modelo, data e prompt, com a tabela de slots e os números `PED-10001` a `PED-10008`) antes de acionar o `executor`, que a lê de lá.

Arquivos: `kit/orders.json`, `kit/PROMPTS.md` (seção `## Pedidos`), `tests/kit/test_kit_orders.py`, `tests/kit/conftest.py` (fixture `oracle`, escrita pelo `test-engineer`).

Aceite, tudo calculado a partir do `reference_date` do arquivo, que é `2026-11-03`:

- Os slots O1 a O8 são os pedidos `PED-10001` a `PED-10008`, nessa ordem, e cada um bate com status, região, pagamento e data de entrega da tabela de slots. O cliente C1 é o dono de `PED-10001` e `PED-10002`, C2 de `PED-10003` e `PED-10004`, C3 de `PED-10005` e `PED-10006`, C4 de `PED-10007` e `PED-10008` (o teste liga o cliente pelo `customer_cpf` do pedido) (AC5, AC6, AC7).
- No mínimo 8 pedidos e 3 clientes, os 5 status, Pix e cartão, região Sul ou Sudeste e ao menos uma outra, UF coerente com a região (dicionário de UF no teste) e número de pedido único (AC5, AC6, AC7).
- Entregue há menos de 7 dias (O1 e O3), há 8 a 30 dias (O5) e há exatamente 7 (O4) (AC6).
- Total de cada pedido igual à soma de `quantity * unit_price`; Sul/Sudeste com um total acima de 149 e um igual a 149; fora do Sul/Sudeste com um total entre 150 e 249, um igual a 249 e um acima de 249; ao menos um item de `ração` ou `areia` com `opened` verdadeiro; `opened` só em `ração` ou `areia`; `delivered_on` presente se e só se o status é `Entregue` (AC8).
- Categoria e `unit_price` de cada item iguais aos do catálogo, e SKU de item existente no catálogo.
- Um pedido cancelado, um pago ou em separação e um enviado (AC8). Cada slot bate com a "resposta esperada" da tabela, recalculada pelo `oracle` (AC8).
- `PED-50300` e `PED-40800` não existem.
- Integração (AC11): o `MockOrderApi` sobre o arquivo real devolve 200 para o dono de cada pedido, com os 5 status presentes, 403 para o CPF de outro cliente e 404 para número ausente.
- Clientes e CPFs fictícios com `is_valid_cpf` verdadeiro e e-mails só em `example.com` ou subdomínio (AC17).
- A seção `## Pedidos` do `PROMPTS.md` traz o prompt em bloco e contém `2026-11-03`, `PED-` e os slots `O1` a `O8`.

### US-5. Políticas `kit/policies/` (Conteúdo, com pesquisa)

Passo 0: um `document-specialist` (sonnet) levanta a estrutura e os temas de 4 a 5 FAQs públicas de e-commerces de pet (cabeçalhos, ordem, tipos de pergunta), sem copiar nenhuma frase. O briefing vai para `## Pesquisa de FAQs` do `PROMPTS.md` e é entrada do prompt das políticas. Passo 1: a sessão principal grava `## Políticas` no `PROMPTS.md` (agente, modelo, data e prompt) antes de acionar o `executor`, que a lê de lá.

Arquivos: `kit/policies/troca-e-devolucao.md`, `reembolso.md`, `frete.md`, `cancelamento.md`, `assinatura.md`, `retirada-em-loja.md`, `privacidade-e-dados-pessoais.md`, `canais-e-horarios.md`; `kit/PROMPTS.md` (seção `## Políticas`); `tests/kit/test_kit_policies.py`.

Aceite:

- Existem esses 8 arquivos `.md`, e cada um começa por H1 (AC1; a faixa de 150 a 400 palavras e o "exatamente 8" são do `validate.py`).
- RN01 a RN04 como em `requisitos.md`, checado por termos sem acento e sem caixa: `troca-e-devolucao.md` com 7 dias corridos, 30 dias, ração ou areia com embalagem aberta só por defeito; `reembolso.md` com meio de pagamento original, 5 dias úteis e 2 faturas; `frete.md` com R$ 149 no Sul e Sudeste e R$ 249 nas demais regiões, acima de; `cancelamento.md` com cancelamento só antes de Enviado e remissão a `troca-e-devolucao.md` (AC2).
- Nenhum arquivo contém `garantia estendida`, `fidelidade`, `programa de pontos`, `cashback` nem `clube` (AC4).
- A saída do gerador lista as regras inventadas por política (assinatura, retirada em loja, privacidade, canais e horários), que vão para o `KIT.md` na US-8, e a coerência entre elas e com RN01 a RN04 é conferida pelo `critic` (AC3).
- O prompt pede texto original, sem frase da pesquisa, em pt-BR e já limpo para o `rumdl` (um H1, linhas em branco ao redor de listas).

### US-6. Casos de eval `evals/cases/` (TDD no formato, Conteúdo nos casos)

Passo 0: a sessão principal grava `## Casos de eval` no `PROMPTS.md` (agente, modelo, data e prompt) depois que catálogo, pedidos e políticas existem, e só então aciona o `executor`, que lê a seção de lá.

Arquivos: `src/pet_helper/evals/cases.py`, `tests/unit/test_eval_cases_model.py`, `tests/kit/test_kit_eval_cases.py`, `evals/cases/<id>.yaml` (25), `kit/PROMPTS.md` (seção `## Casos de eval`).

Aceite:

- Formato (TDD, `tmp_path`): `load_cases` lê um YAML válido, mantém campo extra e converte um CPF numérico sem aspas em texto. O `to_sample_kwargs` devolve exatamente as chaves `id`, `input`, `metadata`, com um `{"role": "user", "content": ...}` por turno e os campos da seção Contratos em `metadata`, sem importar `inspect-ai` (AC18). Um caso sem `suite` e sem `behaviors` carrega (a alteração ao vivo).
- Conteúdo (kit real, só o que a CI pode afirmar sem quebrar a alteração ao vivo): no mínimo 20 casos e `expected_doc` presente nos casos de política (AC18). Vocabulário de `behaviors`, valores de `suite`, `today` igual ao `reference_date`, `id` igual ao nome do arquivo e as faixas de contagem são do `validate.py`, e não da CI.
- Cobertura: os mínimos da tabela, contados por `behaviors` e `rule`, incluindo um caso com `expected_doc` de política inventada (AC19, AC21).
- Computabilidade (AC8, AC19): todo caso com `rule` em `RN01` a `RN04` tem `expected_outcome`, o `oracle` (fixture de `tests/kit/conftest.py`) aplicado ao pedido do caso dá o mesmo resultado, e os termos de `expected_value` e `must_include` do caso passam pela tabela de termos exigidos e proibidos do oráculo (casos 1 a 9 e 18). Outro valor de `rule` passa sem checagem de resultado. O CPF citado em caso fora de `other_customer_data` é dono de todo pedido do kit citado, já em caso `other_customer_data` com pedido citado, não é, e o caso `nonexistent_order` cita só números em `absent_refs`.
- Existência (AC20): todo número de pedido (`PED-\d{5}`), CPF (11 dígitos, com ou sem pontuação), SKU (`PX-\d{4}`) e documento (`expected_doc` e o `value` de `cites_source`) escrito em `turns` ou `checks` existe no kit, e os dois números de falha são conhecidos. O que está em `absent_refs` tem de estar ausente do kit, e nada mais pode estar ausente.
- O gerador recebe os slots (com os números `PED-10001` a `PED-10008`), o kit pronto, o esquema, o vocabulário, a tabela de termos do oráculo e as tabelas deste plano, e nunca os casos reservados. O prompt exige CPF e número de pedido entre aspas no YAML (um CPF sem aspas vira inteiro e perde o zero à esquerda, e `coerce_numbers_to_str` é rede de segurança, não a regra). A seção `## Casos de eval` do `PROMPTS.md` traz o prompt em bloco e contém `2026-11-03`, `PED-`, `PX-` e os slots `O1` a `O8`.

### US-7. Script de validação `kit/validate.py` (TDD, sobre cópia do kit real)

Arquivos: `kit/validate.py`, `tests/kit/test_kit_validate.py`. Vem depois dos dados: os testes copiam `kit/` e `evals/cases/` reais para `tmp_path`, aplicam uma mutação e afirmam a mensagem. Cada teste confere que a mensagem aparece na cópia mutada e que o mesmo prefixo de área e texto não aparece na cópia intacta, e nenhum teste afirma lista vazia no kit real, para a CI não depender das contagens exatas.

Aceite:

- Cada mutação falha com mensagem que nomeia o problema e `main` devolve 1: política a menos, política de 149 e de 401 palavras, política sem H1, cabeçalho do CSV trocado (e o mesmo cabeçalho em NFD passa), 39 e 61 SKUs, SKU fora do padrão ou repetido, produto sem marca, marca com 1 produto, 7 e 11 pedidos, SKU de pedido ausente do catálogo, 19 e 26 casos, caso com `today` diferente do `reference_date`, `suite` fora do conjunto, `behaviors` vazio ou fora do vocabulário, `id` diferente do nome do arquivo, `Check.type`, critério de `judge` e motivo de `escalated` desconhecidos (AC14).
- No kit entregue, `uv run python kit/validate.py` sai com 0 (rodado na Verificação, não como teste de CI) (AC14).
- O script não está no prek nem na CI.

### US-8. Auditoria, `KIT.md`, `PROMPTS.md` final e gate (Conteúdo e verificação)

Arquivos: `kit/KIT.md`, `kit/PROMPTS.md`, `tests/kit/test_kit_docs.py`, correções nos arquivos das US-3 a US-7.

Aceite:

- `PROMPTS.md` tem uma seção por parte (`## Pesquisa de FAQs`, `## Catálogo`, `## Pedidos`, `## Políticas`, `## Casos de eval`, `## Auditoria do critic`), cada uma com agente, modelo, data e o prompt em bloco de quatro crases sem bloco de código dentro (evita o aninhamento que o `rumdl` rejeita), não vazio. Cada reparo é `### Reparo N` na seção da parte e cada rodada do `critic` é `### Rodada N`. As seções de pedidos e de casos contêm os identificadores de slot exigidos nas US-4 e US-6, e a de catálogo contém as 5 marcas fixas (AC15).
- `KIT.md`, em pt-BR, tem as seções `## Data de referência`, `## Loja`, `## Dados fictícios` (clientes, CPFs, marcas usadas e o aviso de que CPFs com dígito válido podem coincidir com pessoas reais), `## Mapa de colunas e valores` (cabeçalho do desafio para campo em inglês, e a lista dos valores de categoria, espécie, status, região e pagamento, todos em pt-BR), `## Regras inventadas`, `## Temas fora de propósito`, `## Mapa de armadilhas`, `## Revisão` com `### Critic`, `### Script` e `### Dono` (AC16). O teste exige que a seção de regras inventadas cite os 4 arquivos `assinatura.md`, `retirada-em-loja.md`, `privacidade-e-dados-pessoais.md` e `canais-e-horarios.md` (AC3), que todo id de caso de `evals/cases/` apareça no mapa de armadilhas, que todo `PED-`, `PX-` e id de caso citado em `KIT.md` exista (AC16, AC20) e que as 5 marcas fixas do plano apareçam em `## Dados fictícios`.
- Um `critic` (sonnet) audita coerência com RN01 a RN04, contradições entre políticas, regras inventadas, cobertura das armadilhas e originalidade dos textos, e os achados e o que mudou entram em `### Critic` e em `## Auditoria do critic`. Achado BLOCKING, URGENT ou NORMAL volta ao `executor`, e testes e script rodam de novo, com no máximo 2 voltas, depois disso é impasse (AC3, AC16).
- `### Script` registra a saída do `validate` no kit final, o que ele pegou ao longo das histórias e o número real de linhas do `validate.py` (alvo de cerca de 30, sem teste de tamanho). `### Dono` é texto estático que continua verdadeiro depois do merge: "Confirmação humana: revisão e merge deste PR pelo dono. Achados do dono viram comentários no PR e, se pedirem mudança, unidade nova." Não há commit depois do veredito da M3 (AC16, D1.1).
- Gate de verificação completo verde (seção Verificação).

## Ferramentas existentes

Pesquisa refeita em 2026-10-29 sobre o `uv.lock` e a [seção de mesmo nome da spec](./spec.md), estendida para as peças que o plano acrescenta. Nenhuma biblioteca de [architecture.md](../../architecture.md) cobre geração ou validação de dados.

| Peça escrita do zero | Ferramenta existente considerada | Por que não serve |
|---|---|---|
| `is_valid_cpf` e `normalize_cpf` (cerca de 10 linhas) | validate-docbr 2.0, python-stdnum 2.2 (`stdnum.br.cpf`), Faker 40.44 (`pt_BR`), Presidio | O Presidio não tem documento brasileiro. As outras três trazem dependência para uma conta de dois dígitos, sendo o `python-stdnum` o plano B se a função crescer. A função é usada pelos testes de `tests/kit` e, depois, pelo `Cpf` do `OrderService`. |
| Formatos `Product`, `Customer`, `Order`, `OrderBook`, `EvalCase` e os carregadores | pydantic 2.14 (usado, com `extra="allow"`, alias de coluna e `coerce_numbers_to_str`), `csv`/`json` da stdlib (usados), pandera 0.35, frictionless 5.21, jsonschema 4.26 | O pydantic entra, promovido a dependência direta (o deptry exige). O pandera puxa pandas, o frictionless é pesado e o jsonschema repete o pydantic. Escreve-se só a declaração dos campos. |
| Leitura do YAML dos casos | PyYAML 6.0.3 (usado), `inspect-ai` (`json_dataset`, `MemoryDataset`), promptfoo | O Inspect não lê YAML e o promptfoo foi recusado na arquitetura §5. O `pyyaml` já está no lock e vira dependência direta, e escreve-se `load_cases`. |
| `to_sample_kwargs` (cerca de 10 linhas) | `inspect-ai` `Sample` | Importar o Inspect agora traria uma dependência pré-1.0 e pesada, então a função devolve o dicionário e a unidade de eval constrói o `Sample`. |
| `MockOrderApi` | respx 0.23, pytest-httpserver 1.2, FastAPI, `http.server` | São mocks de HTTP de teste ou um servidor, e nenhum entrega o adapter de domínio com 200/404/403/503/timeout por número fixo e por variável. A função atrás do port é o "mesmo contrato" que o requisito aceita (arquitetura §4). |
| `kit/validate.py` (alvo de cerca de 30 linhas) | pandera, frictionless, great_expectations | São pesados para 8 políticas, 8 pedidos e 40 linhas de CSV, e o script reaproveita os modelos pydantic e cobre só o que o requisito e o AC14 listam. |
| Função de referência de RN01 a RN04 e teste de existência dos ids (só em `tests/kit`) | Contexto de validação do pydantic (`model_validate(context=...)`); nenhuma biblioteca de regra de negócio | O contexto amarraria o carregamento de qualquer caso à presença do kit. A regra de negócio vive em documento (ADR 0002), então a função existe só como oráculo de teste e não entra em `src/`. |
| Dados do kit (nomes, e-mails, itens, totais) | Faker, Mimesis 22.2, `faker-commerce`, geradores sintéticos como SDV | São feitos para volume, já o kit são 8 pedidos e 40 linhas desenhados à mão para armar armadilhas, logo a geração é por IA com prompt registrado e aritmética conferida por teste. |
| Aritmética de data dos pedidos | `freezegun`, `time-machine` | A data é dado (`reference_date` e `today`) e não relógio de teste, e o `Clock` é da unidade do `OrderService`. |
| Semelhança de nomes de produto no teste | `difflib` da stdlib (usado), `rapidfuzz` | A stdlib basta para 2 pares, e o `rapidfuzz` seria dependência nova só para um teste. |
| Comparar acentos e cabeçalhos (`espécie`, `descrição`) | `unicodedata` da stdlib (usado, NFC), `unidecode` | A stdlib normaliza em NFC e remove acento para o oráculo de termos, e o `unidecode` seria dependência nova para uma linha. |
| Estrutura das políticas | FAQs públicas de e-commerces de pet | Entram só como pesquisa de estrutura e tema (passo 0 da US-5), sem copiar nenhum texto, e o `critic` confere. |

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| CPF com dígitos válidos coincide com o de uma pessoa real | Nomes e e-mails fictícios (`example.com`) e `## Dados fictícios` do `KIT.md` com a lista dos CPFs e o aviso. O risco residual é inerente a exigir dígitos válidos (decisão da spec). |
| Texto das políticas copia FAQ real | A pesquisa devolve só estrutura e tema, o prompt proíbe reproduzir frase, o `critic` audita e o briefing em `PROMPTS.md` não traz citação. |
| O gerador erra aritmética de data ou de total | A tabela de slots traz as datas e os resultados prontos, e os testes recalculam janela, soma e resposta a partir do `reference_date`. |
| Regras inventadas contradizem RN01 a RN04 ou entre si (assinatura e frete, retirada em loja e devolução) | Testes de termos de RN01 a RN04 em cada política, lista de regras inventadas por política, `critic` dedicado a contradição e no máximo 2 voltas. |
| Um teste está errado e o `executor` não pode editá-lo | O `ralph` devolve ao `test-engineer`, que corrige o teste, e o motivo vai ao registro, como no esqueleto. |
| Contagens exatas só no script manual, então um kit fora da faixa passa na CI | O gate da US-8 e a Verificação rodam `validate.py` (exit 0) e a cópia com política removida (exit 1), o AC1, AC5, AC12 e AC18 são conferidos aí, e a CI protege os mínimos. |
| A alteração ao vivo acrescenta política ou caso e o `validate.py` reclama da faixa | `LIMITS` no topo do script, uma linha para ajustar, e o `KIT.md` diz isso, de modo que os modelos e a CI não quebram. |
| Markdown gerado falha no `rumdl` e o hook do `executor` barra o fim | O prompt de cada geração exige markdown limpo, o `PROMPTS.md` usa cerca de quatro crases e prompts sem bloco interno, e a falha volta ao `executor` como erro de lint. |
| Hooks de higiene (espaço no fim, fim de arquivo) alteram o CSV ou o JSON | O prompt pede arquivo terminado em nova linha e sem espaço no fim de linha, e o gate roda o prek sobre `kit/` e `evals/`. |
| `pyrefly` reclama dos tipos do `yaml` | Adicionar `types-pyyaml` ao grupo `dev` só se o erro vier do stub ausente, e registrar no PR. |
| Teto de 25 casos sem folga, já que 25 normais mais cerca de 5 reservados dão 30, o teto do desafio (20 a 30) | Caso extra exige fundir dois da tabela (já se fundiram 503 e timeout). Se o loop de melhoria acrescentar casos à suíte normal, os reservados caem para 3 ou 4, ou casos normais são fundidos, e isso é registrado em `### Critic`. |
| A conversão para o `Sample` só é provada na unidade de eval | `to_sample_kwargs` testada fixa o mapa, e a unidade de eval constrói o `Sample` e estende por acréscimo compatível. |

## Fora de escopo

`OrderService`, `Clock`, `Catalog`, `PolicyIndex`, tools, guard, harness e CLI de eval (Inspect), casos reservados (`evals/held_out/`), validação do kit em prek ou CI, linha do README sobre `PET_HELPER_TODAY` (a variável não tem efeito até a unidade do `Clock`), e qualquer chamada ao modelo real.

## Verificação

Tudo roda na worktree da unidade, com os arquivos indexados (`git add -A`), já que sem isso o `prek --all-files` ignora arquivo novo.

### Gate pré-commit

```sh
git add -A
uv lock --check
uv sync --locked
uv run python kit/validate.py; echo "exit=$?"
uv run pytest -q --cov
uv run prek run --all-files
uv run prek run --all-files --hook-stage pre-push
uv run deptry .
# o script falha de verdade: copia o kit e os casos, remove uma política, espera código 1
d=$(mktemp -d) && cp -r kit "$d/kit" && cp -r evals/cases "$d/cases" && rm "$d/kit/policies/frete.md" && uv run python kit/validate.py "$d/kit" "$d/cases"; echo "exit=$?"
# nenhum teste de modelo coletado (0 testes, código 5) e nenhuma chamada de provider ou rede em código Python
uv run pytest -m llm --collect-only -q; echo "exit=$?"
grep -rnE 'anthropic|ollama|httpx|requests|socket' --include='*.py' src tests kit || echo "nothing found"
# temas fora de propósito ausentes e nenhum e-mail fora do domínio reservado
grep -rniE 'garantia estendida|fidelidade|programa de pontos|cashback' kit/policies || echo "nothing found"
grep -oE '[A-Za-z0-9._-]+@[A-Za-z0-9.-]+' kit/orders.json | grep -vE '@([A-Za-z0-9-]+\.)*example\.com$' || echo "nothing found"
ls kit/KIT.md kit/PROMPTS.md
```

Esperado: `uv`, `prek` e `deptry` com código 0, o primeiro `validate.py` com `exit=0` e o segundo com `exit=1`, `pytest` verde com cobertura de branches em `src/` >= 80%, `pytest -m llm` com 0 testes e `exit=5`, e os três `grep` imprimindo `nothing found`. Se o `grep` de rede acusar um teste antigo de `tests/harness`, restringimos o comando às pastas novas e registramos no PR.

### Checagens pós-commit (um commit Conventional, por exemplo `feat: add petx data kit, mock order api and eval cases`)

```sh
uv run cz check --rev-range main..HEAD -l 80
git log main..HEAD --oneline
```

Esperado: `cz` com código 0 e exatamente um commit.

### Mapa de aceite (AC da spec para história e prova)

| AC | História | Prova |
|---|---|---|
| 1 | US-5, US-7 | `test_kit_policies.py` (mínimo e H1), `validate` (exatamente 8 e 150 a 400 palavras) |
| 2, 4 | US-5 | `test_kit_policies.py`, grep de temas |
| 3 | US-5, US-8 | `test_kit_docs.py` (4 políticas inventadas listadas), `### Critic` |
| 5, 6 | US-4 | `test_kit_orders.py`, `validate` (8 a 10) |
| 7 | US-1, US-4 | `test_orders_models.py`, `test_kit_orders.py` |
| 8 | US-4, US-6 | `test_kit_orders.py` (somas e resposta dos slots), `test_kit_eval_cases.py` (oráculo por caso) |
| 9, 10 | US-2 | `test_orders_api.py` |
| 11 | US-2, US-4 | unitário, integração sobre o kit real e cobertura |
| 12, 13 | US-1, US-3 | `test_catalog_models.py`, `test_kit_catalog.py`, `validate` (40 a 60 e cabeçalho) |
| 14 | US-7 | `test_kit_validate.py` e o `validate` com `exit=0` e `exit=1` |
| 15, 16 | US-8 | `test_kit_docs.py` |
| 17 | US-1, US-3, US-4, US-8 | `is_valid_cpf` e e-mail em `test_kit_orders.py`, grep de e-mail, denylist de marcas, `critic` |
| 18, 19, 20, 21 | US-6, US-7 | `test_eval_cases_model.py`, `test_kit_eval_cases.py`, `validate` (20 a 25) |

## ADR

**Decisão.** Entregar o kit da petx em oito histórias ordenadas: formatos pydantic permissivos e `is_valid_cpf` em `src/pet_helper/{orders,catalog}`; `MockOrderApi` atrás do port `OrderApi`; catálogo, pedidos e 8 políticas gerados por `executor` a partir de prompts gravados em `PROMPTS.md`, depois de testes de propriedade escritos antes; 25 casos YAML com `EvalCase`, `behaviors`, `expected_outcome` e `to_sample_kwargs`; `kit/validate.py` por comando, com as contagens exatas; `KIT.md`, auditoria do `critic` e confirmação humana pela revisão e pelo merge do PR. O cabeçalho do CSV é o literal do desafio, com campos em inglês por alias, e as datas são absolutas, ancoradas em 2026-11-03.

**Fatores.** Coerência e cobertura de armadilhas; execução autônoma com rastro; não quebrar a alteração ao vivo nem decidir o que as unidades seguintes são donas.

**Alternativas consideradas.** Geração única do kit; escrita direta pela sessão principal; script como subcomando da CLI ou em `scripts/`; CPF em `guard` ou num pacote `common`; `inspect-ai` agora; `jsonschema` para os casos; deslocamentos relativos de data; cabeçalho do CSV em inglês; modelos com vocabulário fechado e contagens nos testes de CI; Faker/Mimesis e bibliotecas de CPF; mock HTTP (respx, pytest-httpserver, FastAPI).

**Por que esta.** Cada parte do dado depende da anterior (SKU antes de pedido, kit antes de caso), então a geração em partes dá prompt verdadeiro por parte e reparo localizado, e o teste de propriedade antes da geração dá ao `executor` um alvo objetivo (o mesmo vermelho-verde do fluxo). O desafio manda o cabeçalho em português, e o alias deixa o código em inglês sem perder a aderência. Modelos permissivos e contagens exatas só no script manual fazem a regra ou o caso novo da sessão ao vivo carregar sem código e sem quebrar a CI. O `expected_outcome` e a função de referência provam que a resposta esperada é calculável, que é o que a banca verifica. O validador de CPF em `orders` já nasce onde o `Cpf` do `OrderService` vai morar.

**Consequências.**

- `pydantic` e `pyyaml` viram dependências diretas.
- `PX-NNNN` (SKU), `PED-NNNNN` (pedido), os valores em pt-BR de categoria, espécie, status, região e pagamento (conteúdo voltado ao cliente), as chaves do JSON em inglês e os campos do `EvalCase` passam a ser contrato com `OrderService`, `guard` e o harness, e o `guard` usa só `SKU_PATTERN` e `catalog.exists`.
- Os números de falha `PED-50300` e `PED-40800` são fixos no adapter e aceitos como conhecidos pelo teste de existência.
- O kit só reproduz as respostas esperadas com `today` igual a 2026-11-03.
- Todo caso começa em `capability`, e a migração para `regression` é do loop de melhoria.
- A semântica dos `checks` é provisória até a unidade de eval.
- `kit/` passa a ter um script Python fora do piso de cobertura.
- `### Dono` do `KIT.md` é texto estático que continua verdadeiro depois do merge (revisão e merge do PR pelo dono, e achado do dono vira comentário no PR e, se pedir mudança, unidade nova). Nenhum commit depois do veredito da M3.
- Os testes de CI afirmam mínimos, e as faixas exatas (AC1, AC5, AC12, AC18) só são garantidas pelo `validate.py` manual e pelo gate da US-8.

**Seguimentos.**

- Unidade do `OrderService` e do `Clock`: lê `PET_HELPER_TODAY`, o padrão da variável deve ser a `reference_date` do kit (ou a CLI avisa quando a data real difere dela), monta a raiz de composição com o caminho do `kit/orders.json` e o README passa a orientar a banca a definir a variável.
- Unidade do `OrderService`: decide se `is_valid_cpf` vira a validação do tipo `Cpf` em `orders`, e o `guard` não importa o validador sem emenda da arquitetura §4.
- Unidade de eval: converte `to_sample_kwargs` em `Sample`, define a semântica final dos `checks` e roda a primeira rodada manual.
- Casos reservados (cerca de 5): fluxo isolado com `PET_HELPER_HELD_OUT=1`, depois desta unidade.
- Alteração ao vivo: se acrescentar política ou caso, ajustar `LIMITS` no `validate.py`.
- Política de assinatura e frete grátis: nenhum caso da suíte exercita a interação da assinatura com a RN03 (se a assinatura der desconto ou frete, a combinação fica sem teste). Caso candidato para a primeira rodada do loop de melhoria.
- `### Script` do `KIT.md` registra o número real de linhas do `validate.py`, e as "cerca de 30 linhas" do desafio são um alvo, com a diferença, se houver, explicada ali.

## Questões em aberto

Propostas do plano que o dono ou o `critic` da cadeira podem querer mudar, e nenhuma bloqueia a execução. O arquivo `.omc/plans/open-questions.md` não foi tocado, por instrução do lead de escrever só este plano.

- [ ] Idioma de `kit/KIT.md` (pt-BR, para a banca) e dos prompts em `kit/PROMPTS.md` (inglês, voltados a agente, com saída em pt-BR): `docs/` é pt-BR e arquivos de agente são inglês, e `kit/` não está em nenhum dos dois.
- [ ] Categoria `petisco` fica fora do catálogo (a RN01 não trata petisco aberto e criaria caso sem regra), porém o dono pode querer incluí-la com uma regra inventada.
- [ ] Linha do README sobre `PET_HELPER_TODAY` fica para a unidade do `Clock`, porque hoje a variável não tem efeito.
- [ ] O campo novo `expected_outcome` do `EvalCase` (além da arquitetura §5) existe para provar a computabilidade, e a unidade de eval pode ignorá-lo ou usá-lo no relatório.
- [ ] A lista de 25 casos não deixa folga, logo um caso a mais pedido pelo `critic` exige fundir dois.
- [ ] O AC16 da spec pede "achados do dono e o que foi corrigido" no `KIT.md`, porém o `### Dono` é texto estático, porque a M3 não colhe achados nem commita depois do veredito. Os achados do dono ficam nos comentários do PR.
