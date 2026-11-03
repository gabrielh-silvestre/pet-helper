# pet-helper

Assistente de atendimento de um e-commerce pet fictício (a petx), construído e medido sobre um kit de dados sintético em `kit/`. Responde dúvidas com base nas políticas e cita a fonte, consulta pedidos, recomenda produtos do catálogo sem inventar SKU, escala para humano quando precisa e resiste a prompt injection. A suíte de eval, em `evals/`, é a parte que mede tudo isso.

Este README resume as decisões. O detalhe e o motivo de cada uma estão em [docs/architecture.md](docs/architecture.md), nos ADRs de [docs/adr/](docs/adr/) e no [glossário](GLOSSARY.md). Os resultados medidos da baseline entram numa seção própria, a partir da rodada versionada em `evals/results/`.

## Arquitetura

O código segue as camadas MSC (Model, Service, Controller), com o LangGraph usado direto. Cada módulo esconde uma decisão que pode mudar:

| Camada | Módulo | O que esconde |
|---|---|---|
| Controller | `cli` | os comandos `chat`, `eval` e `metrics` |
| Service | `conversation` | o grafo do LangGraph, o prompt, o registro de tools, a identidade do cliente da sessão e o contador de falhas repetidas |
| Service | `guard` | a detecção e a máscara de dado pessoal (Presidio e reconhecedor de CPF próprio), a heurística de injection e a checagem de SKU e de fonte na resposta |
| Service | `evals` | os checks puros, o juiz, a rodada sobre o Inspect AI e a análise de erro |
| Model | `gateway` | o provedor (Anthropic ou Ollama), o esforço, o cache de prompt, o retry e o fallback |
| Model | `knowledge`, `orders`, `catalog` | a busca BM25 nas políticas, a API mock de pedidos e o catálogo em CSV |
| Transversal | `trace` | o formato do JSONL, o callback do LangChain, a máscara no destino e o custo |

Um turno percorre o grafo assim:

```text
mensagem -> guard de entrada -> agente <-> ferramentas -> guard de saída -> resposta
```

As ferramentas são `search_policies`, `get_order`, `search_catalog` e `escalate_to_human`. Decisões que valem destaque:

- **Identidade vem do estado, não do modelo.** O cliente se identifica pelo CPF na conversa e só vê um pedido quando o CPF é dono dele e o número é informado. O CPF fica no estado da sessão, nunca é argumento de tool, e pedido inexistente e pedido de outro cliente viram o mesmo `NOT_FOUND` antes de o modelo ver ([ADR 0001](docs/adr/0001-order-access-by-cpf-and-order-number.md)).
- **Regra de negócio vive nos documentos de política.** As tools devolvem fatos já calculados (status, data de entrega, dias desde a entrega, região, total) e nenhuma dá veredito. O modelo aplica a regra da fonte citada ([ADR 0002](docs/adr/0002-business-rules-in-policies-facts-from-tools.md)).
- **Busca lexical.** BM25 com stemmer português sobre o documento inteiro, sem chunking e sem limiar de score. O "não sei" é decisão do modelo, por regra do prompt, e o juiz mede esse acerto.
- **Guard de saída.** Confere que cada SKU citado existe no catálogo e que cada fonte citada veio da busca da sessão. Uma resposta inválida é regenerada uma vez.
- **Defesa contra injection, em ordem de peso:** nenhuma tool concede cupom ou dado alheio e todas só leem; a identidade vem do estado; o prompt tem a regra; o guard de entrada marca a suspeita no rastro, sem bloquear.

## Escolha de modelo

| Papel | Modelo | Configuração |
|---|---|---|
| Assistente | `claude-haiku-5-5` | thinking adaptativo e `effort: low`, sem `temperature`, `top_p` nem `top_k` (a API rejeita) |
| Juiz | `claude-sonnet-5-5` | `effort: medium` e saída estruturada com a crítica antes do veredito |
| Caminho sem chave | `llama3.1:8b` no Ollama | `num_ctx` de 8192 e temperature 0 |

- O Haiku é o modelo barato e fraco de propósito: a qualidade deve vir do sistema (política em documento, guards, tools que devolvem fatos), não do tamanho do modelo.
- O juiz roda numa classe acima da do avaliado. Mesmo assim é da mesma família do Haiku, o que a seção sobre as limitações do juiz declara.
- O `llama3.1:8b` é o menor Llama com tool calling e português suportado que ainda roda em CPU com 16 GB, o que permite avaliar sem pagar. `llama3.2:3b` fica como opção para máquina com pouca RAM.
- O fallback da Anthropic para o Ollama acontece só em erro de transporte (conexão, timeout, 429, 5xx ou 529), depois de um retry do SDK. Uma recusa do modelo (`stop_reason: refusal`) e um 4xx nunca disparam fallback, e as rodadas de eval o mantêm desligado.
- Modelo e endpoint se configuram por variável de ambiente:

| Variável | Efeito |
|---|---|
| `PET_HELPER_PROVIDER` | `anthropic` ou `ollama`; sem ela, `anthropic` se existir `ANTHROPIC_API_KEY`, senão `ollama` |
| `PET_HELPER_ASSISTANT_MODEL` | modelo Anthropic do assistente (padrão `claude-haiku-5-5`) |
| `PET_HELPER_OLLAMA_MODEL` | modelo Ollama, primário e de fallback (padrão `llama3.1:8b`) |
| `PET_HELPER_JUDGE_MODEL` | modelo do juiz (padrão `claude-sonnet-5-5`) |
| `PET_HELPER_EFFORT` | esforço do assistente: `low` (padrão), `medium`, `high`, `xhigh` ou `max` |
| `PET_HELPER_FALLBACK` | `ollama` (padrão) ou `none` |
| `OLLAMA_BASE_URL` | endereço do servidor Ollama |
| `PET_HELPER_KIT_DIR` | diretório do kit (padrão `kit`, relativo ao diretório de trabalho) |
| `PET_HELPER_TODAY` | data de hoje do assistente, no formato `AAAA-MM-DD` |
| `PET_HELPER_TRACE_CONTENT` | `0` grava só o tamanho do texto livre no rastro |

## Trade-offs

- **Haiku com thinking contra um modelo maior.** Custa uma fração, e a eval mostra onde ele erra. O preço é um modelo que erra mais em tool calling e em regra com data, e por isso as tools calculam as datas.
- **BM25 contra embeddings.** Com 8 documentos de 150 a 400 palavras, BM25 reindexa em milissegundos sem baixar modelo, o que a alteração ao vivo exige. O custo é não casar sinônimos que o stemmer não cobre.
- **Documento inteiro, sem limiar de score.** A citação fica trivial e cabe no contexto, mas o "não sei" depende do modelo seguir o prompt. Um limiar erraria justamente nos temas de propósito fora da cobertura, que dividem termos com políticas reais.
- **Resposta só depois do guard de saída.** O cliente não vê um SKU inventado antes do bloqueio, ao custo de latência percebida. Em produção o caminho seria streaming por sentença já checada.
- **Rastro próprio em JSONL, não Phoenix, LangSmith nem o SDK do OpenTelemetry.** Quem avalia não precisa subir processo nem abrir conta, e a máscara fica num ponto só. Os nomes das chaves seguem o OpenTelemetry GenAI, o que mantém um exportador futuro barato.
- **Presidio na máscara.** É o padrão de mercado, ao custo de cerca de 316 MB de instalação. Ele não traz CPF, que segue como reconhecedor próprio.
- **Inspect AI como motor da eval.** Entrega épocas, logs locais sem conta e visualizador, e sobra para o projeto só o que é dele (casos YAML multi-turno, juiz com `unable`, freio de custo por comando). O preço é uma dependência pré-1.0, fixada no `uv.lock`.
- **Turnos fixos nos casos de eval.** Um usuário simulado por LLM reagiria à resposta, mas custaria chamadas e quebraria o determinismo. Quando o assistente pede o que o roteiro não prevê, a execução vira `unable`, não falha.
- **Juiz só para o que não se calcula.** Tom, julgamento de escalonamento e acerto do "não sei" vão ao juiz; citação, número do pedido, RN01 a RN04 e recusa de pedido alheio são asserts em código.

## Como rodar o chat

### Setup nativo

```sh
uv sync
PET_HELPER_TODAY=2026-11-03 uv run pet-helper chat
```

`PET_HELPER_TODAY` fixa a data de hoje do assistente. Sem ela, o assistente usa a data real, e os prazos dos pedidos do kit (calculados a partir de 2026-11-03, a `reference_date` de `kit/orders.json`) saem errados.

Por padrão, a CLI carrega políticas, catálogo e pedidos do diretório `kit` relativo ao diretório de trabalho. Use `PET_HELPER_KIT_DIR` para apontar para outro caminho do kit.

O provedor do modelo vem de três variáveis:

- `PET_HELPER_PROVIDER`: `anthropic` ou `ollama`. Sem ela, a CLI usa `anthropic` quando `ANTHROPIC_API_KEY` está definida e `ollama` nos demais casos.
- `ANTHROPIC_API_KEY`: chave da API da Anthropic.
- `OLLAMA_BASE_URL`: endereço do servidor Ollama, quando não for o padrão.

Se a conversa terminar com a causa `model_unavailable`, o provedor não respondeu: confira as variáveis acima e rode o comando de novo.

### Setup sem chave (Ollama)

Para avaliar sem chave de API paga, o Docker Compose sobe o Ollama (`ollama/ollama:0.40.12`), baixa o modelo configurado (padrão `llama3.1:8b`) antes de iniciar o app e abre o chat no terminal:

```sh
docker compose run --rm app
```

Em máquinas com pouca RAM, sobrescreva a tag do modelo pela variável `PET_HELPER_OLLAMA_MODEL`:

```sh
PET_HELPER_OLLAMA_MODEL=llama3.2:3b docker compose run --rm app
```

Built with Llama (Meta Llama 3.1, sob a Llama 3.1 Community License).

## Rastros da conversa

O `pet-helper chat` grava um rastro em `traces/AAAA-MM-DD/<session_id>.jsonl`, relativo ao diretório de trabalho: um arquivo por sessão, uma linha por evento. Nas rodadas de eval, os rastros ficam em `traces/` dentro da pasta da rodada. Exemplo de linha, de uma sessão com o modelo simulado:

```json
{"ts": "2026-10-31T07:28:14.212741+00:00", "session_id": "sessao-1", "turn": 1, "event": "turn.start", "trace_id": "sessao-1:1", "gen_ai.input.messages": "Oi, meu CPF é cpf#e774ebd4"}
```

Toda linha traz `ts`, `session_id`, `turn`, `event` e `trace_id` (`<session_id>:<turn>`). Os eventos e as chaves principais:

| Evento | Chaves |
|---|---|
| `turn.start` | `gen_ai.input.messages` |
| `guard.input` | `pet_helper.cpf_identified`, `pet_helper.cpf_conflict`, `pet_helper.injection_suspected` |
| `retrieval` | `gen_ai.retrieval.query.text`, `pet_helper.hits` (fonte e score de cada documento) |
| `model.call` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.time_to_first_chunk`, `duration_ms`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.response.finish_reasons`, `pet_helper.fallback`, `pet_helper.streaming`, `pet_helper.cost_usd`, `pet_helper.price_unknown`, `error.type` |
| `tool.call` | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `duration_ms`, `pet_helper.status`, `error.message` |
| `order.refused` | `pet_helper.reason` (`not_found` ou `other_customer`) |
| `guard.output` | `pet_helper.invalid_skus`, `pet_helper.unretrieved_sources`, `pet_helper.regenerated` |
| `escalation` | `pet_helper.escalation_reason` |
| `turn.end` | `gen_ai.output.messages`, `pet_helper.sources` |

As linhas `model.call` e `tool.call` levam também `span_id` e `parent_span_id`, copiados do `run_id` e do `parent_run_id` do LangChain. Os ids de correlação não são os ids hexadecimais de 32 e 16 caracteres do OpenTelemetry; um exportador futuro faz o mapeamento. A tabela completa do nosso formato para a convenção OpenTelemetry GenAI está em [docs/architecture.md](docs/architecture.md), seção 2.

- **Máscara.** Cada texto passa por `Guard.mask` no destino: CPF vira o pseudônimo estável `cpf#<8 hex>`, e e-mail e telefone são substituídos. Depois da máscara, o texto é truncado em 10.000 caracteres e a linha vai por `json.dumps`.
- **Interruptor de conteúdo.** O texto livre vem ligado por padrão, mascarado, para que o rastro explique a resposta. Com `PET_HELPER_TRACE_CONTENT=0`, os campos `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.retrieval.query.text` e `gen_ai.tool.call.arguments` gravam só o tamanho. As rodadas de eval sempre rodam com o conteúdo ligado, porque a análise de erro lê as conversas.
- **Tempo até o primeiro chunk.** Conta o primeiro chunk com texto, em segundos, e fica nulo quando a chamada não fez streaming. Com thinking ligado, o valor inclui o tempo de raciocínio que antecede o texto. Um teste sem modelo varre os rastros de exemplo atrás de CPF e e-mail crus, e cada rodada de eval repete essa varredura em seus arquivos.

## Como rodar o eval

A suíte tem 20 casos visíveis em `evals/cases/`, um YAML por caso, e 5 casos reservados em `evals/held_out/cases/`, que um hook esconde dos agentes que corrigem o assistente (a pasta está versionada: o histórico do git fica como residual aceito, descrito em `docs/directives/loop-melhoria.md`). Cada caso roda 3 vezes, e um caso passa quando passa nas 3. Criar um caso novo é criar um arquivo.

```sh
uv run pet-helper eval                                   # as duas suítes visíveis, juiz síncrono
uv run pet-helper eval --suite regression                # só uma suíte
uv run pet-helper eval --case rn03-south-exact-149-not-free
uv run pet-helper eval --judge none                      # só as conversas, sem juiz
uv run pet-helper eval --judge batch                     # juiz pela Message Batches API
uv run pet-helper eval --collect <rodada>                # termina uma rodada com lote pendente
uv run pet-helper eval --rejudge <rodada>                # aplica o juiz às transcrições salvas
uv run pet-helper eval --case <caso> --runs 10           # repetição de um caso decisivo
PET_HELPER_HELD_OUT=1 uv run pet-helper eval --held-out --judge none
```

O assistente roda no provedor configurado; o juiz precisa de `ANTHROPIC_API_KEY`. Sem chave, `--judge none` roda só as conversas, e a rodada completa no Llama em CPU leva horas.

- **Estimativa e confirmação.** Antes de chamar o modelo, o comando mostra a estimativa de custo, calculada pela média da última rodada salva ou, sem rodada anterior, por US$ 0,004 por turno, e pergunta `Run? [y/N]`. Qualquer resposta que não seja `y` ou `yes` aborta.
- **Teto de US$ 5 por comando.** Rodada, repetição de caso decisivo, `--rejudge`, validação do juiz e fumaça têm cada um a própria estimativa e o próprio teto. O comando reserva o pior caso de cada chamada antes de fazê-la, para ao chegar ao teto e salva o que já tiver. Preço desconhecido ou chamada sem contagem de tokens interrompe a rodada, e as execuções restantes ficam como `not_run`.
- **Nada automático.** Nenhum gancho do git, CI ou agendamento chama o modelo real. Toda rodada é disparada à mão.
- **Juiz.** `--judge sync` é o padrão. `--judge batch` envia o lote depois das conversas e espera o resultado; se ele não terminar no prazo, a rodada guarda o id do lote em `round.json` e `--collect <rodada>` a termina depois. `--judge none` serve à baseline, porque o prompt do juiz ainda não está congelado nessa hora, e `--rejudge <rodada>` aplica o juiz congelado às transcrições salvas, com estimativa e teto próprios. Os casos com critério do juiz ficam `judge_pending` até o re-julgamento.
- **Casos reservados.** `--held-out` exige `PET_HELPER_HELD_OUT=1`. Resultados e rastros vão para `evals/held_out/results/`, fora do git, e o terminal mostra só o id do caso, o k/3 e o SHA do código medido.
- **Repetição decisiva.** `--case <caso> --runs 10` roda um caso de novo 10 vezes e o compara com a rodada de referência da suíte. A regra de aceitação está em [loop-melhoria.md](docs/directives/loop-melhoria.md).

Cada rodada grava uma pasta `evals/results/AAAA-MM-DDTHHMMSS/`:

| Arquivo | Conteúdo |
|---|---|
| `round.json` | SHA do git, modelos, hash dos prompts, custo total, `pii_check` e o id do lote pendente, se houver |
| `*.eval` | o log do Inspect: conversas mascaradas, checks e tokens, que `uv run inspect view --log-dir <pasta>` abre no navegador |
| `judge.jsonl` | a crítica e o veredito do juiz, a fonte única dos vereditos |
| `cases.csv` | uma linha por caso: `case_id`, `suite`, `k_of_3`, `first_failure_note`, `category`, `human_label`, `cost_usd`, `failed_checks`, `unable_runs`, `unable_reasons` |
| `traces/` | os rastros JSONL de cada conversa |

Cada execução termina em `pass`, `fail`, `error`, `unable` ou `not_run`, e só `pass` conta no k. Execução `unable` (conversa fora do roteiro ou juiz sem condição de avaliar) não é passa nem falha e fica fora das categorias de falha.

Os checks e o grafo têm testes sem modelo, que rodam sem chave e sem custo:

```sh
uv run pytest -q --cov
```

### Smoke: uma execução por caso

`--runs 1` é a fumaça, o caminho de quem avalia sem pagar muito:

```sh
uv run pet-helper eval --runs 1 --case rn03-south-exact-149-not-free
```

A saída leva `{"smoke": true, "runs": 1}`. A fumaça não conta como rodada, nunca decide aceitação e não grava em `evals/results/`: resultados e rastros vão para uma pasta temporária `pet-helper-smoke-*`, cujo caminho é a última linha da saída. A rodada completa continua exigindo as 3 execuções.

## Métricas com `pet-helper metrics`

```sh
uv run pet-helper metrics traces/2026-11-03
uv run pet-helper metrics evals/results/<rodada>/traces
```

O comando lê os JSONL da pasta, sem chamar modelo, e imprime:

- por provider e modelo: chamadas, tempo até o primeiro chunk (p50 e p95, em ms), chamadas sem streaming (`ttft_missing`), tokens por segundo mediano depois do primeiro chunk, tokens de entrada, saída, cache read e cache write, custo em US$ e taxa de acerto do cache (`cache_read / input_tokens`);
- por tool: chamadas e taxa de erro;
- fallback: quantidade e taxa;
- escalonamentos por motivo;
- por conversa: custo em US$ e latência em segundos.

O custo vem da `genai-prices` por `cost_usd`, e o Ollama custa zero.

## Premissas de custo

O custo abaixo é uma estimativa a partir de preços públicos. Os valores medidos pelo `pet-helper metrics` sobre a rodada versionada a substituem.

Por chamada ao modelo, com `entrada_nova = entrada - cache_read - cache_write` (a `entrada` do LangChain já soma o cache):

```text
custo = (entrada_nova x preço_entrada + cache_read x preço_cache_read
         + cache_write x preço_cache_write + saída x preço_saída) / 1.000.000
```

Preços de referência em US$ por milhão de tokens (prompt de até 100K tokens), lidos da `genai-prices` 0.2.1, a mesma tabela que o teto de custo e o `metrics` usam:

| Modelo | Entrada | Saída | Cache read | Cache write (5 min) |
|---|---|---|---|---|
| `claude-haiku-5-5` | 0,10 | 0,50 | 0,01 | 0,125 |
| `claude-sonnet-5-5` | 2,00 | 10,00 | 0,10 | 2,50 |

O Batch tem 50% de desconto sobre esses preços, e o Ollama custa zero. Os preços podem mudar e a tabela da biblioteca não garante exatidão: tratam-se de premissas, não de fatura.

- **Conversa de 4 turnos (premissa).** Cerca de 6 chamadas ao modelo, cada uma com uns 3,9K tokens de entrada e uns 250 de saída. Dá cerca de US$ 0,003 sem cache.
- **Estimativa do comando `eval`.** Sem rodada anterior, usa US$ 0,004 por turno e por execução; com rodada anterior, usa a média por caso dela. Cada chamada do juiz entra com uma estimativa padrão de US$ 0,007.
- **Latência.** Não há premissa: o tempo até o primeiro chunk e a duração por conversa só entram medidos, pelo `pet-helper metrics`, na seção de resultados.

## Limitações do juiz

O juiz por modelo avalia só tom, julgamento de escalonamento e acerto do "não sei", com um critério por chamada e a opção "não sei avaliar" (`unable`). Limitações declaradas:

- **Sem controle de temperatura.** A API rejeita `temperature` no Sonnet 5.5, então o mesmo julgamento pode variar entre chamadas.
- **Mesma família do avaliado.** O Sonnet e o Haiku são da mesma família, com risco de autopreferência: o juiz pode favorecer o estilo do modelo que avalia.
- **Falhas plantadas.** Quando a baseline não tem falhas naturais suficientes, o conjunto de rótulos é completado com falhas plantadas por modelos de texto determinísticos. Elas são declaradas como plantadas, e as contagens naturais e plantadas aparecem à parte, porque uma falha plantada é mais fácil de achar do que uma natural.
- **Tamanho da amostra e intervalos.** Os rótulos são cerca de 50 itens por critério, divididos por caso em dev e teste. Com tão poucos itens, TPR e TNR saem com intervalo de Wilson largo, e o relatório mostra o intervalo e as contagens brutas, nunca só o ponto.
- **Execuções correlacionadas.** As 3 execuções de um caso são quase cópias umas das outras, então o `n` efetivo fica perto do número de casos, não do número de execuções.
- **Turnos fixos.** Os turnos são roteirados e não reagem à resposta. Um assistente que pede um dado que o roteiro não previu deixa a execução `unable`, e o juiz não mede esse desvio.
- **Suíte pequena.** São 20 casos visíveis, então o intervalo de Wilson do pass^3 da suíte é largo e um caso muda a taxa em 5 pontos percentuais.
- **Juiz Llama não validado.** O mesmo prompt pode rodar no `llama3.1:8b`, um juiz pequeno de concordância esperada menor. Esta entrega valida só o Sonnet, e o juiz Llama não foi validado contra rótulos humanos: nenhum veredito dele decide aceitação.
- **Validação vale para um par.** A validação cobre o modelo do juiz e o prompt congelado, identificados por um hash que acompanha cada veredito. Mudar um dos dois exige validar de novo.

## Limitações dos checks de texto

Os checks `expected_value`, `must_include` e `must_not_include` comparam trechos literais da resposta. O assistente pode acertar com outras palavras, e a lista de trechos não acompanha todas as variações.

- **Oscilação medida.** Em quatro rodadas visíveis com `--judge none`, o número de casos em 3 de 3 foi 15, 13, 15 e 14 de 20. Entre uma rodada e outra, os casos que mais oscilam são os de direção ("tem frete grátis ou não", "o cancelamento é permitido"), porque cada execução usa uma paráfrase nova da mesma conclusão.
- **O que foi feito.** As listas dos casos que falharam por redação foram ampliadas só por adição, e cada entrada positiva ganhou o gêmeo negado em `must_not_include`, provado por testes sem modelo com respostas reais e respostas erradas escritas à mão. Os tipos de check não mudaram de semântica.
- **O que não foi feito.** Não há regex de polaridade nem critério novo do juiz para esses casos. A revisão adversarial mostrou que a regex erra nos dois sentidos com respostas reais, e o juiz ainda não tem rótulos para validar um critério novo.
- **Como ler o k/3.** Um caso abaixo de 3 de 3 pode ser redação do check e não erro do assistente. Os números de direção (por exemplo R$ 224,80 no `rn03-midwest-discount-not-free`) são mais estáveis que as frases, e a leitura da transcrição vem antes de culpar o assistente.
- **Resultado otimista nas visíveis.** As listas foram ajustadas olhando os 20 casos visíveis. A taxa neles é de desenvolvimento, e só os casos reservados medem generalização.

## Loop de melhoria

O ciclo completo está em [docs/directives/loop-melhoria.md](docs/directives/loop-melhoria.md): medir, conferir o avaliador, diagnosticar, escolher a categoria de maior frequência vezes severidade, corrigir pelo fluxo de desenvolvimento e aceitar ou rejeitar. A análise lê o `cases.csv` da rodada:

```sh
uv run python -m pet_helper.evals.analysis report evals/results/<rodada>
uv run python -m pet_helper.evals.analysis categories evals/results/<rodada>/cases.csv
```

### Regra de aceitação e aritmética dos limiares

Cada caso decisivo roda de novo 10 vezes. Um caso de regressão decisivo é rejeitado com 2 ou mais falhas em 10, e um caso do alvo é aceito com 9 ou mais passes em 10. Os limiares são binomiais, com execuções independentes:

| Taxa real de passes do caso | Regressão rejeitada (2 ou mais falhas em 10) | Alvo aceito (9 ou mais passes em 10) |
|---|---|---|
| 0,95 | cerca de 9% | cerca de 91% |
| 0,90 | cerca de 26% | cerca de 74% |
| 0,70 | cerca de 85% | cerca de 15% |

Os limiares deixam passar uma regressão pequena de propósito. Uma queda real de 1,0 para 0,9 só é pega em cerca de 7% das vezes, porque o caso primeiro precisa cair abaixo de 3 de 3 na rodada de 3 execuções para virar decisivo e depois ser rejeitado nas 10. O relatório mostra, ao lado do pass^3, o intervalo de Wilson da suíte e a diferença pareada contra a rodada anterior, dada em casos que ganharam e casos que perderam o 3 de 3. O intervalo informa e não decide.

### Quem anota

O critic da cadeira do dono anota cada falha primeiro: uma nota sobre a primeira coisa que deu errado e uma categoria proposta. O dono lê cerca de 5 conversas e confirma as categorias. Essa ordem tem um risco de ancoragem: o dono confirma o que o critic já escreveu, e a anotação do critic pode puxar o julgamento dele. A recomendação usual é que o especialista anote cerca de 30 conversas primeiro, antes de ver a anotação de qualquer modelo. Esta entrega segue a ordem do critic primeiro, e o risco fica declarado.

### Da transcrição que falhou ao teste sem modelo

Quando uma conversa real falha por um bug do grafo, de um guard ou de uma tool, e não por uma resposta ruim do modelo, ela vira um teste de regressão sem modelo, escrito à mão com o `FakeGateway`:

1. Abra o rastro da sessão e confirme que o erro está no código: o que a tool devolveu, o que o guard deixou passar, o estado da sessão. Falha de redação ou de julgamento do modelo não se reproduz com modelo simulado e pede um caso de eval.
2. Monte o roteiro do modelo a partir do rastro: cada chamada a tool (`tool.call`, com `gen_ai.tool.name` e `gen_ai.tool.call.arguments`) vira uma `AIMessage` com `tool_calls`, e a resposta final (`turn.end`) vira uma string. O rastro mascara o CPF; use no teste um CPF do kit.
3. Chame `Assistant.turn` com as mensagens do cliente e confira a resposta e o que o modelo recebeu em `gateway.calls`.
4. Guarde o teste em `tests/unit/`, com o nome no formato `it should ...`.

Exemplo: o cliente pergunta por um pedido sem informar o CPF, e o teste confere que a tool devolve `needs_identification` ao modelo.

```python
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from pet_helper.conversation import build_assistant
from pet_helper.gateway import FakeGateway

KIT_DIR = Path("kit")


def test_it_should_ask_for_the_cpf_before_showing_an_order():
    gateway = FakeGateway(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_order",
                        "args": {"order_number": "PED-10001"},
                        "id": "call_1",
                    }
                ],
            ),
            "Preciso do seu CPF para consultar o pedido.",
        ]
    )
    assistant = build_assistant(gateway=gateway, kit_dir=KIT_DIR)

    reply = assistant.turn("session-1", "Quero saber do pedido PED-10001")

    tool_results = [m for m in gateway.calls[-1] if isinstance(m, ToolMessage)]
    assert tool_results[-1].content == '{"status": "needs_identification"}'
    assert reply.text == "Preciso do seu CPF para consultar o pedido."
```

Um script que gere esse teste a partir do rastro fica como trabalho futuro. Ele só ajudaria em bug de grafo, guard e tool, nunca em falha do lado do modelo.

## O que faria com mais tempo

- Validar também o juiz Llama contra rótulos humanos, para o caminho sem chave ter um juiz com TPR e TNR conhecidos.
- Escrever a transcrição de referência dos 20 casos que ainda não têm uma (hoje só os casos adversariais têm), para achar um check quebrado antes da baseline.
- Construir o script que transforma uma transcrição que falhou em teste com `FakeGateway`.
- Medir o assistente com um usuário simulado que reage à resposta, para cobrir o que os turnos fixos não cobrem, aceitando o custo e a variação.
- Aumentar a suíte e os casos reservados para estreitar os intervalos de confiança, e fazer o especialista anotar mais conversas antes do critic.
- Em produção: streaming por sentença já checada pelo guard, exportar o rastro para um backend de OpenTelemetry (a troca de destino mexe só no módulo `trace`), limite de taxa, filas e cache semântico.
- Embeddings locais, se o corpus de políticas crescer a ponto de o BM25 não bastar.
- Um visualizador HTML das conversas, se ler no terminal e no `inspect view` fizer falta.

## Como a IA foi usada

O projeto foi feito com o Claude Code e subagentes. O dono decidiu produto e estratégia (as premissas ficam em `docs/directives/estrategia.md`), e um critic em modelo maior decidiu a arquitetura no lugar do dono, sempre registrado e depois aprovado por ele.

- **Kit.** Os dados sintéticos foram gerados por IA a partir de prompts que ficam em `kit/PROMPTS.md`. A revisão crítica do que ela gerou, as armadilhas por caso e os temas deixados de fora de propósito estão em `kit/KIT.md`, e um script de validação confere contagens e coerência.
- **Fluxo.** Cada unidade de trabalho passa por entrevista, plano, cadeira do dono, aprovação, entrega com TDD, revisão local e revisão do PR por um painel. Quem escreve os testes não escreve o código: o `test-engineer` escreve os testes que falham, e o `executor` nunca os edita.
- **Guardas por hook.** Hooks do agente bloqueiam a edição de testes pelo executor e os caminhos diretos até os casos reservados (não o histórico do git, residual aceito), e rodam as checagens estáticas quando um subagente termina. Os casos reservados nascem de um fluxo isolado, em que o agente lê só os requisitos, o glossário e os dados do kit, e o dono aprova cada um.
- **Avaliação.** Nada que chama o modelo real roda sozinho: as rodadas pagas são manuais, com estimativa, confirmação e teto de custo. As anotações de falha do critic são conferidas pelo dono antes de valerem.
- **Decisões registradas.** As decisões de arquitetura e o raciocínio do plano ficam em `docs/architecture.md`, em `docs/specs/` e no registro do hexlog, não só nas conversas.
