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
