# Arquitetura

Linha de base da arquitetura do pet-helper, decidida pelo critic da cadeira em 28/10/2026 numa rodada de bootstrap sem spec nem plano: as perguntas vieram direto do dono. Cada unidade de trabalho seguinte registra o que acrescenta em `docs/specs/<slug>/architecture.md` e aponta para esta. Toda decisão nasce com `origin: simulated` e passa a `simulated-approved` com o aval do dono; as desta linha de base foram aprovadas pelo dono em 28/10/2026. Em 29/10/2026 o dono revisou o que já existia pronto na comunidade e trocou peças próprias por ferramentas curadas: `genai-prices` e callback do LangChain no tracing, Presidio na máscara e Inspect AI na eval (decisões 2, 4 e 5, `origin: owner`). Em 30/10/2026 a unidade de entrega dos evals emendou as seções 2, 4 e 5 ([specs/eval-delivery/architecture.md](specs/eval-delivery/architecture.md)). Em 03/11/2026 a unidade `market-tools-adoption` emendou as seções 2, 4 e 5 ([specs/market-tools-adoption/architecture.md](specs/market-tools-adoption/architecture.md)): spans OpenTelemetry no lugar do JSONL próprio ([ADR 0003](adr/0003-otel-langsmith.md)), o módulo `optimize` com o DSPy fora do runtime ([ADR 0004](adr/0004-dspy-offline-optimizer.md)) e o modo do juiz das rodadas de paridade. Os fatos de modelo e preço foram conferidos em 28/10/2026 em `platform.claude.com/docs/en/about-claude/pricing` e `.../build-with-claude/prompt-caching`; os do Ollama, em `docs.ollama.com` e `hub.docker.com/r/ollama/ollama`.

## 1. Modelo por papel

### Escolha

- Assistente: `claude-haiku-5-5`, com thinking adaptativo (o padrão do modelo) e `effort: low`, configurável por variável. A requisição nunca leva `temperature`, `top_p` nem `top_k`: no Haiku 5.5 um valor fora do padrão dá 400, e na versão de API `2026-10-21` qualquer valor dá 400. O `max_tokens` fica folgado, porque o thinking conta nele.
- Juiz: `claude-sonnet-5-5` com `effort: medium` e saída estruturada (`output_config.format`) no formato `{critique, verdict}`, com a crítica antes do veredito. Não usa `tool_choice` forçado, que dá 400 no Sonnet 5.5.
- Caminho sem chave: Ollama em Docker, na imagem `ollama/ollama:0.40.12` (versão fixada, nunca `latest`). Um serviço de inicialização no compose roda `ollama pull` do tag configurado e o app só sobe depois dele.
- Modelo local: `llama3.1:8b` (4,9 GB, contexto de 128K, capacidade `tools`). Português está entre as línguas oficialmente suportadas pela Meta (MMLU em português: 62,12). A licença é a Llama 3.1 Community License, com a atribuição "Built with Llama" no README. `llama3.2:3b` (2,0 GB) fica como opção por variável para máquina com pouca RAM.
- Integração local: o `ChatOllama` do `langchain-ollama`, sobre a API nativa, com `num_ctx` de 8192 explícito e temperature 0. Temperature 0 vale só neste adapter, porque a API da Anthropic rejeita o parâmetro.
- Fallback em execução: da Anthropic para o Ollama, só em erro de transporte (conexão, timeout, 429, 5xx ou 529) e depois de 1 retry do SDK. Nunca em 4xx nem em `stop_reason: refusal`. O Haiku 5.5 não tem fallback do lado do servidor, então uma recusa é uma falha irrecuperável e vira Repeated Failure, como o glossário já define. O fallback fica desligado nas rodadas de eval.
- Contrato de configuração (variáveis de ambiente):
  - `PET_HELPER_PROVIDER` (`anthropic` | `ollama`). Sem valor: `anthropic` se existir `ANTHROPIC_API_KEY`, senão `ollama`.
  - `PET_HELPER_ASSISTANT_MODEL` (o modelo Anthropic do assistente), `PET_HELPER_OLLAMA_MODEL` (o modelo Ollama, primário e fallback; padrão `llama3.1:8b`) e `PET_HELPER_JUDGE_MODEL`.
  - `PET_HELPER_FALLBACK` (`ollama` | `none`).
  - `OLLAMA_BASE_URL`.
  - `PET_HELPER_EFFORT`.
  - `PET_HELPER_KIT_DIR`: diretório do kit de dados (padrão `kit`, relativo ao diretório de trabalho).
- Estimativa de custo. É uma premissa: os valores medidos a substituem.
  - Preços: Haiku 5.5 a US$ 0,10 por MTok de entrada e US$ 0,50 de saída (prompt de até 100K), cache read a US$ 0,01 e escrita de 5 min a US$ 0,125. Sonnet 5.5 a US$ 2 e US$ 10, cache read a US$ 0,10. Batch com 50% de desconto: Sonnet a US$ 1 e US$ 5.
  - Conversa de 4 turnos: cerca de 6 chamadas, cada uma com uns 3,9K tokens de entrada e uns 250 de saída. Dá cerca de US$ 0,003 sem cache, ou R$ 0,02.
  - Rodada de 30 casos × 3 execuções: cerca de US$ 0,30 do assistente e de US$ 0,20 a 0,40 do juiz. Menos de US$ 1, abaixo do teto de US$ 5.

### Motivo

O Haiku 5.5 é o modelo fraco e barato que a premissa `qualidade-vem-do-sistema` pede. O juiz roda numa classe acima da do avaliado. Não saiu nenhum Llama pequeno depois do Llama 4, então o 3.1 8B é o menor Llama com tool calling e português suportado que ainda roda em CPU com 16 GB, o que atende `avaliar-sem-pagar`. O `ChatOllama` aceita `num_ctx` e monta `usage_metadata` a partir de `prompt_eval_count` e `eval_count`. Sem ele, o prompt de sistema mais as tools mais o histórico passam do contexto padrão e são truncados sem aviso. Usar fallback só em erro de transporte evita que o Llama responda o que o Claude recusou.

### Alternativas

- Haiku como juiz: rejeitada, porque o juiz teria o mesmo porte do avaliado.
- `llama3.2:3b` como padrão: rejeitada, porque é mais fraco em tool calling. Fica como opção.
- Docker Model Runner: rejeitada, porque exige o recurso habilitado na máquina de quem avalia e a documentação não confirma tool calling.
- Servidor do llama.cpp: rejeitada, porque exige baixar o GGUF à mão e usar `--jinja`.
- `ChatOpenAI` apontado para o Ollama: rejeitada, porque só ajusta o contexto por Modelfile e não tem `tool_choice`.

`origin: simulated-approved`. Premissas: `qualidade-vem-do-sistema`, `avaliar-sem-pagar`, `modelo-real-so-a-mao`.

## 2. Tracing e observabilidade

Emendada em 2026-11-03 pela unidade `market-tools-adoption` ([specs/market-tools-adoption/architecture.md](specs/market-tools-adoption/architecture.md), [ADR 0003](adr/0003-otel-langsmith.md)): o JSONL próprio deu lugar a spans OpenTelemetry. A escolha de 2026-10-29 de não usar OpenTelemetry nem LangSmith foi revertida; o que continua rejeitado está nas alternativas abaixo.

### Escolha

- Spans OpenTelemetry, um trace por turno. A instrumentação do LangChain do OpenLLMetry (`opentelemetry-instrumentation-langchain`, fixada em `>=0.62.4,<0.63`) cria os spans de modelo e de tool. O span raiz `turn` é nosso e leva `session_id` e `turn`; os spans do OpenLLMetry descendem dele, com os spans `execute_task` e `invoke_agent` do LangGraph no meio (um por nó e por roteamento). A conversa se agrupa pelo `session_id`.
- Destinos:
  - Arquivo, sempre: `traces/<AAAA-MM-DD>/spans/<session_id>.jsonl` no chat, e `traces/spans/<session_id>.jsonl` dentro da pasta da rodada na eval. Uma linha por span, no JSON do SDK do OpenTelemetry (`to_json`).
  - LangSmith, só com `LANGSMITH_API_KEY`: OTLP em `https://api.smith.langchain.com/otel/v1/traces`, no projeto `LANGSMITH_PROJECT` (padrão `pet-helper`). Sem a chave, nada sai da máquina e o assistente se comporta igual. O tracer próprio da LangSmith fica desligado (`tracing_context`), porque as runs dele levariam texto sem máscara.
- O código nosso por cima da biblioteca é pontual e escreve no span de cada execução (`run_id`):
  - Modelo: tempo até o primeiro chunk em `gen_ai.server.time_to_first_token` (em segundos; ausente quando a chamada não fez streaming), custo, `pet_helper.fallback`, `pet_helper.streaming`, tokens de cache e, no caminho de erro do freio de custo, tokens e status. Os tokens de entrada e saída do caminho normal vêm do OpenLLMetry.
  - Tool: o span se chama `execute_tool <tool>` e leva argumentos, resultado (o `OrderView` da allowlist ou o texto da busca), `pet_helper.status` e `error.message`.
  - Eventos de domínio entram como span events do raiz do turno, com os campos `pet_helper.*`: `guard.input` (CPF detectado, suspeita de injection), `retrieval` (query e hits com `source` e `score`), `order.refused` (`not_found` ou `other_customer`, [ADR 0001](adr/0001-order-access-by-cpf-and-order-number.md)), `guard.output` (SKUs inválidos, fonte não recuperada) e `escalation` (motivo). A mensagem do cliente e a resposta vão como `gen_ai.input.messages` e `gen_ai.output.messages` do raiz, em JSON de mensagens, com as fontes citadas em `pet_helper.sources`.
  - Espelho para a LangSmith: TTFT, custo, `session_id`, `turn` e cada evento de domínio também saem em `langsmith.metadata.*`, e `langsmith.span.kind` marca o raiz como `chain`, o modelo como `llm` e a tool como `tool`.
- Os nomes seguem a convenção OpenTelemetry GenAI, com o prefixo `pet_helper.*` para o que ela não cobre (`cost_usd`, `price_unknown`, `fallback`, `streaming`, `status`). A fonte da convenção é o `client-inference.md` do repositório `open-telemetry/semantic-conventions-genai`, consultado em 30/10/2026, onde todos os atributos têm status Development.
- Interruptor de conteúdo: `PET_HELPER_TRACE_CONTENT` vem ligado por padrão, no chat e nas rodadas de eval (o runner fixa o valor `1`), e o texto livre sai mascarado. Com `PET_HELPER_TRACE_CONTENT=0`, os campos `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.retrieval.query.text`, `gen_ai.tool.call.arguments` e `gen_ai.tool.call.result` gravam só o tamanho. A captura de conteúdo do próprio OpenLLMetry fica sempre desligada (`TRACELOOP_TRACE_CONTENT=false`), porque o prompt do modelo leva dados de pedido fora da allowlist. O prompt de sistema, texto fixo do repositório, e as respostas intermediárias do modelo não entram no trace.
- A máscara é aplicada num único ponto, o `MaskingSpanExporter`, que fica na frente de todo exporter (arquivo e OTLP), por `Guard.mask`:
  - A detecção usa o Presidio (`presidio-analyzer` e `presidio-anonymizer`) só com regex, no motor `no_op`, sem modelo de NLP: e-mail e telefone pelos reconhecedores nativos (região BR) e CPF por um `PatternRecognizer` nosso, aplicado antes do telefone, porque 11 dígitos colidem com ele.
  - CPF, com ou sem formatação, vira o pseudônimo estável `cpf#<8 hex>` (HMAC-SHA256 com o sal de `PET_HELPER_TRACE_SALT`; sem a variável, um sal aleatório por processo). Todo número com formato de CPF é mascarado, mesmo com dígitos verificadores inválidos; a validação só decide se ele vincula o Session Customer.
  - E-mail e telefone são substituídos.
  - A máscara cobre nome do span, atributos, eventos, descrição do status, links e eventos de exceção. As chaves opacas, que são nomes e ids escolhidos por nós, pelo runtime ou pelo provedor (provider, modelo, nome da tool, `error.type`, `session_id`, `langsmith.span.kind`), são listadas pelo nome exato, nunca por prefixo: um id só de dígitos seria lido como telefone. Qualquer outra chave, o espelho `langsmith.metadata.*` incluído, é mascarada como string comum.
  - Depois da máscara, cada texto é truncado em 10.000 caracteres (o corte acrescenta `[truncated N chars]`).
  - O `SpanProcessor.on_end` recebe o span somente leitura, então a máscara tem que acontecer na exportação, e a rota do arquivo da sessão lê o `session_id` antes dela.
- O pedido entra no trace e no modelo como `OrderView`, com uma allowlist de campos: número, status, itens, total, região, meio de pagamento, data de entrega e dias desde a entrega. Nome e endereço nunca saem do módulo de pedidos.
- O CPF digitado pelo cliente é trocado por `[CPF informado]` antes de chegar ao modelo. O backend guarda o CPF no estado da sessão como Session Customer.
- Um teste sem modelo varre os traces de exemplo atrás de padrões de CPF e e-mail.
- O histórico da conversa nunca é reescrito depois de enviado: a máscara acontece antes do envio e na exportação. Reescrever o histórico invalidaria os blocos de thinking e o cache. Exceção, decidida pelo dono em 2026-10-30: depois de uma regeneração bem-sucedida do guard de saída, a resposta inválida e a mensagem corretiva saem do histórico com `RemoveMessage`. Só o fim do turno atual muda, então o prefixo em cache até a mensagem do cliente continua válido. Os blocos de thinking assinados, porém, dependem de tudo o que veio antes deles: por isso, na mesma regeneração, as mensagens do assistente que sobram depois da corretiva perdem os blocos `thinking` e `redacted_thinking` (copiadas sem eles, com o mesmo `id`), e isso só acontece depois que a resposta regenerada passa no guard. Emenda aprovada pelo dono em 2026-10-30.
- O `BaseCallbackHandler` do LangChain deixou de criar eventos: ele guarda o freio de custo (`BudgetExceeded`, preço desconhecido) e a cola descrita acima, e escreve nos spans do OpenLLMetry por `run_span(run_id)`. Esse acesso lê o atributo interno `spans` do handler da biblioteca (`shortcut:` no código, protegido por teste de contrato). Quando o freio relança antes de o LangChain chamar o callback de erro, o `fail_run` encerra o span. O `input_tokens` é o do LangChain, que já soma cache read e write.
- O `uninstrument()` do OpenLLMetry 0.62.4 não remove os wrappers do LangChain e do LangGraph. Por isso `otel.install()` instrumenta uma vez e encaminha os spans ao provider da última instalação.
- O comando `pet-helper metrics <pasta>` lê os `spans/*.jsonl`, sem chamar modelo, e imprime uma tabela por provider e modelo:
  - chamadas;
  - TTFT p50/p95;
  - TPS mediano (tokens de saída sobre o tempo depois do primeiro token);
  - tokens de entrada, saída, cache read e cache write;
  - custo em US$, lido de `pet_helper.cost_usd` (calculado pela `genai-prices`; a versão da lib vai no relatório como fonte e data dos preços; o Ollama custa zero);
  - taxa de acerto de cache: `cache_read / input_tokens`;
  - chamadas e taxa de erro por tool;
  - quantidade e taxa de fallback;
  - escalonamentos por motivo;
  - custo e latência por conversa, que alimentam o README.
- Rodadas gravadas no formato JSONL antigo (um evento por linha, fora de `spans/`) não são lidas pelo `metrics` (decisão do dono, 2026-11-03).
- O prompt cache usa dois breakpoints: um no fim do prompt de sistema estático (as tools são renderizadas antes dele, então entram junto) e um na última mensagem. O prompt de sistema fica byte a byte estável: sem data nem id. O mínimo cacheável do Haiku 5.5 é de 512 tokens, e o prefixo estático passa disso.
- O gateway sempre consome o modelo em streaming, para medir TTFT e TPS. A CLI mostra a resposta só depois do guard de saída.
- Estado em 2026-11-03: o sink JSONL antigo (`<session_id>.jsonl`, ao lado de `spans/`) segue ativo até o passo P1b do dono; depois dele, a História 3 da unidade o remove e `metrics`, a rodada e a auditoria de PII passam a ler só `spans/`. Esta seção descreve o estado depois dessa remoção.

### Motivo

O desafio pede logs que expliquem por que o bot respondeu algo. Spans OpenTelemetry dão um formato padrão, que um backend entende sem mapeamento nosso, e cortam o código de sink, ids e emissão que existia só para isso. O arquivo local mantém o caminho sem conta para quem avalia, e a chave da LangSmith liga um painel sem mudar o código. Mascarar num só ponto, na frente de todo exporter, torna `dado-pessoal-mascarado` verificável por teste e cobre o arquivo e a nuvem com o mesmo caminho. A allowlist e o CPF fora do modelo reduzem o dado pessoal na origem: o modelo não tem como repetir o que não viu. Mostrar a resposta só depois do guard troca latência percebida por correção. Em produção, o caminho seria fazer streaming por sentença já checada.

### Alternativas

- OpenTelemetry via OpenLLMetry (A1, escolha do dono): adotada em 2026-11-03. O dono aceitou acoplar a uma API interna de um pacote 0.x para obter TTFT, custo e dados de tool no span da biblioteca.
- Handler próprio criando os spans (A4, decisão do crítico): vira fallback, com gatilho no [ADR 0003](adr/0003-otel-langsmith.md).
- OpenInference: rejeitada em 2026-10-29 e mantida. Grava em arquivo sem backend, mas usa a convenção `llm.*`, não mede TTFT, não conhece os eventos do domínio, gera cerca de 15 spans por turno e só deixa mascarar por API privada.
- `langsmith[otel]`: rejeitada. Não mede TTFT nem leva eventos de domínio, e é um segundo caminho que poderia vazar dado pessoal sem a máscara.
- Phoenix: rejeitada pelo custo de setup (um processo a mais para quem avalia).
- LangSmith: rejeitada em 2026-10-29 por exigir conta e guardar os dados na nuvem. Adotada em 2026-11-03 só como destino opcional, ligado pela chave: quem avalia não precisa dela.
- Anonymizer do Client da LangSmith ou Collector com redação em vez do exporter de máscara: rejeitadas, porque cobrem só as runs do Client, ou exigem um processo a mais.
- Tabela de preços mantida à mão: rejeitada, porque a `genai-prices` já traz o Haiku e o Sonnet 5.5, inclusive a faixa acima de 100K tokens.
- Máscara só com regex própria: rejeitada pelo dono em favor do Presidio, padrão de mercado, apesar dos cerca de 316 MB de instalação. O Presidio não traz CPF, que segue como reconhecedor nosso.
- Mascarar em cada chamada de log: rejeitada, porque um ponto esquecido vaza.
- Mostrar a resposta em streaming e checar depois: rejeitada, porque o cliente veria um SKU inventado antes do bloqueio.

`origin: owner`. Premissas: `dado-pessoal-mascarado`, `pedido-so-do-dono`, `simples-e-medido`.

## 3. Busca nas políticas (RAG)

### Escolha

- Biblioteca e normalização:
  - BM25 com `bm25s` (0.3.16, MIT, só numpy obrigatório), no método `lucene` (o padrão, com IDF sempre não negativo).
  - Stemmer Snowball português do `PyStemmer`, sem download de modelo.
  - Antes de tokenizar, o texto passa para minúsculas e perde os acentos, para que "devolucao" encontre "devolução". Uma lista de stopwords em português é aplicada.
- Documentos e chamada:
  - A unidade indexada é o documento inteiro, de 150 a 400 palavras, sem chunking.
  - O id da fonte é o nome do arquivo em `kit/policies/`, e o título é o primeiro H1 do arquivo.
  - A tool `search_policies(query)` devolve até 2 documentos, com fonte, título, texto e score.
- O "não sei":
  - `found: false` só aparece quando nenhum termo da query bate em nenhum documento (score zero).
  - Não há limiar de score. A decisão de não saber é do modelo, por regra explícita do prompt: só afirmar regra presente nas fontes devolvidas e citá-la.
  - O juiz mede essa decisão pelo critério "acerto do não sei", que a [loop-melhoria.md](directives/loop-melhoria.md) já prevê.
- Citação: a resposta cita a fonte pelo título. O guard de saída confere que toda fonte citada veio da busca daquela sessão.
- Reindexação: o índice é montado em memória a cada início de processo, em milissegundos para 8 a 10 documentos, sem índice salvo. Na alteração ao vivo, basta criar o arquivo, rodar o script de validação do kit e começar uma sessão ou rodada nova.
- Recall de busca: cada caso de eval de política declara `expected_doc`, e o runner registra se ele apareceu nos hits.

### Motivo

BM25 é a sugestão do próprio desafio para poucos documentos, e reindexar sem baixar modelo é o que a alteração ao vivo exige. Um limiar de score não sobrevive a um corpus de 8 documentos: o IDF muda quando entra um documento novo, e temas de propósito fora da cobertura (como garantia estendida) dividem termos com políticas reais ("defeito", "garantia"). Por isso o limiar erraria justamente nos casos de "não sei". Documento inteiro deixa a citação trivial e cabe no contexto.

### Alternativas

- `rank-bm25`: rejeitada, porque está parada na 0.2.2 e tem IDF negativo quando o termo aparece em mais da metade dos documentos.
- `BM25Retriever` do LangChain: rejeitada, porque é uma camada a mais sobre o `rank-bm25`.
- Embeddings locais: rejeitada, porque baixam modelo e não fazem falta com 8 documentos.
- Buscar a cada turno, antes do modelo: rejeitada, porque perde a reescrita da query em "e para gato?".
- Colocar todas as políticas no prompt: rejeitada, porque não é RAG e o desafio pede RAG.

`origin: simulated-approved`. Premissas: `aderencia-ao-desafio`, `nao-inventa`.

## 4. Camadas MSC e mapa de módulos

### Escolha

As 4 camadas das notas do dono viram módulos dentro do MSC. Entra só o que o desafio exercita. Rate limit, filas e cache semântico ficam para a conversa sobre produção, sem código.

- Controller: `cli`.
- Service: `conversation`, `guard`, `evals`, `optimize`.
- Model: `gateway`, `knowledge`, `orders`, `catalog`.
- Transversal: `trace`.

Cada módulo é um módulo profundo, documentado pela interface. Os nomes internos e os arquivos abaixo do módulo ficam com o executor.

| Module | Interface | Hides | Seams / adapters |
|---|---|---|---|
| `cli` | `chat`, `eval`, `metrics`, `optimize` commands | argument parsing, terminal I/O, lazy import of `optimize` with a clear message when the extra is not installed | none; thin |
| `conversation` | `Assistant.turn(session_id: str, text: str) -> Reply(text, sources, escalation)` | LangGraph `StateGraph` (input guard -> agent <-> tools -> output guard), `InMemorySaver` keyed by `thread_id = session_id`, system prompt, tool registry, tool-loop iteration cap (overflow = Repeated Failure), Session Customer binding, Repeated Failure counter, Escalation state, one regeneration on output-guard failure; `build_assistant(system_prompt: str \| None)` takes a candidate system prompt, and without the argument the prompt is byte for byte `SYSTEM_PROMPT` | tool registry: adding a tool for the live change = one function + one registration; tools get identity from graph state, never from arguments; tool arguments validated against their schema, a validation error returns a tool error result |
| `guard` | `inspect_input(text) -> InputVerdict(model_text, cpf, injection_suspected)`; `check_reply(text, retrieved_sources) -> OutputVerdict(invalid_skus, unretrieved_sources)`; `mask(text) -> str` | Presidio analyzer and anonymizer (regex-only `no_op` engine), own CPF recognizer, pseudonymisation, accent-insensitive injection heuristics, SKU pattern | uses `catalog.exists` |
| `gateway` | `ModelGateway.complete(messages, tools) -> ModelResult(message, provider, model, usage, ttft_ms, fell_back, refused)` | provider selection by env, Anthropic/Ollama clients, effort/thinking settings, `cache_control` placement, per-provider sampling (none on Anthropic, temperature 0 on Ollama), retry and fallback policy, cross-provider message normalisation (text-only history to Ollama), refusal handling, `model.call` trace | adapters: `anthropic` (langchain-anthropic), `ollama` (langchain-ollama, `num_ctx` set), `fake` (scripted replies: the seam that lets the whole graph run in pytest without a model) |
| `knowledge` | `PolicyIndex.load(dir) -> PolicyIndex`; `.search(query, k=2) -> list[PolicyHit(source, title, text, score)]` | tokenisation, accent folding, stemming, stopwords, bm25s | none |
| `orders` | `OrderService.lookup(customer: Cpf \| None, order_number: str) -> Found(OrderView) \| NotFound \| NeedsIdentification \| Unavailable` | collapse of 404 and other-customer refusal into `NotFound` (ADR 0001), one retry on timeout, `days_since_delivery` via `Clock`, PII allowlist of `OrderView`, internal reason to trace | port `OrderApi.get(order_number, customer_cpf) -> ApiResponse(status, body)` with adapter `MockOrderApi` over `kit/orders.json` (200/404/403/503/timeout; failure by a fixed order number or `PET_HELPER_ORDER_API_FAILURE`); `Clock.today()` from `PET_HELPER_TODAY` or the system date |
| `catalog` | `Catalog.search(text, species=None, category=None) -> list[Product(sku, name, ..., in_stock)]`; `Catalog.exists(sku) -> bool` | CSV loading, similar-name matching | none |
| `trace` | `Tracer.event(kind, **fields)`; `summarize(paths) -> MetricsTable` | OpenTelemetry provider and the OpenLLMetry instrumentation (`trace/otel.py`), span layout and domain events on the turn's root span, LangChain callback handler (cost brake, plus TTFT, cost and tool data written on the OpenLLMetry spans), `MaskingSpanExporter` in front of every exporter, cost through `genai-prices` | exporters: the spans file under the run's directory (`spans/`; reserved-case runs under `evals/held_out/`) and LangSmith OTLP when `LANGSMITH_API_KEY` is set |
| `optimize` | `pet-helper optimize judge <round_dir>`; `pet-helper optimize assistant <targets.json>` (offline, paid, covered by the paid-command hook, installed only with the `optimize` extra) | DSPy (BootstrapFewShot on the judge, GEPA on the assistant prompt), the dev-only case split, the accept rule for the judge (TPR and TNR no worse than the current ones), the candidate file `.ignore/assistant-prompt-candidate.txt`; `dspy` is imported only here | depends on `evals` and `conversation`, nothing depends on it; the result enters the runtime only as committed text (`CRITERION_PROMPTS` and `FEW_SHOT_EXAMPLES` in `evals/judge.py`, `SYSTEM_PROMPT` in `conversation/prompt.py`) |
| `evals` | `run_round(selection, runs=3, judge="sync" \| "batch" \| "none", budget_usd=5) -> RoundResult`; pure check functions | Inspect AI task (YAML case loader, multi-turn solver calling `Assistant`, scorer with the pure checks and the judge), estimate and confirmation, round budget stop, `cases.csv` export | judge as a pure function over an injected client (Anthropic SDK sync and Message Batches API; Ollama client later), called by the scorer and by post-steps (batch, `--rejudge`, `--collect`, the label tool); `Assistant` reused as-is |

Tools que o modelo vê: `search_policies(query)`, `get_order(order_number)` sem CPF, `search_catalog(query, species?, category?)` e `escalate_to_human(reason)`, com motivo em enum. Repeated Failure é decidido pelo backend, não pelo modelo. Um segundo CPF na mesma conversa é recusado: o Session Customer continua o primeiro, e outro cliente exige conversa nova.

Defesa contra prompt injection, nesta ordem de peso:

1. Capacidade: nenhuma tool concede cupom, desconto ou dado alheio, e todas são só de leitura.
2. Identidade vem do estado da sessão, nunca de argumento.
3. Regra no prompt de sistema.
4. Uma heurística no guard de entrada marca a suspeita no trace, sem bloquear.

O SKU do kit precisa de um padrão reconhecível (prefixo fixo) para que o guard de saída o encontre. Esse é um requisito para quem gerar o kit.

### Motivo

Cada módulo esconde uma decisão que pode mudar: provider, motor de busca, formato da API de pedidos, destino do trace, otimizador de prompt. O `fake` do gateway é o seam que permite o TDD e o piso de 80% sem modelo, que a [fluxo-desenvolvimento.md](directives/fluxo-desenvolvimento.md) exige. Identidade no estado e allowlist no `OrderView` aplicam o [ADR 0001](adr/0001-order-access-by-cpf-and-order-number.md) por construção, não por prompt. O limite de iterações e a validação dos argumentos contêm as falhas típicas de tool calling num modelo pequeno, que a documentação do Ollama não trata. O registro de tools é o seam da alteração ao vivo.

### Alternativas

- `create_agent` ou ReAct pré-pronto do LangChain: rejeitada, porque esconde onde injetar a identidade e os guards.
- SDK da Anthropic direto no grafo: rejeitada, porque exigiria traduzir mensagens à mão para o Ollama. O SDK direto fica só no juiz, por causa do Batch.
- Servidor HTTP para a API de pedidos: rejeitada por ora. O desafio aceita função com o mesmo contrato, e o port permite trocar depois.
- Guard que bloqueia injection por regex: rejeitada, porque é frágil e bloqueia cliente legítimo.
- DSPy no runtime: rejeitada em 2026-11-03, ver [ADR 0004](adr/0004-dspy-offline-optimizer.md). Um nó DSPy não compartilha tools nem estado com o grafo e traria um segundo cliente de modelo. O DSPy fica no módulo `optimize`, offline, e o resultado entra como texto commitado.

`origin: owner`. Premissas: `pedido-so-do-dono`, `nao-inventa`, `simples-e-medido`.

## 5. Harness de eval

### Escolha

- Caso como dado: um arquivo YAML por caso.
  - Os casos normais ficam em `evals/cases/`. Os reservados ficam em `evals/held_out/cases/`, o caminho que o hook já bloqueia.
  - Campos: `id`, `suite` (`capability` | `regression`; o reservado se define pela pasta), `rule` (opcional: `RN01`..`RN04`), `today` (data fixa do relógio), `turns` (lista de mensagens do cliente), `checks` (asserts determinísticos), `expected_doc` (opcional) e `judge` (lista de critérios, opcional).
  - Criar um caso ao vivo é criar um arquivo.
- Checks determinísticos são funções puras, testadas no pytest sem modelo, com tipos fixos:
  - fonte citada;
  - SKU existente;
  - número do pedido mencionado;
  - nenhum dado de outro cliente;
  - tool chamada ou não chamada;
  - escalonado com o motivo X;
  - termos obrigatórios e proibidos;
  - valor esperado, para RN01 a RN04.
- Juiz: um critério por chamada (tom, julgamento de escalonamento, acerto do "não sei"), com veredito `pass`, `fail` ou `unable`.
- Motor: Inspect AI (versão fixada no `uv.lock`, porque é pré-1.0 e lança quase todo dia), com uma casca fina nossa, `pet-helper eval [--suite ...] [--case ...] [--judge sync|batch|none] [--runs N] [--held-out]`, mais `--rejudge <rodada>` e `--collect <rodada>`.
  1. Carrega os YAML como amostras do Inspect.
  2. Mostra a estimativa de tokens e custo, pela média da última rodada salva ou por um valor padrão por turno, e pede confirmação.
  3. Roda cada caso 3 vezes (`--epochs 3`, com reducer que só passa em 3 de 3), com concorrência (8 na Anthropic, 1 no Ollama) e fallback desligado. Um erro de infraestrutura conta como `error`, nunca como `pass`: o Inspect tiraria a exceção do denominador, então a casca a registra explicitamente.
  4. Soma o custo real de cada chamada e para ao passar de US$ 5, salvando o que já tiver. O `--cost-limit` do Inspect é por amostra e não substitui esse freio.
  - O juiz é um scorer nosso; `unable` vira `Score.unscored()`. O `inspect view` é o visualizador local das conversas.
- Modo do juiz:
  - `sync` é o padrão, usado na apresentação ao vivo e em suíte pequena.
  - `batch` serve para as rodadas que contam. O lote usa a Message Batches API direto, fora do scorer do Inspect, e o teste real dessa API é feito pelo dono no passo 1 dos passos do dono; se falhar, o juiz fica só síncrono. O runner envia o lote depois das conversas e espera o resultado. Se o lote não terminar no prazo de espera, salva o id em `round.json`, e `pet-helper eval --collect <rodada>` termina a rodada depois.
  - `none` roda só as conversas, sem juiz. A baseline o usa, porque o prompt do juiz ainda não está congelado; os casos com critério do juiz ficam `judge_pending` até o re-julgamento. Na unidade `market-tools-adoption` (2026-11-03), as três rodadas de comparação (a baseline P1a, a paridade P1b e a aceitação do prompt do assistente P4) rodam com `--judge none`. Misturar modos criaria regressões `judge:*` falsas, porque um `fail` ou `unable` do juiz sobrepõe um `pass` determinístico, e um juiz sem validação não decide aceitação. A paridade e o aceite do assistente decidem só pelos checks determinísticos; tom, julgamento de escalonamento e acerto do "não sei" ficam pendentes, e o relatório declara juiz `none`, sem hash de juiz.
  - `--rejudge <rodada>` aplica o juiz congelado às transcrições salvas de uma rodada e grava o `judge.jsonl`, com estimativa, confirmação e teto de US$ 5 próprios.
- Resultados, versionados no repositório (entregável 4), em `evals/results/<round_id>/`:
  - `round.json`: SHA do git, modelos, hash do prompt do assistente e do juiz, relógio, custo total.
  - `results.json`: o log do Inspect, por caso e por execução, a transcrição mascarada, os checks, a crítica e o veredito do juiz, tokens, custo, latência, modelo e se o `expected_doc` foi recuperado.
  - `cases.csv`: exportado do `samples_df()` do Inspect, uma linha por caso, com `case_id`, `suite`, `k_of_3`, `first_failure_note`, `category`, `human_label`, `cost_usd`. O CSV nunca é editado à mão: o runner gera as linhas, o critic preenche `first_failure_note` e `category`, e o agente grava em `human_label` o que o dono confirmar na conversa. A suíte vem do campo `suite` do YAML.
  - `traces/`.
  - A contagem por categoria é um script sobre o CSV.
- Casos reservados: só rodam com `--held-out` e `PET_HELPER_HELD_OUT=1`. Resultados e traces vão para `evals/held_out/results/<round_id>/`, sob o bloqueio do hook. A saída no terminal mostra só o id e o k/3, nunca o conteúdo.
- Juiz no caminho sem chave: o mesmo prompt, no `llama3.1:8b`. Só o juiz Sonnet é validado nesta unidade, nos termos da [loop-melhoria.md](directives/loop-melhoria.md): cerca de 50 rótulos por critério, divisão por caso em dev e teste e piso de TPR e TNR escrito pelo dono antes de ler o teste. O juiz Llama não é validado, e o README o declara assim.
- Limitações declaradas do juiz:
  - não há controle de temperatura no Sonnet, porque a API rejeita;
  - o Sonnet é da mesma família do Haiku avaliado, então há risco de autopreferência;
  - o Llama 8B é um juiz pequeno, com concordância esperada menor;
  - ambos mitigados pela medição contra os rótulos e pelas 3 execuções.
- Caminho sem chave na eval: o Llama em CPU leva horas na rodada completa. O avaliador sem chave lê os resultados versionados ou roda uma fumaça com `pet-helper eval --runs 1`: a saída leva `"smoke": true`, não conta como rodada, nunca decide aceitação e não grava em `evals/results/`.

### Motivo

Pass^3, a parada por custo no meio da rodada, a confirmação antes de rodar e o juiz em lote acontecem fora de um teste isolado. No pytest viraria plugin e captura de stdin. O Inspect já traz épocas com pass^k, logs locais sem conta, o dataframe para a análise de erro e o visualizador; sobra para nós só o que é deste projeto (casos YAML multi-turno, juiz com `unable`, freio de US$ 5 por rodada), cerca de 250 a 350 linhas. O pytest continua onde é forte: checks e grafo sem modelo, que é a sugestão do desafio ("pytest com asserts determinísticos"). Guardar resultados e traces reservados sob `evals/held_out/` fecha um vazamento que o hook sozinho não cobria. O relógio fixo por caso mantém o RN01 calculável em qualquer dia de execução.

### Alternativas

- Pytest como runner (sugestão do dono): rejeitada pelos motivos acima. Ter dois caminhos de execução seria duplicação.
- Uma pasta por suíte: rejeitada, porque mover caso viraria mover arquivo, e a diretriz pede uma mudança de campo à mão.
- Juiz sempre em lote: rejeitada, porque a maioria dos lotes termina em até 1 hora, o que não cabe nos 30 minutos ao vivo.
- Runner próprio inteiro: rejeitada na revisão de 29/10/2026, porque refaz épocas, logs e visualizador que o Inspect entrega.
- promptfoo: rejeitada. Tem casos YAML nativos e visualizador, mas traz Node, não tem `unable` nem lote no juiz e o pass^3 sai de pós-processamento.
- Phoenix local: rejeitada. Exige o servidor de pé a cada rodada, cria um segundo destino para as conversas fora da máscara única, guarda os resultados no banco dele e o servidor é Elastic License 2.0.
- LangSmith como motor da eval: rejeitada. O plano gratuito exige conta e guarda os dados na nuvem por 14 dias, e o self-hosted é só Enterprise. A eval continua no Inspect AI, sem `evaluate()` nem datasets da LangSmith, e roda sem conta. Em 2026-11-03 a LangSmith foi adotada só como destino opcional dos traces, ligado por `LANGSMITH_API_KEY` ([ADR 0003](adr/0003-otel-langsmith.md)); a rejeição de 2026-10-29 vale para a eval.

`origin: owner`. Premissas: `comportamento-nasce-como-eval`, `modelo-real-so-a-mao`, `simples-e-medido`.

## 6. Fatos calculados em código, regra aplicada pelo modelo

### Escolha

As regras de negócio (RN01 a RN04 e as inventadas no kit) vivem nos documentos de política. As tools devolvem fatos já calculados: status, data de entrega e dias desde a entrega pelo `Clock`, região, total, meio de pagamento, categoria e embalagem aberta dos itens. Nenhuma tool dá veredito ("pode devolver"). O modelo aplica a regra da fonte citada, e os asserts da eval conferem o resultado.

### Motivo

A alteração ao vivo traz uma regra nova. Com regra em documento, ela entra como documento mais caso de eval, sem código de regra. Com fatos calculados, o modelo fraco não erra conta de data. Se a eval mostrar falha recorrente numa regra, o loop pode mover só essa regra para uma tool ([ADR 0002](adr/0002-business-rules-in-policies-facts-from-tools.md)).

### Alternativas

- Motor de regras em código, com tools de elegibilidade: rejeitada como padrão, porque toda regra nova exigiria código ao vivo.
- Modelo calculando tudo a partir de datas brutas: rejeitada, porque erro de aritmética de datas é previsível num modelo pequeno.

`origin: simulated-approved`. Premissas: `qualidade-vem-do-sistema`, `nao-inventa`, `aderencia-ao-desafio`.
