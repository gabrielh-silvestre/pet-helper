# Kit de dados sintético da petx

Status: aprovado em 29/10/2026. A entrevista foi em 29/10/2026 e terminou com ambiguidade de 16%. A versão completa, com transcrição e pontuação, fica em `.omc/specs/deep-interview-data-kit.md`.

## Objetivo

É o kit de dados sintético que o desafio pede na seção "Kit" da [requisitos.md](../../requisitos.md), montado para a loja fictícia **petx**. Ele traz 8 políticas que refletem RN01 a RN04 sem contradição e inventam as demais regras, de 8 a 10 pedidos, a API mock de pedidos, um catálogo de 40 a 60 SKUs, o script de validação, o `PROMPTS.md` e o `KIT.md` com a revisão crítica.
Junto com o kit sai a suíte normal de eval, com 30 casos YAML. Cada armadilha do kit e cada comportamento do assistente pedido no desafio ganha pelo menos um caso, conforme a premissa `comportamento-nasce-como-eval`.

## Decisões da entrevista

- A data de referência do kit é **2026-11-03**, lida de `PET_HELPER_TODAY`. As datas do JSON são absolutas e calculadas a partir dela, a eval fixa essa data em cada caso, e o README orienta a banca a conversar com a variável definida.
- A loja se chama **petx**, e os e-mails falsos usam um domínio reservado (`example.com` ou um subdomínio dele).
- As regras inventadas (assinatura, retirada em loja, privacidade, canais e horários) ficam a cargo da IA, que toma FAQs de e-commerces reais como base de estrutura e de temas. Porém nenhum texto é copiado, porque a requisitos.md exige "nada copiado de políticas reais".
- Os temas que ficam de fora de propósito são **garantia estendida** e **programa de fidelidade**. Nenhuma política os cobre, nem de passagem.
- A revisão crítica tem três etapas: um subagente critic audita a coerência com RN01 a RN04 e a cobertura das armadilhas, o script valida e o dono confirma. O `KIT.md` registra o que cada etapa encontrou e o que foi corrigido.
- O script de validação roda só por comando, fora do prek e da CI.
- Os CPFs têm dígitos verificadores válidos, já que a validação do CPF decide se ele vincula o Session Customer ([architecture.md](../../architecture.md) §2), e usam a faixa `000.000.00x`, improvável de coincidir com CPF real. A falsidade aparece também nos nomes, nos e-mails e no registro do `KIT.md`.

## O que entra

- As políticas em `kit/policies/`, os pedidos em `kit/orders.json` e o catálogo em `kit/catalog.csv`.
- O adaptador `MockOrderApi`, atrás do port `OrderApi` do módulo `orders`, conforme desenhado na [architecture.md](../../architecture.md) §4.
- O script de validação, o `kit/PROMPTS.md` e o `kit/KIT.md`.
- 30 casos em `evals/cases/`.

Lembrando que algumas decisões já estão tomadas e valem aqui também: o id da fonte é o nome do arquivo e o título é o primeiro H1, o SKU tem prefixo fixo, as tools devolvem fatos e não veredito ([ADR 0002](../../adr/0002-business-rules-in-policies-facts-from-tools.md)), e o acesso a um pedido exige o CPF e o número juntos, com a API recusando pedido de outro cliente ([ADR 0001](../../adr/0001-order-access-by-cpf-and-order-number.md)). Nada nesta unidade chama o modelo real. Código e chaves ficam em inglês, já o texto voltado ao cliente (políticas, descrições e falas dos casos) fica em pt-BR.

## O que fica de fora

- `OrderService`, `Catalog`, `PolicyIndex`, as tools, o guard e o harness de eval no Inspect. Esta unidade escreve os dados e o `MockOrderApi`, nada que os consuma.
- Rodar a suíte de eval, já que os casos são só dados até o harness existir.
- Os cerca de 5 casos reservados, que saem depois num fluxo isolado com `PET_HELPER_HELD_OUT=1` e levam a suíte a cerca de 35 casos, acima do teto de 30 do desafio. O dono enxuga depois.
- Validação automática do kit em hook ou CI.

## Critério de aceite

Políticas

1. `kit/policies/` tem exatamente 8 arquivos `.md`, um por tema obrigatório: troca e devolução, reembolso, frete, cancelamento, assinatura, retirada em loja, privacidade e dados pessoais, canais e horários. Cada arquivo tem de 150 a 400 palavras e começa por um H1.
2. RN01 a RN04 aparecem exatamente como escritas na requisitos.md, sem contradição entre documentos, e a RN04 remete ao fluxo de devolução da RN01.
3. As regras inventadas são coerentes entre si e com RN01 a RN04, e todas estão listadas no `KIT.md`.
4. Nenhuma política cobre garantia estendida nem programa de fidelidade.

Pedidos

5. `kit/orders.json` tem de 8 a 10 pedidos de pelo menos 3 clientes e cobre os status Pago, Em separação, Enviado, Entregue e Cancelado.
6. Os pedidos incluem regiões do Sul e do Sudeste e pelo menos uma outra. Em relação a 2026-11-03, pelo menos 1 pedido foi entregue há menos de 7 dias e 1 entre 8 e 30 dias.
7. Cada pedido traz os fatos do ADR 0002: status, data de entrega quando houver, região/UF, total, meio de pagamento (Pix e cartão aparecem) e itens com SKU, categoria e embalagem aberta.
8. Os pedidos armam os casos calculáveis de RN01 a RN04: entregas nas duas janelas, ração ou areia com embalagem aberta, totais dos dois lados de R$ 149 e de R$ 249 nas regiões certas e cancelamento antes e depois de Enviado.

API mock

9. `MockOrderApi` implementa `OrderApi.get(order_number, customer_cpf) -> ApiResponse(status, body)` sobre `kit/orders.json` e devolve 200 ao dono, 404 para pedido inexistente e 403 para pedido de outro cliente.
10. A falha é simulada por um número de pedido fixo, um para 503 e outro para timeout, e por `PET_HELPER_ORDER_API_FAILURE`.
11. Testes sem modelo cobrem cada status, mantendo o piso de 80% de cobertura de branch em `src/`.

Catálogo

12. `kit/catalog.csv` tem de 40 a 60 SKUs, com as colunas SKU, nome, categoria, espécie, preço, estoque e descrição, e todo SKU tem um prefixo fixo.
13. O catálogo tem cães e gatos, ração e areia, itens com estoque zero e pares de nomes parecidos, todos de marcas fictícias.

Validação e documentação

14. Um script de cerca de 30 linhas confere contagens, colunas e coerência (todo SKU de pedido existe no catálogo, as políticas respeitam a faixa de palavras). Ele sai com código diferente de zero quando algo falha e passa no kit entregue.
15. `kit/PROMPTS.md` guarda, na íntegra, os prompts usados para gerar cada parte do kit.
16. `kit/KIT.md` registra a data de referência, a loja, as regras inventadas e os temas fora de propósito. Também traz o mapa armadilha → pedido/SKU/política → caso de eval, os achados do critic, do script e do dono e o que foi corrigido.
17. Nenhum dado é real: CPFs, nomes, e-mails e marcas são fictícios, e o texto das políticas é original.

Casos de eval

18. `evals/cases/` tem de 20 a 25 arquivos YAML, um por caso, com `suite`, a data fixada e, nos casos de política, `expected_doc`. O formato mapeia para uma `Sample` do Inspect.
19. A suíte cobre:
    - pelo menos 4 casos de RN01 a RN04 com resposta calculável e pelo menos 3 adversariais (prompt injection, como o cupom de 100%, e dado de outro cliente);
    - "não sei" nos 2 temas fora, falha da API e pedido sem número;
    - contexto em vários turnos ("e o outro pedido?", "na verdade é para gato");
    - recomendação sem SKU inventado, passando por estoque zero e nomes parecidos;
    - escalonamento por reclamação grave, por pedido explícito e por saúde animal sem diagnóstico, e citação da fonte.
20. Todo pedido, CPF, SKU e documento citado num caso existe no kit, e um teste sem modelo confere isso.
21. Todo comportamento do assistente que o kit arma tem pelo menos um caso.

## Ferramentas existentes

A pesquisa foi ampla, já que nenhuma biblioteca da architecture.md cobre geração ou validação de dados. Foi feita em 29/10/2026 sobre o `uv.lock` e a documentação atual.

| Peça | Considerada | Decisão |
|---|---|---|
| Dados falsos pt-BR (CPF, nome, e-mail) | Faker 40.44 (`pt_BR`), Mimesis 22.2, validate-docbr 2.0 | Não entram. O kit é estático e gerado por IA uma vez, e os dígitos verificadores do CPF cabem numa função própria de ~8 linhas, que depois a vinculação do Session Customer reaproveita. |
| Validação de CSV/JSON | pydantic 2.14 (já no lock, transitivo), pandera 0.35, frictionless 5.21, jsonschema 4.26 | Entra o pydantic, que vira dependência direta (o deptry exige), junto com `csv` e `json` da stdlib. O pandera puxa o pandas, o frictionless é pesado e o jsonschema repete o pydantic. |
| API mock | respx 0.23, pytest-httpserver 1.2, FastAPI | Não entram. São mocks de HTTP para teste, e nenhum entrega um adaptador de domínio. A função atrás do port `OrderApi` é o "mesmo contrato" que o requisito aceita. |
| Casos YAML para o Inspect | inspect-ai 0.3.286 (`json_dataset`, `MemoryDataset`, `Sample`) | O Inspect não lê YAML, então os casos passam por `yaml.safe_load` (o pyyaml já está no lock), depois por um modelo pydantic, e viram `Sample` (`id`; `input` com os turnos; `metadata` com suite, today, checks e expected_doc). O solver de vários turnos fica para a unidade de eval. |
| Validação de CPF | presidio-analyzer 2.2.366, python-stdnum 2.2 | O Presidio não tem reconhecedor de documento brasileiro. O dígito verificador vem de uma função própria, e o python-stdnum fica como alternativa se ela crescer. |
