# Arquitetura: entrega dos evals

Status: aprovado em 30/10/2026. Decidida pelo critic da cadeira do dono em 30/10/2026, com 11 lacunas abertas a partir dos desvios do [plano](./plan.md). Base: [spec.md](./spec.md), [architecture.md](../../architecture.md), [cadeira-do-dono.md](../../directives/cadeira-do-dono.md), [loop-melhoria.md](../../directives/loop-melhoria.md) e [fluxo-desenvolvimento.md](../../directives/fluxo-desenvolvimento.md).

O critic rodou em `opus`, numa tab do Herdr, e gravou cada resposta no hexlog (processo `docs-specs-eval-delivery`) como `premise` `simulated` com a `evidence` e o veredito anexado. As 9 lacunas comuns se resolveram pelo caminho curto, com a evidência local; as 2 críticas (Q6 e Q7) passaram por júri de 3 jurados em `sonnet` e ficam abertas até o aval do dono na saída do `/pre-code`.

Decisões que já eram do dono e não passaram pela cadeira (origin `human`, na spec): só o juiz Sonnet é validado agora, cerca de 50 rótulos por critério, o critic anota primeiro, 2 a 3 casos de injeção indireta, turnos fixos com `unable` no desvio, IC de Wilson com repetição de 10 e a unidade só mede, sem Ollama.

## 1. Fronteira entre o Inspect e o nosso código

- Escolha: o Inspect AI fica com o solver multi-turno (que chama o `turn` síncrono via `asyncio.to_thread`), as épocas `Epochs(3, "at_least_3")`, a concorrência e o log `.eval` com `log_dir` explícito. O juiz é uma função pura sobre o SDK da Anthropic, com o cliente injetado. No `--judge sync` o scorer a chama inline, e o `unable` vira `Score.unscored()`; o lote (Message Batches API direto), o `--rejudge`, o `--collect` e a ferramenta de rótulos rodam como passos depois das conversas, sobre as transcrições salvas. O `judge.jsonl` é a fonte única dos vereditos. O US-0 é o ponto de saída: uma asserção "stop if red" vermelha (inclusive a nova, de que o juiz via `to_thread` dentro do scorer não serializa as amostras) volta à cadeira como pergunta crítica.
- Motivo: a §4 da baseline se contradiz (o mapa de módulos diz "judge through an Inspect model role", e as alternativas dizem que o SDK direto fica no juiz por causa do Batch). Com o MP1 e a ordem baseline sem juiz, congelamento e re-julgamento, o juiz precisa ser chamável fora de uma task do Inspect.
- Alternativas: a §5 ao pé da letra (juiz como model role e `--batch` dentro do scorer), rejeitada porque depende de semântica pré-1.0 que a própria §5 diz não confirmada; runner próprio, rejeitado porque reabre uma rejeição do dono de 29/10/2026.
- Consequência: a §4 e a §5 da baseline são emendadas nesta unidade (US-12).
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f29-c607-759e-8784-74717fb32a88`

## 2. Esquema do trace

- Escolha: nomes do OpenTelemetry GenAI como chaves planas (`gen_ai.response.time_to_first_chunk` em segundos, `gen_ai.usage.*`, `gen_ai.response.finish_reasons` como lista, `gen_ai.conversation.id` igual ao `session_id`, `pet_helper.*` para os nossos); ids de correlação (`span_id` = `run_id`, `parent_span_id` = `parent_run_id`, `trace_id` = `session:turn`, sem o formato hex do OTel, que um exportador futuro mapeia); o interruptor `PET_HELPER_TRACE_CONTENT` com o conteúdo **ligado** e mascarado por padrão, no chat e na eval, e `=0` grava só o tamanho; no destino, a ordem é máscara, truncamento e escape.
- Motivo: o módulo `trace` ainda está vazio, então renomear não custa nada. O entregável 6 pede trace que explique a resposta para quem roda o `pet-helper chat`, e a máscara fica num ponto só e testada, por isso o default desligado do plano foi revertido.
- Achado: o input do Sample do Inspect leva os turnos crus do caso, com CPF, e o log é versionado. O input passa pela máscara e a varredura de PII da rodada cobre também o log do Inspect, o `judge.jsonl` e o `cases.csv`.
- Alternativas: manter os nomes da §2; spans OTel via SDK (a §2 já rejeitou); conteúdo desligado no chat.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f2b-f385-787c-9db7-912cb28aa4f1`

## 3. Teto de custo

- Escolha: um `RoundBudget` fail-closed por comando pago (rodada, repetição decisiva, re-julgamento, validação do juiz, fumaça), compartilhado entre assistente e juiz, com reserva em voo trocada pelo custo real quando ele chega. Chamada sem usage mantém a reserva como gasta e, em modo estrito, a rodada para, salva o que tem e marca o resto como `not_run`; preço desconhecido impede o comando de começar; `retry_on_error` em 0; o lote reserva o pior caso na submissão.
- Motivo: o `cost_limit` do Inspect é por amostra e não vê o assistente; com 8 conversas em paralelo, checar só o gasto deixa 8 chamadas passarem juntas.
- Mudança: cada reserva cobre o pior caso de uma chamada (a do assistente, com `max_tokens` de 8192, passa de 0,005), conferida por teste contra o `genai-prices`, e o juiz ganha `max_tokens` explícito.
- Alternativas: checar só entre amostras; o `cost_limit` do Inspect.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f2d-ab72-725e-a4a2-2f5c36fbca2c`

## 4. Rodadas da baseline

- Escolha: duas rodadas no mesmo SHA, a visível e depois a reservada, cada uma com estimativa, confirmação e teto próprios, as duas com `--judge none`. Depois do congelamento do juiz no split dev, o dono roda `--rejudge` nas duas, e a anotação só começa depois disso.
- Motivo: o congelamento depende do conjunto de rótulos, que depende da baseline, então a ordem sai das ACs e do MP1.
- Mudanças: cada veredito leva o hash do prompt do juiz, e o `round.json` só grava o hash quando todos os vereditos vigentes o compartilham (senão `judge_pending`); relatório, categorias, anotação e o passo do README recusam uma rodada `judge_pending`; o `round.json` grava `git_dirty`, a rodada reservada imprime o SHA e o README avisa se os dois SHAs diferem.
- Alternativas: juiz inline e nova rodada depois; uma rodada combinada.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f2f-649f-75e8-8b96-c9c3019a275c`

## 5. Isolamento dos casos reservados

- Escolha: o hook passa a casar o caminho reservado por forma de caminho, com `.gitignore` ancorado, `.rgignore` e `log_dir` explícito; os resultados reservados ficam fora do git e fora da análise por agente, e a referência guardada é o k/3 por caso mais o SHA.
- Achado: o hook só cobria as tools nativas. Toda tool MCP que lê arquivo ou roda shell (context-mode, `python_repl`, `ast_grep`, `lsp_*`, os índices de código) passava sem checagem.
- Mudanças: o hook cobre as tools MCP de leitura e execução e varre todas as strings do `tool_input`; `grep -r` e `find` sobre um ancestral sem excluir a pasta são barrados; a pasta sai dos índices do gitnexus e do graphify antes de gerar os casos. Resíduo declarado: `cd` em `evals` seguido de listagem.
- Alternativas: manter o match literal; versionar os resultados sob o hook (o `git log -p` vazaria); barreira no sistema de arquivos (bloquearia o `--held-out` do próprio dono).
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f31-878c-7034-8dc8-99378db5704a`

## 6. Regras do loop de melhoria (crítica)

- Escolha recomendada: emendar a `loop-melhoria.md` com o texto exato do anexo do veredito (linhas 7, 31-32, 35, 42, 51, 52 e 58). Em resumo: caso decisivo roda 10 vezes antes do veredito (regressão rejeitada com 2 ou mais falhas em 10, alvo aceito com 9 ou mais de 10), `unable` ou `error` de caso decisivo é refeito até haver 10 execuções válidas, parada pelo teto deixa o caso sem veredito; o relatório mostra o IC de Wilson e a diferença pareada, que informam e não decidem; a graduação para regressão passa a ser obrigatória; o teto vale por comando pago; o juiz é validado com cerca de 50 itens por critério, dev e teste por caso, com o piso de TPR e TNR escrito pelo dono antes de ler o teste; a validação vale para o par modelo e prompt; os reservados rodam uma vez na baseline sem ser consumidos.
- Motivo: aplica o MP1, o MP5 e a rodada 7 da entrevista. O júri achou cinco contradições no rascunho do plano.
- O dono precisa ver: os limiares deixam passar regressão pequena de propósito (uma queda real de 1,0 para 0,9 é pega em cerca de 7% das vezes); o piso antes do teste é novo; "parar com menos rótulos" vem do plano, não do MP1; a graduação vira obrigatória.
- Alternativas: o rascunho do plano como estava; emenda mínima adiando a estatística (contraria o MP5).
- Júri: unânime, confiança média (os três jurados no mesmo modelo).
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f39-17c4-7e45-aa94-eca32ddcf79f` (lacuna aberta até o aval)

## 7. Pausa da M2 para os passos do dono (crítica)

- Escolha recomendada: a pausa vira regra geral da `fluxo-desenvolvimento.md`. Quando o `plan.md` da unidade tem a seção `## Passos do dono`, a M2 para depois do PR em draft e antes da etapa 7, registra `impasse` com o motivo `owner steps pending` e mostra os comandos; `/deliver <slug>` retoma na etapa 7. Os casos reservados chegam à `main` por um PR próprio, que o dono mergeia depois de aprovar cada caso, e entram na branch por merge da `main`, para o diff que o painel lê não os mostrar. Antes do veredito, a M3 confere se `src/` mudou desde o SHA medido e, se mudou, escreve no README e no PR a linha de baseline velha; rodar de novo é decisão do dono.
- Motivo: todo ciclo do loop de melhoria tem rodada paga manual, então a exceção pontual se repetiria a cada volta; a guarda R2 impede outra sessão de escrever depois do hand-off.
- O dono precisa ver: um PR a mais para mergear antes da baseline reservada; a linha de baseline velha; a pausa aparece como `impasse`.
- Alternativas: exceção só nesta unidade; passos na sessão do painel (exceção à R2, dois painéis, sessão viva por dias); chave de estado nova; rerun forçado antes do veredito (jogaria fora os rótulos).
- Júri: 3 votos ao fim (2 a 1 na rodada às cegas), confiança média.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f40-4532-7193-a86f-900638834ef9` (lacuna aberta até o aval)

## 8. Semântica do `unable`

- Escolha: cinco resultados separados (pass, fail, error, unable, not_run), só pass conta no k, e o denominador do k_of_3 são as execuções feitas; a coluna `unable_reasons` mostra o motivo (`off_script:<dado>` ou `judge:<critério>`); `error` também é refeito na repetição decisiva. A anotação lê toda execução fora do roteiro, porque um pedido indevido do assistente (como CPF numa pergunta de catálogo) sai como `unable` e não como `fail`; esse pedido vira caso de eval novo.
- Alternativas: tirar o `unable` do k (2 pass e 1 unable virariam 2/2); contar como fail; juntar error com unable.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f42-08d2-7219-9c9d-4e9bc4dbbf58`

## 9. Costuras do assistente

- Escolha: o `build_assistant` ganha `tracer`, `kit_dir` e `today` (relógio injetado), o `Assistant.turn` não muda, a eval o chama via `to_thread` num executor explícito do tamanho da concorrência, o sink do trace e a inicialização do Presidio ganham trava única, e os adapters passam a streaming depois da caracterização.
- Motivo: a corrida real está nas variáveis de ambiente, que 8 threads não conseguem setar cada uma; um Assistant por amostra mantém o ADR 0001; o streaming já é decisão da §2.
- Mudanças: o handler do LangChain captura as próprias exceções e só deixa passar o `BudgetExceeded`, para um bug no trace não derrubar o chat; a caracterização do streaming confere a assinatura dos blocos de thinking, os breakpoints de cache e o usage no chunk final; um teste paralelo confere data e kit próprios por amostra.
- Alternativas: Assistant assíncrono; manter `invoke` com TTFT nulo.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f43-c974-73bb-b51b-ac4941ab16de`

## 10. Esquema dos casos

- Escolha: entram `turn`, `args`, o check `state`, `fixture`, `reference` opcional nos 5 adversariais e `expected_doc` como lista, com o esquema no `KIT.md`; ficam adiados o `reference` dos outros 20 casos e o script que transforma transcrição em teste com `FakeGateway`.
- Mudanças: `state` só aceita os campos públicos do `snapshot()` listados no `KIT.md`; `turn` em check que vale para a conversa inteira é rejeitado; as chaves de `args` são conferidas contra o esquema da tool (um `cpf` em `get_order` nunca casaria, pelo ADR 0001).
- Alternativas: manter o esquema da §5; `extra="forbid"`; construir agora o que foi adiado.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f45-9d12-79c9-b241-5c0b62491445`

## 11. Conjunto de rótulos do juiz

- Escolha: o conjunto sai de cada critério aplicado às transcrições da baseline em que vale, mais falhas plantadas por modelos de texto determinísticos, com dev e teste divididos por caso e o `judge-pool` no terminal do dono.
- Mudanças: o critério do "não sei" ganha a classe "dentro do escopo" (toda resposta de RN e de política que cita a fonte), para não ficar apoiado em 4 ou 5 casos; cada número mostra também quantos casos distintos estão por trás; exemplo usado no prompt sai das métricas de dev; sem falha natural, o TNR sai como "só plantadas", e o dono rotula as plantadas às cegas também.
- Motivo: 25 casos × 3 execuções dão 75 transcrições, porém as 3 execuções de um caso são quase cópias, então o n efetivo fica perto do número de casos.
- Alternativas: falhas plantadas geradas por modelo; rótulos só dos 6 casos com `judge`.
- origin: simulated-approved · premissa `docs-specs-eval-delivery:01a18f47-652c-73ff-8002-37815c595cf1`

Nenhuma decisão virou ADR: as que mudam regra (6 e 7) mudam as diretivas, e as outras emendam a baseline em `docs/architecture.md`.
