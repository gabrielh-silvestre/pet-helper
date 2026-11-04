# Spec: adoção de ferramentas de mercado (OTel, LangSmith, DSPy) e varredura de código próprio

## Metadata

- Slug: market-tools-adoption
- Rounds: 11 (+ Round 0 de topologia)
- Final Ambiguity Score: 15%
- Type: brownfield
- Generated: 2026-11-03
- Threshold: 0.2
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

## Clarity Breakdown

| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.88 | 0.35 | 0.31 |
| Constraint Clarity | 0.88 | 0.25 | 0.22 |
| Success Criteria | 0.82 | 0.25 | 0.21 |
| Context Clarity | 0.80 | 0.15 | 0.12 |
| **Total Clarity** | | | **0.85** |
| **Ambiguity** | | | **0.15** |

## Topology

| Component | Status | Description | Coverage |
|-----------|--------|-------------|----------|
| OpenTelemetry | active | OTel vira a camada única de trace no lugar do JSONL próprio de `src/pet_helper/trace` | AC1-AC5 |
| LangSmith | active | Destino dos traces quando há `LANGSMITH_API_KEY`, traces das rodadas de eval também | AC2, AC3, AC6 |
| DSPy | active | Adotado no juiz LLM e no system prompt do assistente, cada um sob regra de aceite própria | AC7-AC9 |
| Varredura geral | active | Outras peças próprias que uma ferramenta de mercado cobre, o crítico propõe, o dono decide cada corte | AC10 |

## Goal

Reduzir o código próprio do pet-helper trocando peças feitas à mão por ferramentas de mercado (OpenTelemetry, LangSmith, DSPy e o que a varredura achar), aceitando que a ferramenta não cubra 100% e fechando cada gap com código pontual, sem regressão de comportamento do assistente.

## Constraints

- Prazo: tentar entregar o escopo completo hoje (2026-11-03). O que não fechar fica isolado na worktree/PR da unidade.
- PII: CPF e demais PII continuam mascarados antes de qualquer dado sair da máquina (regra atual do Guard: Presidio regex-only + reconhecedor de CPF, CPF pseudonimizado). Vale para o envio ao LangSmith e para o arquivo local.
- Sem `LANGSMITH_API_KEY` (caso de quem avalia o desafio), o trace vai para arquivo local via exporter OTel. Assistente e evals rodam sem conta.
- TTFT e eventos de domínio (escalação, segundo CPF recusado, NOT_FOUND etc.) são obrigatórios no trace. O que a instrumentação/LangSmith não medir vira código pontual.
- Evals ficam no Inspect AI (roda sem conta). Traces das rodadas vão ao LangSmith via OTel quando há chave. LangSmith `evaluate()` não substitui o Inspect.
- Gasto pago: teto de US$30 no total da unidade (otimização do juiz + otimização do prompt do assistente + rodada de paridade). Cada etapa paga mostra estimativa e para antes de gastar. O teto de US$5 por rodada de eval do loop continua.
- Nada que chame modelo real roda automaticamente (hook, CI, schedule).
- Otimizadores DSPy treinam só nos casos de dev. Casos held-out nunca entram em otimização (hook `evals/held_out/` continua).
- Requisitos do desafio preservados (`docs/requisitos.md` itens 5, 6 e linha 96): observabilidade mínima que explique por que o bot respondeu algo, e estimativa de custo e latência por conversa no README.

## Non-Goals

- Migrar evals para LangSmith `evaluate()` ou datasets LangSmith.
- Trocar a camada LangGraph/LangChain do agente por DSPy (ReAct do DSPy).
- Backend OTel local com UI (Jaeger/Phoenix em Docker).
- Mudar regras de negócio, tools ou o comportamento esperado do assistente.

## Acceptance Criteria

- [ ] AC1: Com `LANGSMITH_API_KEY`, uma conversa gera um trace no LangSmith com chamadas de modelo e tool, tokens, custo e latência por turno.
- [ ] AC2: Sem `LANGSMITH_API_KEY`, a mesma conversa gera spans OTel num arquivo local, e o assistente funciona igual.
- [ ] AC3: Nenhum CPF, e-mail ou telefone em claro chega ao LangSmith nem ao arquivo local (auditável por teste sem modelo).
- [ ] AC4: TTFT por chamada de modelo aparece no trace (LangSmith e arquivo local).
- [ ] AC5: Eventos de domínio aparecem no trace ligados ao turno em que ocorreram.
- [ ] AC6: Uma rodada de eval no Inspect AI, com chave, deixa os traces das conversas no LangSmith.
- [ ] AC7: Juiz com DSPy: aceito só se TPR/TNR no split de teste de rótulos ficar igual ou melhor que o do prompt atual. Reprovado, fica o prompt atual e o ADR registra o número.
- [ ] AC8: System prompt do assistente otimizado com DSPy (treino só em dev): substitui o atual só se passar pelo passo de aceitar/rejeitar do `loop-melhoria`, held-out incluso, sem piorar nenhum caso hoje verde. Reprovado, fica o atual e o ADR registra.
- [ ] AC9: ADR do DSPy registra veredito por alvo (juiz, assistente), custo gasto e resultado.
- [ ] AC10: Varredura: o crítico entrega a lista de peças próprias com a ferramenta que as substitui e o gap pontual. Cada corte é aprovado pelo dono individualmente. Os aprovados são removidos.
- [ ] AC11: `uv run pytest -q --cov` verde com piso de 80% de cobertura de branch e `uv run prek run --all-files` verde.
- [ ] AC12: Uma rodada paga de paridade (estimativa antes, dentro do teto, `--judge none`) com resultado igual ao da baseline P1a, rodada na `main` antes de qualquer mudança desta unidade (decisão do dono, 2026-11-03: a última rodada salva em `evals/results/` é anterior a mudanças em `checks.py`, `orders/service.py` e 5 casos). Caso que cai repete 10 vezes pela regra de caso decisivo do `loop-melhoria`.
- [ ] AC13: `docs/architecture.md` e ADRs atualizados para as decisões revertidas (LangSmith e OTel antes rejeitados em 2026-10-29).

## Eval cases

Esta unidade não cria nem muda comportamento do assistente. Nenhum caso novo de eval. A suíte atual é a prova de paridade (AC12), e o prompt do AC8 passa pela suíte atual mais os held-out.

## Assumptions Exposed & Resolved

| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| LangSmith exige conta e por isso não serve | Quem avalia não tem conta | OTel é a camada, LangSmith é destino opcional, sem chave, arquivo local |
| Dados sintéticos dispensam mascaramento | Conversas vão para nuvem | Mascarar antes de enviar continua |
| TTFT e eventos de domínio podem cair | Contrário: e se não forem necessários? | Dono: ambos obrigatórios |
| LangSmith evaluate deveria substituir o Inspect | Avaliador sem conta | Inspect fica |
| DSPy só serve no juiz (pesquisa) | Dono quer também no assistente | Ambos, cada um com regra de aceite e teto total US$30 |
| Crítico decide cortes da varredura | Dono quer controle | Crítico propõe, dono decide cada corte |

## Technical Context

- `src/pet_helper/` tem 6705 linhas: evals 3508, conversation 898, trace 735, cli 431, gateway 362, orders 283, guard 251.
- `trace/` (735): `callback.py` (TraceCallbackHandler, TTFT em `on_llm_new_token`, `BudgetExceeded`, `fallback_scope`), `tracer.py` (JSONL por sessão, chaves `gen_ai.*` + `pet_helper.*`, `emit` de eventos de domínio por contextvar, máscara injetada), `cost.py` (genai-prices), `metrics.py` (`pet-helper metrics`), `pii.py` (auditoria regex sobre arquivos). Callers: conversation/service.py, graph.py, tools.py, gateway/anthropic.py, cli, evals/judge.py, task.py, budget.py, round.py, labels.py.
- `evals/` (3508): Inspect AI 0.3.286 (Task, Epochs, scorer, solver). Próprio: `round.py` (914), `labels.py` (681, TPR/TNR), `judge.py` (601, prompts em 40-103, Message Batches direto), `task.py` (409), `analysis.py`, `checks.py`, `cases.py`, `budget.py`, `stats.py` (Wilson), `confirm.py`, `fixtures.py`. `docs/architecture.md:245` estimava 250-350 linhas para a casca.
- Presidio só em `guard/service.py:128-162`. `Guard.mask` injetado no Tracer e em `evals/task.py`.
- Prompts: assistente `conversation/prompt.py:6-33`. Juiz `evals/judge.py:40-103`. Nenhuma otimização de prompt existe.
- `budget.py` depende de `cost_usd` (genai-prices) para o teto por rodada.

## Ferramentas existentes (pesquisa)

- **LangSmith**: tracing LangGraph por env vars (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`). Tokens, custo (tabela própria) e latência automáticos com ChatAnthropic. Ingere OTLP em `https://api.smith.langchain.com/otel` (header `x-api-key`). `langsmith[otel]` com `LANGSMITH_OTEL_ENABLED`/`LANGSMITH_OTEL_ONLY`. Mascaramento por `Client(hide_inputs/hide_outputs)` ou `create_anonymizer` (regex ou função. Presidio citado como anonimizador de terceiros), válido só para runs desse Client. Métricas `first_token` garantidas só via `@traceable`/wrappers, pelo callback LangChain precisa teste empírico. Metadata/tags via RunnableConfig, sem conceito de evento de domínio (run filho ou metadata). Developer: US$0, 1 seat, 5k traces/mês, retenção 14 dias. Docs: docs.langchain.com/langsmith/{trace-with-langgraph,trace-with-opentelemetry,mask-inputs-outputs,cost-tracking,pricing-plans}.
- **OpenTelemetry**: GenAI semconv em Development (repo semantic-conventions-genai), inclui `gen_ai.server.time_to_first_token`. Instrumentações: `openinference-instrumentation-langchain` 0.1.81 (convenção OpenInference), `opentelemetry-instrumentation-langchain` 0.62.4 (OpenLLMetry, `gen_ai.*`), e equivalentes para o SDK Anthropic. Exporter para arquivo exige exporter próprio pequeno ou Collector com file exporter. `ConsoleSpanExporter` existe.
- **DSPy** 3.4.1: Claude nativo. Módulo DSPy roda dentro de um nó LangGraph sem trocar a camada de chat model. `dspy.Tool.from_langchain`. Otimizadores: BootstrapFewShot (~10 exemplos), com RandomSearch (50+), MIPROv2 (200+, inviável aqui), GEPA (reflexivo, métrica com feedback textual, eficiente em rollouts). Custo de centavos a dezenas de US$. Observabilidade: MLflow autolog. OpenInference tem instrumentação DSPy (não validada). Encaixe: juiz como programa de uma etapa contra rótulos (bom). System prompt via GEPA com feedback das evals (possível, mais caro e arriscado).
- **Já escolhidas e mantidas**: Inspect AI (runner), genai-prices (custo local para o budget), Presidio (máscara).

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| Trace | core | spans, TTFT, tokens, custo, eventos de domínio | exportado por OTel para LangSmith ou arquivo |
| Exporter | supporting | destino (LangSmith OTLP, arquivo) | escolhido pela presença de `LANGSMITH_API_KEY` |
| Mask | supporting | CPF, e-mail, telefone | aplicada antes de qualquer exporter |
| Domain Event | supporting | nome, turno | anexado ao span do turno |
| Eval Round | core | casos, epochs, custo, resultado | roda no Inspect, traces vão ao exporter |
| Judge | core | prompt, TPR/TNR | pode ser otimizado por DSPy contra rótulos |
| System Prompt | core | texto, sha256 | pode ser otimizado por DSPy, passa no aceitar/rejeitar |
| Custom Piece | supporting | módulo, ferramenta substituta, gap | proposto pelo crítico, cortado pelo dono |
| Budget | supporting | teto US$30 unidade, US$5 rodada | limita etapas pagas |

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 4 | 4 | - | - | - |
| 3 | 6 | 2 | 0 | 4 | 67% |
| 6 | 7 | 1 | 0 | 6 | 86% |
| 9 | 9 | 2 | 0 | 7 | 78% |
| 11 | 9 | 0 | 0 | 9 | 100% |

## Interview Transcript

Transcrição (Q&A (11 rounds)):

- R0 Topologia: 4 componentes (OTel, LangSmith, DSPy, varredura). **Está certa.**
- R1 Prazo (hoje): **Tentar entregar hoje o escopo completo. Se não der, fica numa wt/PR isolada.**
- R2 PII na nuvem: **Mascarar antes de enviar.**
- R3 Sem chave LangSmith: **Arquivo local via exporter OTel. JSONL próprio sai.**
- R4 Prova: **Testes sem modelo + 1 rodada paga de paridade.**
- R5 TTFT/eventos de domínio: **Ambos obrigatórios.**
- R6 Runner de eval: **Fica no Inspect. Traces das rodadas vão ao LangSmith.**
- R7 DSPy nesta unidade: **Adotar já se o veredito for positivo.**
- R8 Onde o DSPy entra: **Juiz e também o prompt do assistente.**
- R9 Teto de gasto: **US$30 no total.**
- R10 Aceite do prompt do assistente: **Mesmo passo de aceitar/rejeitar do loop, held-out incluso.**
- R11 Varredura: **Dono quer ver a lista e decidir cada corte.**
