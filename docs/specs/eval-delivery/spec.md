# Entrega dos evals

Status: aprovado em 30/10/2026. A entrevista foi em 30/10/2026 e terminou com ambiguidade de 11%. A versão completa, com transcrição e pontuação, fica em `.omc/specs/deep-interview-eval-delivery.md`.

## Objetivo

Fecha os entregáveis 3, 4, 5 e 6 da [requisitos.md](../../requisitos.md): a suíte de avaliação automatizada (até 30 casos, checks determinísticos e juiz LLM), os resultados salvos com análise de falhas, o README de decisões inteiro e o trace JSONL que explica cada resposta. O desenho já está na [architecture.md](../../architecture.md), no §2 (trace) e no §5 (harness de eval), dessa forma esta unidade constrói o que está lá, com os desvios listados abaixo.
A unidade só fecha depois que o dono roda a rodada baseline no branch e os resultados, a validação do juiz e a análise entram no mesmo PR, antes do merge.

## Decisões da entrevista

- São 5 componentes: runner, checks e juiz, casos reservados (com a consolidação da suíte), trace e a rodada baseline com análise e README. O dono trouxe o trace para esta unidade em vez de deixá-lo numa unidade própria.
- A unidade termina com a rodada no PR. O `/deliver` constrói o harness testado sem modelo e, depois disso, o dono gera os reservados numa sessão isolada, roda a baseline, rotula o juiz às cegas e confirma as categorias de falha, sendo que os resultados e a análise entram no mesmo PR.
- A suíte tem no máximo 30 casos no total, reservados incluídos, como pede o entregável 3 ("20 a 30 casos"). Os 31 visíveis de hoje viram uns 25, mais 5 reservados, e o `CASE_BUDGET` passa a contar as duas pastas.
- A lista de fusões e cortes sai do plano e respeita três travas que os testes do kit já conferem: ao menos um caso por RN01 a RN04 com valor calculável, ao menos 3 adversariais e o `BEHAVIOR_MINIMUMS` intacto. O dono aprova a lista no portão de saída do `/pre-code`.
- A unidade só mede. Nenhuma correção no assistente: cada falha da baseline vira uma volta do loop de melhoria, uma unidade por alvo.
- O Ollama fica fora, ou seja, sem fumaça no `llama3.1:8b` e sem juiz Llama. Só o juiz Sonnet é validado agora, com 10 a 15 rótulos cegos do dono, porém isso desvia do §5, que pede TPR e TNR dos dois juízes, e a cadeira do dono registra o desvio.
- Os 5 reservados rodam uma vez na baseline (3 vezes cada), com saída só de id e k/3. Como nada foi corrigido olhando para eles, não contam como consumidos, e esse resultado é a referência da primeira aceitação.
- O README de decisões sai inteiro nesta unidade (entregável 5), não só a parte de eval.

## Decisões do portão de saída

No fim do `/pre-code`, em 30/10/2026, o dono decidiu fundir em vez de apagar, preferindo sempre conversas longas para estressar o assistente, e fundir o máximo possível sem repor as vagas. Dessa forma os 31 casos legados viram 18, que com os 2 de injeção indireta dão 20 visíveis (o piso `MIN_CASES`), e o total com os 5 reservados fica em 25, dentro do intervalo de 20 a 30. Nos reservados, RN02 e RN04 viram uma conversa só do mesmo cliente (cancelar e depois perguntar pelo reembolso). A ordem de corte do plano para o prazo foi aceita como escrita, e as duas lacunas críticas da arquitetura (emenda da `loop-melhoria.md` e a pausa da M2 como regra geral da `fluxo-desenvolvimento.md`) foram aprovadas.

## Decisões das práticas de mercado

Depois da entrevista, o dono pediu uma pesquisa dedicada às práticas já maduras no mercado antes do plano. Foram quatro estudos (desenho da suíte, juiz LLM, teste de agente e tracing), guardados em `.omc/research/eval-delivery/`, e cada ponto que mexia em produto virou uma decisão do dono:

- Rótulos do juiz: cerca de 50 por critério, uns 150 no total, separados em dev (ajuste do prompt e fonte dos exemplos) e test (lido uma vez com o prompt congelado), estratificados para que cada critério tenha as duas classes, semeando falhas quando a baseline tiver poucas. TPR e TNR saem por critério, com IC de Wilson e a contagem bruta. Isso substitui os "10 a 15 rótulos" do critério 12. Como os 6 casos com `judge` dão amostras de menos, o conjunto de rótulos sai de rodar cada critério sobre as transcrições da baseline em que ele se aplica, mais as falhas semeadas, dentro do teto de US$ 5, e a cadeira do dono decide como.
- A anotação da análise de erro fica como está no `loop-melhoria.md`: o critic anota primeiro e o dono confirma depois de ler umas 5 transcrições. O README declara o risco de ancoragem, já que o Hamel recomenda que o especialista anote uns 30 traces antes.
- Injeção indireta: entram de 2 a 3 casos com o payload dentro de dado que o modelo lê (descrição de SKU, texto de política, nota de pedido), num fixture de kit só de teste, sem mexer no `kit/` entregue. A nota é determinística, ou seja, o ataque não pega e a tarefa legítima ainda sai, e um teste sem modelo confere que o texto bruto da tool fica isolado. O total continua em no máximo 30, então mais 2 ou 3 visíveis são consolidados.
- Os turnos continuam fixos, porém com detecção de desvio: quando a conversa sai do roteiro, a execução vira `unable`, não `fail`, e aparece no relatório.
- Estatística da aceitação: o relatório mostra o IC de Wilson da suíte e a diferença pareada por caso entre rodadas, e o caso que decide aceitar ou rejeitar (regressão ou alvo) roda de novo 10 vezes antes do veredito, dentro do teto de US$ 5. Nesta unidade isso aparece só no relatório da baseline, e a regra muda no `loop-melhoria.md`.

Os itens técnicos da pesquisa vão para o plano e a cadeira do dono, sem pergunta ao dono: nomes de campo do OTel GenAI no JSONL, ids de correlação por turno, TTFT medido até o primeiro chunk, interruptor de conteúdo no trace, truncamento dos campos livres no destino, checks que olham os argumentos da tool e o estado, uma transcrição de referência por caso testada sem modelo, um script que transforma falha real em teste sem modelo, taxa de aprovação por check, regra de graduação para `regression` e taxa de `unable` por critério.

## O que entra

- `pet-helper eval`, ao lado do `chat`, sobre o Inspect AI: estimativa e confirmação, 3 execuções com aprovação só em 3 de 3, teto de US$ 5 por rodada, resultados em `evals/results/<round_id>/`, reservados com `--held-out`, fumaça com `--runs 1`, juiz `sync` ou `batch` e `--collect`.
- Uma função pura por tipo de check em uso, mais o recall do `expected_doc`. O scorer do juiz com `pass`, `fail` e `unable`.
- A consolidação dos casos visíveis e os 5 reservados.
- O trace do §2: eventos em JSONL, máscara no destino, callback handler do LangChain, custo pelo `genai-prices` e `pet-helper metrics`.
- A rodada baseline, o `cases.csv` anotado, a contagem por categoria e o README completo.

## O que fica de fora

- Corrigir o assistente, incluindo os itens que a unidade `assistant` adiou: backstop determinístico de escalonamento, retry em 503 e recusa do segundo CPF.
- Ollama: fumaça, juiz Llama e TPR/TNR do Llama. Vêm numa unidade futura do Ollama.
- Mover casos para a suíte `regression` além do que o dono fizer à mão depois da baseline.

## Critério de aceite

Runner

1. `pet-helper eval [--suite ...] [--case ...] [--judge sync|batch] [--held-out] [--runs N] [--collect <rodada>]` existe como subcomando ao lado do `chat`.
2. Antes de qualquer chamada de modelo, mostra a estimativa de tokens e custo (média da última rodada salva ou um valor padrão por turno) e só segue com confirmação explícita.
3. Cada caso roda 3 vezes e só passa com 3 de 3. O k/3 sai por caso. Um erro de infraestrutura conta como `error`, nunca como `pass`, e aparece no resultado.
4. O custo real (assistente e juiz) é somado chamada a chamada. Quando o total passa de US$ 5, a rodada para e salva o que já tem.
5. Os resultados vão para `evals/results/<round_id>/`: `round.json` (SHA do git, modelos, hashes dos prompts do assistente e do juiz, relógio, custo total), o log do Inspect, `cases.csv` (`case_id`, `suite`, `k_of_3`, `first_failure_note`, `category`, `human_label`, `cost_usd`) e `traces/`.
6. `--held-out` só roda com `PET_HELPER_HELD_OUT=1`, grava em `evals/held_out/results/<round_id>/` e mostra só id e k/3.
7. `--runs 1` é fumaça: a saída leva `"smoke": true` e nada vai para `evals/results/`.
8. O juiz em lote é testado na prática primeiro. Se o lote do Inspect não funcionar a partir do scorer, o juiz fica só síncrono e isso fica registrado. `--collect` termina uma rodada cujo lote não voltou no prazo.
9. Toda a lógica do runner tem cobertura no pytest sem modelo real (`FakeGateway` no assistente, `mockllm` do Inspect no juiz), dentro do piso de 80% de branches.

Checks e juiz

10. Há uma função pura por tipo de check em uso (`cites_source`, `skus_exist`, `mentions_order`, `no_other_customer_data`, `tool_called`, `tool_not_called`, `escalated`, `must_include`, `must_not_include`, `expected_value`), mais o recall do `expected_doc`, cada uma testada no pytest com caso que passa e caso que falha.
11. O scorer do juiz avalia um critério por chamada, com a crítica antes do veredito, que é `pass`, `fail` ou `unable`. `unable` vira `Score.unscored()`.
12. Depois da baseline, o dono rotula de 10 a 15 amostras do juiz às cegas. TPR e TNR do juiz Sonnet vão para o README junto com as limitações declaradas do juiz.
13. Depois da validação, o prompt do juiz fica congelado, e o hash dele no `round.json` confere.

Casos reservados e suíte

14. O plano propõe a lista de consolidação dos visíveis (de 31 para uns 25) sob as três travas, e o dono a aprova no portão de saída.
15. Os 5 reservados ficam em `evals/held_out/cases/`. São gerados numa sessão isolada, aberta com `PET_HELPER_HELD_OUT=1`, que lê só `docs/requisitos.md`, `GLOSSARY.md` e `kit/`, e o dono aprova cada um.
16. O total fica em no máximo 30 casos, e `uv run pytest -q` (testes do kit incluídos) passa.
17. Os reservados rodam uma vez na baseline, 3 vezes cada, sem contar como consumidos.

Trace

18. Os eventos do §2 (`turn.start`, `guard.input`, `retrieval`, `model.call`, `tool.call`, `guard.output` etc.) são gravados em JSONL no diretório da execução, com máscara no destino (o `Guard.mask` e o Presidio que já existem). As chamadas de modelo e de tool são capturadas por um callback handler do LangChain.
19. O custo de cada chamada sai do `genai-prices`, e o `model.call` leva `ttft_ms`, `duration_ms`, tokens de entrada e saída, `cache_read`, `cache_write`, `stop_reason` e `fallback`.
20. `pet-helper metrics <pasta>` lê os JSONL sem chamar modelo e imprime a tabela do §2 (TTFT p50/p95, custo e latência por conversa etc.).

Baseline, análise e README

21. O dono roda a baseline no branch: todos os visíveis 3 vezes e os reservados (critério 17), depois da estimativa e abaixo de US$ 5.
22. O critic preenche `first_failure_note` e `category` (recuperação ou geração, e os códigos axiais) no `cases.csv`. O dono confirma as categorias depois de ler umas 5 transcrições, e o agente grava em `human_label` o que o dono confirmar. O CSV nunca é editado à mão.
23. Um script sobre o `cases.csv` imprime a contagem por categoria.
24. `evals/results/<baseline>/` entra versionado no PR.
25. O README sai completo: arquitetura, escolha de modelo, trade-offs, custo e latência medidos por conversa (pelo `pet-helper metrics` sobre os traces da baseline), a seção de eval (como rodar, k/3 da baseline, k/3 dos reservados, análise de falhas por categoria, TPR/TNR e limitações do juiz) e o que faria com mais tempo.
26. O PR só é mergeado depois dos critérios 21 a 25, e só o dono mergeia.

## Comportamento e caso de eval

Esta unidade não muda o comportamento do assistente. O único comportamento da seção "Assistente" da requisitos que ganha prova aqui é "mascara dados pessoais nos logs", que passa a existir de fato com o trace:

| Comportamento | Caso de eval |
| --- | --- |
| Mascara dados pessoais nos logs | Check determinístico no nível da rodada: nenhum CPF, e-mail ou telefone cru aparece nos traces da baseline. Também coberto pelo pytest. |

Os demais comportamentos já têm casos em `evals/cases/` e passam a ser medidos de verdade, 3 vezes cada, na baseline.

## Ferramentas existentes

A pesquisa foi direcionada _(targeted)_, isso porque eval, preço e trace já têm bibliotecas escolhidas na architecture.md (Inspect AI no §5, `genai-prices`, callback do LangChain e Presidio no §2). O levantamento amplo já tinha sido feito em 28 e 29/10/2026, com promptfoo, Phoenix, LangSmith, Langfuse, Argilla e Label Studio rejeitados, então desta vez a pesquisa conferiu o que o Inspect AI 0.3.286 (de 28/10/2026, Python 3.11 ou mais) entrega, pela documentação em inspect.aisi.org.uk.

- O Inspect já entrega, e por isso não construímos:
  - épocas com `Epochs(3, "at_least_3")`, que exige que todas passem (o `pass_k_3` é um estimador combinatório, não o 3 de 3 estrito);
  - erro por amostra (`fail_on_error=False`, `retry_on_error`);
  - vários scores nomeados por amostra;
  - `Score.unscored(reason=...)` para o `unable`, que sai das métricas e guarda o motivo, ao contrário do `NOANSWER`, que vale 0;
  - o juiz por `model_roles` e `get_model(role="grader")`, com os providers `anthropic/` e `ollama/`;
  - o `--batch`, que cobre as chamadas feitas dentro do scorer (só o juiz passa por ele, porque o assistente tem cliente próprio);
  - logs `.eval`, `samples_df()` para o `cases.csv` e o `inspect view`.
- Fica por nossa conta:
  - o `@solver` multi-turno que conduz o `Assistant` do LangGraph e copia tool calls e escalonamento para `state.metadata` e `store` (o `agent_bridge` e o `as_solver` só servem para agente que chama modelo pelo Inspect);
  - o scorer com os checks puros e a chamada ao juiz;
  - o teto de US$ 5 por rodada e a estimativa, porque o Inspect só tem `cost_limit` por amostra e as chamadas do assistente ficam fora da conta dele;
  - o preço dos modelos para o Inspect via `set_model_cost` ou `--model-cost-config`, que pode vir do `genai-prices`.
- Amostra com erro sai do denominador do Inspect, e a casca precisa contá-la como `error`, o que o §5 já previa.
- `pandas`/`pyarrow` (para o `samples_df`) e `openai` (provider do Ollama) não são dependências obrigatórias do `inspect-ai`, então declaramos o que usarmos.
- Risco: o `retry_on_error` roda o juiz de novo junto com a amostra.
- Primeira tarefa sugerida para o plano: um spike com uma task mínima (solver falso, `Score.unscored`, `Epochs(3, "at_least_3")`, `fail_on_error=False`, juiz em `--batch`) para confirmar o denominador e o lote antes de fechar o desenho da casca. Ela se junta ao "teste prático do lote" que o §5 já põe como primeira tarefa.
