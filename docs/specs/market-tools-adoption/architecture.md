# Arquitetura: adoção de ferramentas de mercado

Spec: [spec.md](spec.md). Plano: [plan.md](plan.md).

Decisões da cadeira do dono para a unidade `market-tools-adoption`, tomadas pelo crítico `opus` em 2026-11-03 sobre o plano aprovado pelo consenso RALPLAN. As duas decisões críticas alteram `docs/architecture.md`, e o texto novo entra na História 6 com a data 2026-11-03.

## 1. Instrumentação OTel pelo OpenLLMetry (A1, decisão do dono)

- **Escolha:** o `opentelemetry-instrumentation-langchain` (OpenLLMetry, fixado em `>=0.62.4,<0.63`) cria os spans de modelo e de tool, com os tokens em `gen_ai.usage.*`. O span raiz do turno é nosso, aberto como contexto corrente em `turn_scope`, e os spans do OpenLLMetry descendem dele (com cerca de 4 spans do LangGraph no meio). Por cima da biblioteca fica só código pontual nosso: o `on_llm_new_token` grava o TTFT no span ainda aberto; o `on_llm_end`/`on_llm_error` grava custo, tokens e status antes do freio de custo relançar; o `on_tool_start`/`on_tool_end` grava argumentos, resultado (o `OrderView` da allowlist), status e erro; eventos de domínio vão como `add_event` no raiz. Tudo é espelhado em `langsmith.metadata.*`. O span de um `run_id` é lido por `handler.spans[run_id].span`, atributo interno do pacote, marcado como `shortcut:` e protegido por teste de contrato. A captura de conteúdo do OpenLLMetry fica desligada (`TRACELOOP_TRACE_CONTENT=false`), porque o prompt leva dados de pedido fora da allowlist.
- **Motivo:** o dono preferiu a ferramenta de mercado criando os spans, aceitando acoplar a uma API interna de um pacote 0.x para o TTFT, o custo e os dados de tool. O crítico tinha decidido A4 (handler próprio criando os spans), porque nenhuma instrumentação LangChain mede TTFT nem conhece eventos de domínio; o dono revogou essa premissa.
- **Fallback:** A4 volta se o teste de contrato da H1 não conseguir prender TTFT ou custo ao span, se o span perder `gen_ai.usage.*`, ou se uma versão 0.62.x mudar `spans`/`SpanHolder`. Fica registrado no ADR 0003.
- **Requisito 6 do desafio:** atendido. O span de tool mostra argumentos e resultado mascarados, o que explica por que o bot respondeu. O prompt de sistema não entra no trace, como hoje, porque é texto fixo do repositório.
- **Alternativas:** A4 handler próprio (premissa do crítico, revogada pelo dono, vira fallback); A2 OpenInference (sem TTFT, convenção `llm.*`); A3 `langsmith[otel]` (sem TTFT nem eventos de domínio, e um segundo caminho que poderia vazar PII sem máscara).
- **Classe:** crítica. Regra tocada: `docs/architecture.md`, seção 2 (Tracing). Texto a corrigir na H6, com data 2026-11-03: a escolha "Trace próprio em JSONL" passa a ser spans OTel do OpenLLMetry com o código pontual acima, exporter de arquivo sem chave, OTLP do LangSmith com chave e `MaskingSpanExporter` como ponto único de máscara; "OpenTelemetry com OpenInference: rejeitada" passa a ser "OTel adotado via OpenLLMetry; OpenInference e `langsmith[otel]` seguem rejeitados"; sai a frase "Não são ids do OTel... um exportador futuro faz o mapeamento".
- **Ação:** o crítico usou `web` (esperada `jury`); a decisão final é humana.
- **origin:** human
- **Premissa hexlog:** `docs-specs-market-tools-adoption:01a1a20c-4d8a-77d3-b1b9-0752e3b733b6` (revoga a `simulated` `docs-specs-market-tools-adoption:01a1a1f6-04a7-758a-a05e-1191cabbf9eb`)

## 2. Ponto único de máscara: `MaskingSpanExporter` (B1)

- **Escolha:** um exporter decorador na frente de todo exporter (arquivo e OTLP do LangSmith) aplica `Guard.mask` (Presidio regex-only, CPF como pseudônimo HMAC). As chaves opacas são definidas por nome exato, nunca por prefixo: `_OPAQUE_FIELDS`, mais `session_id`, `turn`, `langsmith.span.kind`, `langsmith.metadata.session_id`, `turn` e `cost_usd`, `pet_helper.cost_usd`, `price_unknown`, `streaming` e `fallback`. Qualquer outra chave, incluindo o espelho de eventos, é mascarada como string comum. A máscara cobre nome do span, atributos, eventos, `status.description`, links e eventos de exceção. O truncamento em 10.000 caracteres vem depois da máscara, com `SpanLimits` sem limite. O roteamento lê o `session_id` cru, antes da máscara, e o interruptor `PET_HELPER_TRACE_CONTENT` é lido no mesmo exporter.
- **Motivo:** `docs/architecture.md` (seção 2) já fixava a máscara num único ponto, no sink do trace, por `Guard.mask`. B1 mantém a função e a regra e só troca o sink do JSONL pelo exporter OTel. A spec exige máscara antes de qualquer dado sair da máquina, arquivo local incluído, e só um decorador na frente de todo exporter cobre os dois destinos com um caminho de código. `SpanProcessor.on_end` recebe span somente leitura, então a máscara tem que acontecer no export.
- **Alternativas:** B2, anonymizer do Client LangSmith (cobre só runs desse Client, não o OTLP nem o arquivo, e o plano desliga esse tracer); B3, Collector com redação (processo extra, fora do escopo); máscara só no handler (quebra o ponto único, e o espelho e as exceções escapam).
- **Classe:** comum. Refina `docs/architecture.md` seção 2: na H6, "o sink do trace" passa a ser "o `MaskingSpanExporter`, na frente de todo exporter".
- **Ação:** caminho curto (sem ação).
- **origin:** simulated-approved
- **Premissa hexlog:** `docs-specs-market-tools-adoption:01a1a1f7-b215-79a6-b84f-adb7f3091575`

## 3. DSPy offline no módulo `optimize`

- **Escolha:** o DSPy vive só no módulo novo `pet_helper.optimize`, um módulo próprio da camada Service (não uma subparte de `evals`), instalado só pelo extra `optimize`. Nenhum outro módulo o importa. O resultado entra no runtime só como texto commitado (`SYSTEM_PROMPT` em `conversation/prompt.py`, `CRITERION_PROMPTS` e `FEW_SHOT_EXAMPLES` em `evals/judge.py`), e assim `system_prompt_sha256`, `judge_prompt_sha256` e a validação do juiz por modelo e hash congelado mantêm o mecanismo de hoje. Juiz com BootstrapFewShot, assistente com GEPA.
- **Motivo:** as duas regras de aceite do dono (TPR/TNR do juiz lido uma vez com prompt congelado; assistente pelo passo de aceitar/rejeitar com held-out) exigem um candidato que seja texto fixo com hash, e um programa DSPy vivo não tem essa identidade. A spec exclui trocar a camada LangGraph por DSPy: um nó DSPy não compartilha tools nem estado e traria um segundo cliente de modelo (litellm), com custo, trace e fallback próprios. O `dspy` 3.4.1 puxa 16 dependências, e o extra as mantém fora da instalação de quem avalia. O `dspy` traz `DummyLM`, BootstrapFewShot e GEPA, então H4 e H5 seguem em TDD sem modelo. O módulo é próprio porque depende de `evals` e de `conversation`, e nada depende dele.
- **Alternativas:** módulo DSPy num nó do LangGraph (fora do escopo da spec, segundo cliente de modelo e de trace, dependências no avaliador); juiz como `dspy.Predict` vivo (quebra a validação por hash congelado); `optimize` como subparte de `evals` (ilha de import opcional num módulo base); MIPROv2 e GEPA no juiz (inviáveis pelo n).
- **Classe:** crítica. Regra tocada: `docs/architecture.md`, seção 4 (mapa de módulos). Texto a corrigir na H6:
  1. "Service: `conversation`, `guard`, `evals`." passa a incluir `optimize`.
  2. Linha nova na tabela para `optimize`, com os comandos `pet-helper optimize judge` e `pet-helper optimize assistant` (offline, pagos, cobertos pelo hook de comando pago, instalados só com o extra), o que o módulo esconde e a regra de que `dspy` só é importado ali.
  3. Na linha de `cli`, os comandos passam a incluir `optimize`, com import preguiçoso e mensagem clara quando o extra não está instalado.
  4. Na linha de `conversation`, o seam `build_assistant(system_prompt: str | None)`. Sem o argumento, o prompt fica byte a byte igual.
  5. Nas alternativas da seção 4: "DSPy no runtime: rejeitada em 2026-11-03, ver ADR 0004".
- **ADR:** sim, `docs/adr/0004-dspy-offline-optimizer.md` (difícil de reverter, surpreendente, trade-off real contra a premissa de mercado primeiro). O ADR 0003 fica só com OTel e LangSmith. Os dois são gravados na H6, depois dos resultados de P2 a P4.
- **Ação:** degradada (esperada `jury`, usada `web`).
- **origin:** simulated-approved
- **Premissa hexlog:** `docs-specs-market-tools-adoption:01a1a1fb-ee3f-70d2-9244-af7027b3660d`

## 4. Rodadas P1a, P1b e P4 com `--judge none`

- **Escolha:** as três rodadas rodam no mesmo modo, `--judge none`. Os critérios do juiz ficam pendentes, não perdidos: os transcripts são gravados e `--rejudge <rodada>` pode julgá-los depois com um juiz validado.
- **Motivo:** `docs/directives/loop-melhoria.md` diz que juiz sem validação não decide aceitação, e hoje não há `human.jsonl` nem piso. `docs/architecture.md` (seção 5) já usa `none` na baseline com o juiz ainda não congelado. Misturar modos cria regressões `judge:*` falsas, porque um fail ou `unable` do juiz sobrepõe um pass determinístico (`evals/task.py:228-243`). Cada rodada sem juiz custa cerca de US$0,08, e o teto não tem folga. Os 4 casos de dev com critério do juiz também têm check determinístico.
- **Condições no P4 (compatibilidade com a R10):** "hoje verde" quer dizer 3/3 em P1a com `--judge none`. Todo caso verde em P1a que cair em P4 repete 10 vezes (rejeita com 2 ou mais falhas), capability incluída, mais o alvo, com held-out só por script. O relatório declara juiz `none` sem hash, pass^3, Wilson, diferença pareada e a lista de casos com critério do juiz pendente. `--rejudge` de P1a e P4 depois de P2 só roda por ordem explícita do dono dentro dos US$30. O plano já foi ajustado.
- **Alternativas:** juiz síncrono nas três (não decide e custa Sonnet); síncrono só no P4 (modos misturados); batch (mesmo problema de validade, mais a espera do batch no dia do prazo).
- **Classe:** comum. Refina `docs/architecture.md` seção 5 (modo do juiz). Não muda diretiva.
- **Ação:** caminho curto (sem ação).
- **origin:** simulated-approved
- **Premissa hexlog:** `docs-specs-market-tools-adoption:01a1a1fa-1d38-70c0-9e6a-75bfac15ebbd`
