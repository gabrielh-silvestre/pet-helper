# Arquitetura

Linha de base da arquitetura do pet-helper, decidida pelo critic da cadeira em 28/10/2026 numa rodada de bootstrap sem spec nem plano: as perguntas vieram direto do dono. Cada unidade de trabalho seguinte registra o que acrescenta em `docs/specs/<slug>/architecture.md` e aponta para esta. Toda decisão nasce com `origin: simulated` e passa a `simulated-approved` com o aval do dono; as desta linha de base foram aprovadas pelo dono em 28/10/2026. Em 29/10/2026 o dono revisou o que já existia pronto na comunidade e trocou peças próprias por ferramentas curadas: `genai-prices` e callback do LangChain no tracing, Presidio na máscara e Inspect AI na eval (decisões 2, 4 e 5, `origin: owner`). Os fatos de modelo e preço foram conferidos em 28/10/2026 em `platform.claude.com/docs/en/about-claude/pricing` e `.../build-with-claude/prompt-caching`; os do Ollama, em `docs.ollama.com` e `hub.docker.com/r/ollama/ollama`.

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
  - `PET_HELPER_ASSISTANT_MODEL` e `PET_HELPER_JUDGE_MODEL`.
  - `PET_HELPER_FALLBACK` (`ollama` | `none`).
  - `OLLAMA_BASE_URL`.
  - `PET_HELPER_EFFORT`.
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

### Escolha

- Trace próprio em JSONL, um arquivo por sessão: `traces/<AAAA-MM-DD>/<session_id>.jsonl` no chat, e dentro da pasta da rodada na eval.
- Cada linha traz `ts`, `session_id`, `turn` e `event`. Os eventos são:
  - `turn.start`: texto do usuário, mascarado.
  - `guard.input`: CPF detectado, suspeita de injection.
  - `retrieval`: query, hits com `source` e `score`.
  - `model.call`: provider, modelo, `ttft_ms`, `duration_ms`, tokens de entrada e saída, `cache_read`, `cache_write`, `stop_reason`, `fallback`.
  - `tool.call`: nome, argumentos, status, erro de validação e motivo interno. No `get_order`, o motivo é `not_found` ou `other_customer` ([ADR 0001](adr/0001-order-access-by-cpf-and-order-number.md)).
  - `guard.output`: SKUs inválidos, fonte não recuperada.
  - `escalation`: motivo.
  - `turn.end`: resposta mascarada, fontes citadas.
- A máscara é aplicada num único ponto, o sink do trace, por `Guard.mask`:
  - A detecção usa o Presidio (`presidio-analyzer` e `presidio-anonymizer`) só com regex, no motor `no_op`, sem modelo de NLP: e-mail e telefone pelos reconhecedores nativos (região BR) e CPF por um `PatternRecognizer` nosso, aplicado antes do telefone, porque 11 dígitos colidem com ele.
  - CPF, com ou sem formatação, vira o pseudônimo estável `cpf#<8 hex>` (HMAC-SHA256 com o sal de `PET_HELPER_TRACE_SALT`; sem a variável, um sal aleatório por processo). Todo número com formato de CPF é mascarado, mesmo com dígitos verificadores inválidos; a validação só decide se ele vincula o Session Customer.
  - E-mail e telefone são substituídos.
- O pedido entra no trace e no modelo como `OrderView`, com uma allowlist de campos: número, status, itens, total, região, meio de pagamento, data de entrega e dias desde a entrega. Nome e endereço nunca saem do módulo de pedidos.
- O CPF digitado pelo cliente é trocado por `[CPF informado]` antes de chegar ao modelo. O backend guarda o CPF no estado da sessão como Session Customer.
- Um teste sem modelo varre os traces de exemplo atrás de padrões de CPF e e-mail.
- O histórico da conversa nunca é reescrito depois de enviado: a máscara acontece antes do envio e no sink. Reescrever o histórico invalidaria os blocos de thinking e o cache.
- Os eventos `model.call` e `tool.call` vêm de um `BaseCallbackHandler` do LangChain (TTFT, duração, `usage_metadata`, erro de tool). O `input_tokens` gravado é o do LangChain, que já soma cache read e write. Os eventos do domínio são emitidos pelo próprio código.
- O comando `pet-helper metrics <pasta>` lê os JSONL, sem chamar modelo, e imprime uma tabela por provider e modelo:
  - chamadas;
  - TTFT p50/p95;
  - TPS mediano (tokens de saída sobre o tempo depois do primeiro token);
  - tokens de entrada, saída, cache read e cache write;
  - custo em US$, pela `genai-prices` (a versão da lib vai no relatório como fonte e data dos preços; o Ollama custa zero);
  - taxa de acerto de cache: `cache_read / input_tokens`;
  - chamadas e taxa de erro por tool;
  - quantidade e taxa de fallback;
  - escalonamentos por motivo;
  - custo e latência por conversa, que alimentam o README.
- O prompt cache usa dois breakpoints: um no fim do prompt de sistema estático (as tools são renderizadas antes dele, então entram junto) e um na última mensagem. O prompt de sistema fica byte a byte estável: sem data nem id. O mínimo cacheável do Haiku 5.5 é de 512 tokens, e o prefixo estático passa disso.
- O gateway sempre consome o modelo em streaming, para medir TTFT e TPS. A CLI mostra a resposta só depois do guard de saída.

### Motivo

O desafio pede logs que expliquem por que o bot respondeu algo e aceita JSONL. Phoenix e LangSmith exigiriam um processo ou uma conta a mais para quem avalia. Mascarar num só ponto torna `dado-pessoal-mascarado` verificável por teste. A allowlist e o CPF fora do modelo reduzem o dado pessoal na origem: o modelo não tem como repetir o que não viu. Mostrar a resposta só depois do guard troca latência percebida por correção. Em produção, o caminho seria fazer streaming por sentença já checada.

### Alternativas

- Phoenix ou LangSmith: rejeitadas pela sugestão do dono e pelo custo de setup.
- OpenTelemetry com OpenInference: rejeitada. Grava em arquivo sem backend, mas não mede TTFT, não conhece os eventos do domínio, gera cerca de 15 spans por turno e só deixa mascarar por API privada; o código próprio não diminuiria. É o caminho para produção: trocar o destino mexe só no módulo `trace`.
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
- Service: `conversation`, `guard`, `evals`.
- Model: `gateway`, `knowledge`, `orders`, `catalog`.
- Transversal: `trace`.

Cada módulo é um módulo profundo, documentado pela interface. Os nomes internos e os arquivos abaixo do módulo ficam com o executor.

| Module | Interface | Hides | Seams / adapters |
|---|---|---|---|
| `cli` | `chat`, `eval`, `metrics` commands | argument parsing, terminal I/O | none; thin |
| `conversation` | `Assistant.turn(session_id: str, text: str) -> Reply(text, sources, escalation)` | LangGraph `StateGraph` (input guard -> agent <-> tools -> output guard), `InMemorySaver` keyed by `thread_id = session_id`, system prompt, tool registry, tool-loop iteration cap (overflow = Repeated Failure), Session Customer binding, Repeated Failure counter, Escalation state, one regeneration on output-guard failure | tool registry: adding a tool for the live change = one function + one registration; tools get identity from graph state, never from arguments; tool arguments validated against their schema, a validation error returns a tool error result |
| `guard` | `inspect_input(text) -> InputVerdict(model_text, cpf, injection_suspected)`; `check_reply(text, retrieved_sources) -> OutputVerdict(invalid_skus, unretrieved_sources)`; `mask(text) -> str` | Presidio analyzer and anonymizer (regex-only `no_op` engine), own CPF recognizer, pseudonymisation, accent-insensitive injection heuristics, SKU pattern | uses `catalog.exists` |
| `gateway` | `ModelGateway.complete(messages, tools) -> ModelResult(message, provider, model, usage, ttft_ms, fell_back, refused)` | provider selection by env, Anthropic/Ollama clients, effort/thinking settings, `cache_control` placement, per-provider sampling (none on Anthropic, temperature 0 on Ollama), retry and fallback policy, cross-provider message normalisation (text-only history to Ollama), refusal handling, `model.call` trace | adapters: `anthropic` (langchain-anthropic), `ollama` (langchain-ollama, `num_ctx` set), `fake` (scripted replies: the seam that lets the whole graph run in pytest without a model) |
| `knowledge` | `PolicyIndex.load(dir) -> PolicyIndex`; `.search(query, k=2) -> list[PolicyHit(source, title, text, score)]` | tokenisation, accent folding, stemming, stopwords, bm25s | none |
| `orders` | `OrderService.lookup(customer: Cpf \| None, order_number: str) -> Found(OrderView) \| NotFound \| NeedsIdentification \| Unavailable` | collapse of 404 and other-customer refusal into `NotFound` (ADR 0001), one retry on timeout, `days_since_delivery` via `Clock`, PII allowlist of `OrderView`, internal reason to trace | port `OrderApi.get(order_number, customer_cpf) -> ApiResponse(status, body)` with adapter `MockOrderApi` over `kit/orders.json` (200/404/403/503/timeout; failure by a fixed order number or `PET_HELPER_ORDER_API_FAILURE`); `Clock.today()` from `PET_HELPER_TODAY` or the system date |
| `catalog` | `Catalog.search(text, species=None, category=None) -> list[Product(sku, name, ..., in_stock)]`; `Catalog.exists(sku) -> bool` | CSV loading, similar-name matching | none |
| `trace` | `Tracer.event(kind, **fields)`; `summarize(paths) -> MetricsTable` | JSONL layout, LangChain callback handler for model and tool calls, masking at the sink, cost through `genai-prices` | sink writes under the run's directory (held-out runs under `evals/held_out/`) |
| `evals` | `run_round(selection, runs=3, judge="sync" \| "batch", budget_usd=5) -> RoundResult`; pure check functions | Inspect AI task (YAML case loader, multi-turn solver calling `Assistant`, scorer with the pure checks and the judge), estimate and confirmation, round budget stop, `cases.csv` export | judge through an Inspect model role (Anthropic sync or batch, Ollama sync); `Assistant` reused as-is |

Tools que o modelo vê: `search_policies(query)`, `get_order(order_number)` sem CPF, `search_catalog(query, species?, category?)` e `escalate_to_human(reason)`, com motivo em enum. Repeated Failure é decidido pelo backend, não pelo modelo. Um segundo CPF na mesma conversa é recusado: o Session Customer continua o primeiro, e outro cliente exige conversa nova.

Defesa contra prompt injection, nesta ordem de peso:

1. Capacidade: nenhuma tool concede cupom, desconto ou dado alheio, e todas são só de leitura.
2. Identidade vem do estado da sessão, nunca de argumento.
3. Regra no prompt de sistema.
4. Uma heurística no guard de entrada marca a suspeita no trace, sem bloquear.

O SKU do kit precisa de um padrão reconhecível (prefixo fixo) para que o guard de saída o encontre. Esse é um requisito para quem gerar o kit.

### Motivo

Cada módulo esconde uma decisão que pode mudar: provider, motor de busca, formato da API de pedidos, layout do trace. O `fake` do gateway é o seam que permite o TDD e o piso de 80% sem modelo, que a [fluxo-desenvolvimento.md](directives/fluxo-desenvolvimento.md) exige. Identidade no estado e allowlist no `OrderView` aplicam o [ADR 0001](adr/0001-order-access-by-cpf-and-order-number.md) por construção, não por prompt. O limite de iterações e a validação dos argumentos contêm as falhas típicas de tool calling num modelo pequeno, que a documentação do Ollama não trata. O registro de tools é o seam da alteração ao vivo.

### Alternativas

- `create_agent` ou ReAct pré-pronto do LangChain: rejeitada, porque esconde onde injetar a identidade e os guards.
- SDK da Anthropic direto no grafo: rejeitada, porque exigiria traduzir mensagens à mão para o Ollama. O SDK direto fica só no juiz, por causa do Batch.
- Servidor HTTP para a API de pedidos: rejeitada por ora. O desafio aceita função com o mesmo contrato, e o port permite trocar depois.
- Guard que bloqueia injection por regex: rejeitada, porque é frágil e bloqueia cliente legítimo.

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
- Motor: Inspect AI (versão fixada no `uv.lock`, porque é pré-1.0 e lança quase todo dia), com uma casca fina nossa, `pet-helper eval [--suite ...] [--case ...] [--judge sync|batch] [--held-out]`.
  1. Carrega os YAML como amostras do Inspect.
  2. Mostra a estimativa de tokens e custo, pela média da última rodada salva ou por um valor padrão por turno, e pede confirmação.
  3. Roda cada caso 3 vezes (`--epochs 3`, com reducer que só passa em 3 de 3), com concorrência (8 na Anthropic, 1 no Ollama) e fallback desligado. Um erro de infraestrutura conta como `error`, nunca como `pass`: o Inspect tiraria a exceção do denominador, então a casca a registra explicitamente.
  4. Soma o custo real de cada chamada e para ao passar de US$ 5, salvando o que já tiver. O `--cost-limit` do Inspect é por amostra e não substitui esse freio.
  - O juiz é um scorer nosso; `unable` vira `Score.unscored()`. O `inspect view` é o visualizador local das conversas.
- Modo do juiz:
  - `sync` é o padrão, usado na apresentação ao vivo e em suíte pequena.
  - `batch` serve para as rodadas que contam. A primeira tarefa da unidade de eval é um teste prático do lote no scorer do Inspect, que a documentação não confirma; se falhar, o juiz fica só síncrono. O runner envia o lote depois das conversas e espera o resultado. Se o lote não terminar no prazo de espera, salva o id em `round.json`, e `pet-helper eval --collect <rodada>` termina a rodada depois.
- Resultados, versionados no repositório (entregável 4), em `evals/results/<round_id>/`:
  - `round.json`: SHA do git, modelos, hash do prompt do assistente e do juiz, relógio, custo total.
  - `results.json`: o log do Inspect, por caso e por execução, a transcrição mascarada, os checks, a crítica e o veredito do juiz, tokens, custo, latência, modelo e se o `expected_doc` foi recuperado.
  - `cases.csv`: exportado do `samples_df()` do Inspect, uma linha por caso, com `case_id`, `suite`, `k_of_3`, `first_failure_note`, `category`, `human_label`, `cost_usd`. O CSV nunca é editado à mão: o runner gera as linhas, o critic preenche `first_failure_note` e `category`, e o agente grava em `human_label` o que o dono confirmar na conversa. A suíte vem do campo `suite` do YAML.
  - `traces/`.
  - A contagem por categoria é um script sobre o CSV.
- Casos reservados: só rodam com `--held-out` e `PET_HELPER_HELD_OUT=1`. Resultados e traces vão para `evals/held_out/results/<round_id>/`, sob o bloqueio do hook. A saída no terminal mostra só o id e o k/3, nunca o conteúdo.
- Juiz no caminho sem chave: o mesmo prompt, no `llama3.1:8b`. Os 10 a 15 rótulos do dono medem TPR e TNR dos dois juízes, e o README declara os dois números.
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
- LangSmith: rejeitada. O plano gratuito exige conta e guarda os dados na nuvem por 14 dias; o self-hosted é só Enterprise.

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
