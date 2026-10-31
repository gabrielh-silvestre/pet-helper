# Spike: semântica do Inspect AI e paralelismo por thread

Versão fixada: `inspect-ai==0.3.286`, resolvida pelo `uv lock` e gravada em `pyproject.toml` com `==`. Os 15 testes de caracterização estão em `tests/unit/test_inspect_contract.py` e passam. Nada chama modelo real: o `eval` roda com `mockllm/model`.

## Comportamentos

| Comportamento | Resultado |
| --- | --- |
| Amostra que levanta erro fica fora do denominador da nota reduzida (acurácia 0,5 com 2 amostras pontuadas e 1 com erro) | confirmado |
| A contagem de amostras pontuadas mora em `log.results.scores[0].scored_samples`, não em `log.results` | confirmado |
| Amostra com erro continua no log com o erro (`sample.error.message`), uma entrada por época | confirmado |
| O log traz a nota crua de cada época (`CORRECT`, `CORRECT`, `INCORRECT`) | confirmado |
| `Score.unscored()` existe na 0.3.286 e fica fora da acurácia | confirmado; o plano B não se aplica |
| Amostra `unscored` não entra nas pontuadas: `scored_samples=1` e `unscored_samples=1` com uma pontuada, uma `unscored` e uma com erro; a com erro não entra em nenhuma das duas | confirmado |
| `Epochs(3, "at_least_3")` aprova a amostra só quando as 3 épocas passam | confirmado |
| Amostra com erro em uma só época continua pontuada, reduzida sobre as 2 épocas restantes, e reprova em `at_least_3` | confirmado |
| Com `fail_on_error=False`, as outras amostras terminam e o log fecha em `success` | confirmado |
| Com `retry_on_error=0`, a amostra com erro roda uma vez por época e não repete | confirmado |
| Com `log_dir` explícito nada é gravado em `./logs` no diretório de trabalho | confirmado |
| Solver que aguarda `asyncio.to_thread` roda 8 amostras em paralelo com `max_samples=8` | confirmado |
| Solver que chama a função síncrona direto serializa as amostras | confirmado |
| Scorer que aguarda `asyncio.to_thread` não serializa as amostras | confirmado |
| O uso por amostra que o scorer grava em `Score.metadata` é legível no log | confirmado |

Nenhum dos itens de parada (denominador por época, `./logs`, `to_thread` no solver e no scorer) ficou vermelho: todos se comportam como o plano supõe.

## Por que o solver usa `asyncio.to_thread`

O assistente LangGraph é síncrono. Chamado direto dentro da corrotina do solver, bloqueia o event loop do Inspect e as amostras rodam uma após a outra (o teste de chamada direta mede janela de pelo menos 4 vezes o tempo de espera para 8 amostras). Com `await asyncio.to_thread(...)` o loop fica livre e as 8 amostras dormem em paralelo, dentro do limite de `max_samples`. O mesmo vale para o juiz síncrono dentro do scorer.

## Escolha do deptry

Nenhum código em `src/` importa `inspect-ai` ainda, então o `deptry` acusa DEP002. A correção mínima é uma entrada `[tool.deptry.per_rule_ignores]` com `DEP002 = ["inspect-ai"]` e um comentário de uma linha: a US-008 passa a importar o pacote em `src/` e remove a entrada. Mover a dependência para o grupo `dev` foi descartado porque o harness de eval a usa em runtime.

## Streaming do `langchain-anthropic` e do `langchain-ollama` (US-004)

Caracterização em `tests/unit/test_gateway_streaming.py`, com `httpx.MockTransport` devolvendo SSE (Anthropic) e NDJSON (Ollama). Nada chama modelo real. Nenhum item de parada ficou vermelho, então o adaptador só liga `streaming=True` e o `invoke` atual segue valendo.

| Comportamento | Resultado |
| --- | --- |
| `ChatAnthropic(streaming=True)` dispara `on_llm_new_token` por trecho de texto | confirmado |
| A mensagem agregada mantém `response_metadata["stop_reason"]`, então `refused` e `truncated` continuam valendo | confirmado |
| A mensagem agregada mantém `usage_metadata` com os campos de cache (`cache_creation`, `cache_read`) | confirmado |
| Todos os blocos de raciocínio (thinking) ficam na mensagem agregada com a assinatura | confirmado |
| O turno seguinte reenvia os blocos de raciocínio sem alteração | confirmado |
| Os dois pontos de `cache_control` seguem iguais na requisição em streaming | confirmado |
| `ChatOllama` dispara `on_llm_new_token` por trecho e agrega `usage_metadata` e `done_reason` | confirmado |

## Armadilhas que o handler de trace trata

- O uso de tokens só chega completo no `message_delta` final. Numa chamada cortada o handler não recebe a mensagem parcial (`on_llm_error` só traz a exceção), então ele soma o `usage_metadata` de cada `on_llm_new_token` e entrega essa soma ao `on_error`. `add_usage(None, None)` devolve zeros, não `None`: só se soma trecho que traz uso, senão uma chamada sem uso seria cobrada como custo zero.
- O token da Anthropic é uma lista de blocos, não uma string. Só o bloco `type == "text"` com texto para o relógio do tempo até o primeiro trecho; trecho vazio ou só de raciocínio não conta. O Ollama manda string e termina com um trecho vazio.
- O LangChain engole exceção de handler: sem `raise_error = True` o `BudgetExceeded` não impede a chamada ao modelo.
