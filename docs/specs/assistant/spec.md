# Assistente de atendimento

Status: aprovado em 30/10/2026. A entrevista foi em 30/10/2026 e terminou com ambiguidade de 16%. A versão completa, com transcrição e pontuação, fica em `.omc/specs/deep-interview-assistant.md`.

## Objetivo

É o assistente que a seção "Assistente" da [requisitos.md](../../requisitos.md) pede, montado sobre o esqueleto e o kit que já existem e seguindo as escolhas da [architecture.md](../../architecture.md) (§1, §3, §4 e §6). O `pet-helper chat` deixa de falar com o gateway falso e passa a falar com um modelo real, a Anthropic por padrão e o Ollama quando não houver chave. Ele responde pelas políticas citando a fonte, consulta um pedido por tool só para quem informou o CPF do dono e o número do pedido, recomenda apenas SKUs do catálogo e mantém o contexto entre turnos. Além disso, escala para humano em pergunta de saúde animal, sentimento negativo forte, falha repetida ou pedido explícito, encerrando a sessão, e resiste a prompt injection básico.
Cada comportamento é provado na mecânica por pytest com o gateway falso e conferido à mão num smoke com o Haiku real.

## Decisões da entrevista

- A unidade tem 4 componentes: gateway real, núcleo da conversa, tools de dados e guard. O runner de eval, as métricas e a observabilidade (trace JSONL) ficam para a próxima unidade.
- Sem o runner, a prova de pronto é o pytest com o `FakeGateway` roteirizado cobrindo a mecânica de cada comportamento, mais um smoke manual feito pelo dono com o Haiku real em 5 casos: `rn01-regret-window-expired-day-12`, `multi-turn-other-order`, `adv-other-customer-order`, `esc-animal-health` e `rec-out-of-stock`. O custo fica abaixo de US$ 0,05 e as transcrições vão anexadas ao PR. O 3 de 3 fica com a unidade de eval.
- Depois do escalonamento a sessão termina. A resposta anuncia a transferência em linguagem de cliente, sem o motivo técnico, o `Reply.escalation` leva o motivo e o `chat` mostra um marcador e encerra. Dessa forma o GLOSSARY troca "para de responder sobre aquele assunto" por "a conversa termina", isso porque rastrear assunto num modelo fraco é frágil.
- O Ollama não entra no smoke. O adapter dele continua na unidade, provado só por teste de contrato, sem chamada real.
- O dono perguntou sobre modelos free do OpenRouter. Existem 16 com tool calling (entre eles `google/gemma-4-31b-it:free`), porém a cota diária, a conta obrigatória e o catálogo que muda sem aviso mantêm o Ollama como o caminho sem chave paga.
- Repeated Failure era o único comportamento sem caso entre os 30, logo a unidade cria `evals/cases/esc-repeated-failure.yaml`, conforme a premissa `comportamento-nasce-como-eval`.

## O que entra

- Gateway: adapters Anthropic e Ollama, fallback só em erro de transporte, escolha do provedor por variável de ambiente e o compose do Ollama.
- Conversa: grafo LangGraph com guard de entrada, agente, tools e guard de saída, memória por `session_id`, Session Customer, escalonamento e Repeated Failure.
- Tools de dados: busca BM25 nas políticas (`knowledge`), `OrderService` sobre o `MockOrderApi` (`orders`) e busca no catálogo (`catalog`).
- Guard: inspeção da entrada, checagem da resposta e máscara.
- O caso `esc-repeated-failure` e a atualização do verbete Escalation no GLOSSARY.

Lembrando que algumas decisões já estão tomadas e valem aqui também: as regras ficam nas políticas e as tools devolvem fatos ([ADR 0002](../../adr/0002-business-rules-in-policies-facts-from-tools.md)), e o acesso ao pedido segue a [ADR 0001](../../adr/0001-order-access-by-cpf-and-order-number.md), com a identidade injetada pelo backend e o `NOT_FOUND` único. Nenhum teste automático chama o modelo real. Código e identificadores ficam em inglês, já o texto voltado ao cliente fica em pt-BR.

## O que fica de fora

- O runner de eval (`pet-helper eval`, task do Inspect, juiz, `evals/results/`, estimativa e teto de custo), o comando `metrics` e os casos reservados.
- O trace JSONL e o callback handler do LangChain. O `guard.mask` sai agora, porém o destino dele no trace chega com a unidade de observabilidade.
- Ollama no smoke, OpenRouter ou qualquer terceiro provedor.
- Rate limit, filas e cache semântico, que ficam para a conversa sobre produção.
- Continuar a conversa depois do escalonamento.

## Critério de aceite

Gateway

1. O adapter Anthropic usa `ChatAnthropic` com o modelo de `PET_HELPER_ASSISTANT_MODEL` (padrão `claude-haiku-5-5`), `max_retries=1`, o effort de `PET_HELPER_EFFORT` (padrão `low`) em `output_config.effort` e `max_tokens` folgado. O payload montado não leva `temperature`, `top_p` nem `top_k`, conferido sem rede.
2. O adapter Ollama usa `ChatOllama` com `num_ctx=8192`, `temperature=0` e `OLLAMA_BASE_URL`, mandando o histórico só como texto.
3. A escolha do provedor segue o contrato: `PET_HELPER_PROVIDER` manda, e sem ele vale `anthropic` quando existe `ANTHROPIC_API_KEY`, senão `ollama`. O `pet-helper chat` usa essa escolha no lugar do fake.
4. O fallback da Anthropic para o Ollama acontece só em erro de transporte (conexão, timeout, 429, 5xx e o 529 `OverloadedError`), depois do retry do SDK. Nunca em outro 4xx nem em `stop_reason: refusal`, sendo a recusa uma falha irrecuperável (Repeated Failure). `PET_HELPER_FALLBACK=none` desliga o fallback.
5. O compose sobe `ollama/ollama:0.40.12` fixado, com um serviço de inicialização que baixa o tag configurado antes do app, e o README documenta o setup sem chave.

Conversa

6. O grafo segue guard de entrada, agente e tools em loop, e guard de saída, com memória por `session_id` (`InMemorySaver`). O segundo turno enxerga o primeiro ("e o outro pedido?", "na verdade é para gato").
7. O CPF do Session Customer capturado pelo guard de entrada fica no estado do grafo e chega às tools de pedido por injeção. Um CPF que o modelo coloque nos argumentos da tool nunca é usado.
8. Um erro de validação de argumento da tool volta ao modelo como resultado de erro, nunca como exceção saindo do `turn`.
9. Estourar o limite do loop de tools vira escalonamento por Repeated Failure.
10. A mesma etapa (consulta de pedido, identificação do Customer, número do pedido) falhando três vezes seguidas escala por Repeated Failure. O "não sei" sobre um Uncovered Topic não conta.
11. O `Reply.escalation` leva exatamente um Escalation Reason pela prioridade (Animal Health Question, Strong Negative Sentiment, Repeated Failure, Explicit Request). A resposta anuncia a transferência sem o motivo técnico e nunca dá diagnóstico, o `pet-helper chat` mostra um marcador com o motivo e encerra a sessão, e um `turn` posterior na mesma sessão nunca chama o modelo.
12. O `Reply.sources` lista as políticas que a resposta cita.
13. Uma falha do guard de saída (SKU inventado ou fonte não recuperada) gera uma regeneração, como na arquitetura.

Tools de dados

14. A busca nas políticas usa bm25s sobre `kit/policies/*.md` com stopwords e stemmer Snowball em português e remoção de acento no documento e na consulta ("devolucao" acha `troca-e-devolucao`), com `k=2`. Sem nenhum termo em comum não há resultado, e o assistente diz que não sabe.
15. A consulta de pedido passa pelo `OrderService` sobre o `MockOrderApi`: o 404 e o pedido de outro cliente viram um único `NotFound`, sem CPF vem `NeedsIdentification`, o timeout tem um retry e depois vira `Unavailable`, o `days_since_delivery` vem do `Clock` (`PET_HELPER_TODAY`) e só os campos permitidos do `OrderView` chegam ao modelo.
16. A busca no catálogo filtra por espécie e categoria e acha nomes parecidos, devolve só SKUs existentes e marca o que está sem estoque.
17. Registrar uma tool nova custa uma função e um registro, coberto por um teste que registra uma tool de exemplo.

Guard

18. O `inspect_input` extrai um CPF com dígitos verificadores válidos, entrega ao modelo o texto mascarado e sinaliza tentativa de injection sem depender de acento.
19. O `check_reply` aponta SKUs fora do catálogo e fontes citadas que não foram recuperadas.
20. O `mask` mascara CPF e e-mail em qualquer texto.

Transversal

21. O caso `evals/cases/esc-repeated-failure.yaml` carrega pelo `load_cases`.
22. O verbete Escalation do GLOSSARY diz que a conversa termina depois da transferência.
23. Cada comportamento da seção "Assistente" da requisitos.md, menos a máscara nos logs (adiada com o trace), tem um pytest com o gateway falso exercitando a mecânica. `pytest -q --cov`, prek e deptry passam.
24. O dono roda o smoke manual com o Haiku real nos 5 casos pelo `pet-helper chat` e as transcrições vão anexadas ao PR.

## Comportamento e caso de eval

| Comportamento | Casos |
| --- | --- |
| Responde pelas políticas citando a fonte | `policy-*`, `rn01-*` a `rn04-*` |
| Diz que não sabe | `dk-extended-warranty`, `dk-loyalty-program` |
| Consulta pedido e pede o número | `missing-order-number`, `own-nonexistent-order` |
| Falha da API de pedidos | `api-failures` |
| Contexto em vários turnos | `multi-turn-other-order`, `multi-turn-species-switch` |
| Recomenda sem inventar SKU | `rec-out-of-stock`, `rec-similar-names` |
| Escalonamento | `esc-animal-health`, `esc-explicit-request`, `esc-strong-negative-sentiment`, `esc-repeated-failure` (novo) |
| Prompt injection | `adv-coupon-injection` |
| Não vaza dado de outro cliente | `adv-other-customer-data`, `adv-other-customer-order` |
| Máscara nos logs | adiado com o trace |

## Ferramentas existentes

A pesquisa foi direcionada, já que toda capacidade desta unidade tem biblioteca escolhida na [architecture.md](../../architecture.md). Hoje estão instalados `langchain-core` 1.7.1, `langgraph` 1.2.18 e `langgraph-prebuilt` 1.1.1. Entram `langchain-anthropic` 1.7.7, `langchain-ollama` 1.1.0, `bm25s` 0.3.16 com `PyStemmer` 3.1.0 e `presidio-analyzer`/`presidio-anonymizer` 2.2.366.

| Capacidade | O que a biblioteca já entrega | O que ainda escrevemos |
| --- | --- | --- |
| Loop de tools | `ToolNode(handle_tool_errors=...)` transforma `ToolInvocationError` em `ToolMessage` de erro; `tools_condition` com `{"__end__": "output_guard"}` para não pular o guard | A montagem do grafo; as tools devolvem `Unavailable` e afins como valor |
| Injeção de identidade | `InjectedState("session_cpf")` tira o argumento do schema do modelo | O teste de que o CPF mandado pelo modelo é ignorado |
| Limite do loop | `recursion_limit` com `GraphRecursionError`, contado em superstep (uns 2 por ciclo agente e tools) | O mapeamento para Repeated Failure; contador no estado ou `2*N` mais os passos fixos |
| Memória | `InMemorySaver` com `thread_id = session_id` | Nada |
| Agente pronto | `create_react_agent` está deprecado (o sucessor é `langchain.agents.create_agent`), e o grafo à mão segue como a arquitetura decidiu | Nada |
| Fallback | `with_fallbacks([ollama], exceptions_to_handle=(...))` sobre o resultado do `bind_tools`, listando `APIConnectionError`, `RateLimitError`, `InternalServerError`, `OverloadedError`, `ServiceUnavailableError` e `DeadlineExceededError`; o `OverloadedError` não é subclasse de `InternalServerError` | O histórico só texto para o Ollama e as marcas `fell_back`/`refused` |
| Parâmetros da Anthropic | `max_retries=1`; o effort vai em `output_config={"effort": ...}`; `temperature`, `top_p` e `top_k` ficam `None` e não saem no payload | Nada |
| BM25 | `bm25s.tokenize(stopwords="pt", stemmer=Stemmer("portuguese"))`, `BM25(method="lucene")`, `retrieve(k=2)` | A remoção de acento (NFKD) antes de tokenizar, também na lista de stopwords; título e id da fonte |
| Dado pessoal | `AnalyzerEngine` com `NoOpNlpEngine(models=[{"lang_code": "pt", "model_name": "none"}])`, sem baixar modelo do spaCy, `PatternRecognizer` com `validate_result` e os operadores do `AnonymizerEngine` | O recognizer de CPF (regex e dígitos verificadores), pseudônimo estável por sessão, heurísticas de injection e padrão de SKU |
| Nomes parecidos | `difflib.get_close_matches`, da biblioteca padrão, depois da remoção de acento | `rapidfuzz` só se o `difflib` falhar no caso `rec-similar-names` |

Obs: _o `presidio-analyzer` traz o spaCy 3.8 como dependência obrigatória (uns 55 pacotes na instalação, sem download de modelo e sem custo em execução). Fica para a etapa de arquitetura decidir._
