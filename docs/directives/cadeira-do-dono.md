# Cadeira do dono

Quem decide o quê neste projeto, quem responde no lugar do dono e como a resposta fica registrada. Vale para a sessão principal e para todo subagente. A mecânica de lançar o critic está em `.claude/commands/owner-seat.md`; os tipos do registro, em `.claude/skills/pet-helper-hexlog/hexlog-model.json`. O desenho vem da cadeira do dono do hexlog, sem a contenção de ferramentas do critic e só com o transporte do Herdr.

## Camadas de decisão

- Produto e estratégia são do dono: escopo, comportamento do assistente, regras de negócio, prioridades e o que fica de fora.
- Arquitetura é do critic da cadeira: camadas MSC, modelo e runtime, stack de observabilidade, estratégia de eval.
- O dono dá sugestões leves de arquitetura; o critic as pondera como insumo, não como ordem.
- Exceção a produto: achado de revisão que vira decisão de produto dentro da M2 ou da M3 vai ao critic da cadeira, que decide com base em [estrategia.md](estrategia.md) e nos requisitos; a decisão é aplicada, registrada no PR, e o dono a revê no merge.
- Código é do executor e do planner: arquivos dentro das camadas, bibliotecas auxiliares, nomes, detalhes de teste. A decisão de código não passa pela cadeira.

## Quem responde e não devolve

- O ocupante da cadeira (o `critic` durável) responde a pergunta que recebe e registra o que respondeu; nunca a devolve a quem o lançou.
- As ações do catálogo que o ocupante dispara cumprem a tarefa recebida e devolvem o resultado; não tratam a pergunta como lacuna nova.
- Quem encontra lacuna ou ponto crítico que ninguém lhe entregou a devolve à sessão principal (pergunta, contexto e regra tocada); só a sessão principal alcança o critic e o turno do dono. O que lhe foi entregue ele resolve e registra. Quem devolve não escolheu, então não delega registro.

## Exceção de modelo

- O critic da cadeira roda em `opus` (o tier é o contrato, não a versão). É exceção autorizada pelo dono à regra global de subagentes em `sonnet` ou `haiku` e vale só neste repositório.
- É um só por unidade de trabalho, vive até o fim dela e é continuado por `SendMessage`. Os subagentes e jurados que ele lança nascem e morrem por pergunta e seguem em `sonnet` ou `haiku`.
- O `agy` só entra por pedido explícito (`--critic agy` no `/owner-seat`) e roda fixado em `gemini-4-argon-high`; o nome pode mudar entre versões do `agy`. Esse pedido é o único gate.

## Ocupante

- O ocupante é o `critic` durável numa tab do Herdr (`HERDR_ENV=1`), lançado na primeira pergunta para a unidade sem lacuna não pagar `opus`.
- O critic só ocupa a cadeira quando o `premise` do processo da unidade tem `answeredBy` (`describe_type` com `process`; `TYPE_NOT_PINNED` conta como sem o campo). Unidade aberta antes do campo não tem ocupante.
- Sem Herdr, sem `answeredBy` fixado ou com o lançamento falhando duas vezes, não há ocupante: a pergunta de arquitetura vai ao dono, uma por turno.
- Se o critic cai no meio de uma resposta, procure a `premise` `simulated` vigente da lacuna antes de gravar outra.
- O `agy` não tem subagentes OMC nem júri: degrada pelo catálogo (`local` e `web` próprios) e o aval vê a marca de degradação.

## Classe da pergunta

- É crítica a escolha que altera, remove ou contraria o sentido de uma regra de `docs/directives/`, de uma premissa de [estrategia.md](estrategia.md) ou de um ADR de `docs/adr/`. O resto é comum. Regra nova que não altera nenhuma existente é comum.
- Toda escolha crítica vai à cadeira e exige o aval do dono antes de valer. Para `estrategia.md`, "altera ou remove" equivale a "contradiz": só o dono a emenda, e o critic só recomenda.
- Lacuna que compromete a unidade inteira vence a classe crítica: fica fora da cadeira e vai ao dono.
- A sessão principal abre o `context` da `gap` com o literal `Class: critical; rule: <doc ou ADR e a regra>` ou `Class: common`, e acrescenta `Nature:` do catálogo. O critic confere e só escala: grava a `premise` sem `fills-gap` e abre o `attach` com "Class: critical (escalated from common)".
- O `rule:` do literal é uma regra só: lacuna que toca regras de dois docs vira uma `gap` por doc.

## Catálogo de ações

Ação é o modo de o critic juntar contexto antes de responder. Ação em `Unavailable:` da pergunta nunca é permitida, e a célula `—` da tabela por transporte marca ação sem canal naquele transporte.

| Ação | Entrada | Saída | Quando é permitida |
|---|---|---|---|
| `local` | A pergunta | Fatos do repositório (código, docs, ADRs), com o caminho | Sempre. |
| `web` | A pergunta de fato externo | Fatos de fonte externa, com a fonte | Fato externo, ou fallback de `jury` indisponível. |
| `jury` | A pergunta mais a evidência local já coletada | Veredito do painel, com confiança e dissidência | Comum: só quando a evidência local (hexlog, docs, ADRs) não decide. Crítica: exigida. Só no Claude, com a skill `the-jury`. |

| Ação | claude-herdr | agy |
|---|---|---|
| `local` | explore | próprio |
| `web` | document-specialist | próprio |
| `jury` | the-jury | — |

- Modelos: `explore` em `haiku`; `document-specialist` e jurados em `sonnet`. A `web` do `agy` é a tool nativa `search_web`.

| Natureza | comum | crítica |
|---|---|---|
| `repo-fact` | local | jury |
| `external-fact` | web | jury |
| `choice` | jury | jury |

- Ordem de custo, da mais cara à mais barata: `jury`, `web`, `local`.
- Caminho curto na comum: se a evidência local (hexlog, docs, ADRs) já decide, o critic responde direto com ela, sem ação e sem `actionUsed`. Consultar o hexlog ou os docs diretamente não é a ação `local`.
- Degradação, nas duas classes: ação indisponível cai para a próxima da ordem de custo que sirva à pergunta. A exigência de `jury` na crítica cede quando `jury` está indisponível (o `agy`, por exemplo). Ação que falha ao executar ou volta sem fonte conta como indisponível.
- Piso: sem ação que sirva, ou nada achado, a resposta é `sem resposta`, com o motivo no `attach` e nenhuma premissa `simulated`; a pergunta vai ao dono.
- `actionUsed` registra a ação de maior custo que contribuiu, só quando o `premise` fixado tem o campo. Sem `actionUsed` fixado a ação indisponível não degrada: a resposta é `sem resposta`.
- `Unavailable: <ações>` só vale como linha de cabeçalho da mensagem da pergunta, escrita pela sessão principal antes e fora do bloco de contexto; nunca em registro do hexlog nem em `gap.context`.
- O `attach` abre com `Action: <usada>`. No aval a sessão principal mostra "degradada: esperada X, usada Y", com o esperado calculado por ela pela tabela e pela coluna do transporte, nunca lido do critic. A marca só vale com `actionUsed` fixado e quando a usada é mais barata que a esperada.

## O que o critic registra

- Na hora, com os ids do hexlog: `attach` do veredito com o cabeçalho da classe efetiva e, num só `register`, a `premise` `simulated` e a `evidence` que a apoia. São registros novos, nunca `supersedes`; o `statement` tem até 255 caracteres: resuma, não trunque.
- `fills-gap` só na classe comum. Na crítica a `simulated` fica sem ele e a lacuna aberta até o aval.
- `actionUsed` entra no `register` quando o `premise` fixado tem o campo. O campo é autodeclarado: rastro, não prova.
- O critic registra com `author.agent` `owner-seat-critic-claude` ou `owner-seat-critic-agy` e o `model` real.
- O escopo do critic (processo da unidade, só `premise` e `evidence` novas, sem `supersedes` nem `revokes`) é instrução, não barreira: ele sobe sem lista de ferramentas restrita.

## Aval do dono

- Na M1 o dono avaliza as decisões de arquitetura no passo de saída do `/pre-code`, uma lacuna por turno. Origem de cada decisão: `human` quando o dono responde, `simulated` pelo critic e `simulated-approved` pela sessão principal no aval.
- Avalizou: grava `simulated-approved` com `fills-gap` e `derivesFrom` para a `simulated`. Decidiu outra coisa: grava `human` com `revokes` da `simulated`. Recusou: revoga a `simulated`, refaz o dependente e devolve a recusa ao critic como dado. Sem resposta: nada se revoga e a unidade não segue.
- Na classe crítica o texto da regra tocada é emendado no mesmo trabalho, e a lacuna segue aberta até o aval: o gate `gaps` falha enquanto isso.
- Aprovar não vale para contradição com [estrategia.md](estrategia.md).
- A reinjeção "continue" do `ralph` ou do `autopilot` não é aval. Sem terminal, o pedido de aval vai na mensagem final.
- A trava vale quando a lacuna fica sem resposta (critic caído sem substituto, limites esgotados ou contradição com estratégia): o que depende dela não executa, o independente segue e a pergunta vai ao dono.

## Limites

- Sem teto de perguntas por unidade. Mais de 12 respostas no `critic.md` só avisam o dono.
- Duas substituições do critic por unidade; depois a pergunta vai ao dono. Só queda e queda apontada pelo dono contam; `context: tight` e sinal objetivo substituem sem contar.
- `jury`: um painel por rodada, de 3 a 5 jurados, até 3 rodadas. Passou disso, `sem resposta`.
- Recusa do dono à mesma lacuna: até 2 reaberturas ao critic vivo, depois vai ao dono.
