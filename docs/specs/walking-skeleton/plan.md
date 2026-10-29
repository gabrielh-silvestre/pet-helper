# Plano: esqueleto base

Status: aprovado pelo dono e entregue em `feat/walking-skeleton`

Spec: [spec.md](spec.md). Base: [architecture.md](../../architecture.md) e [fluxo-desenvolvimento.md](../../directives/fluxo-desenvolvimento.md). Modo do consenso: SHORT.

## Resumo RALPLAN-DR

### Princípios

1. O menor caminho de ponta a ponta, `chat` -> `Assistant.turn` -> grafo de um nó -> `fake`, e nada além disso.
2. Toda interface exposta nesta unidade é um subconjunto ou um acréscimo compatível da interface de `docs/architecture.md`, com os mesmos nomes e tipos, e o que falta entra por acréscimo (campo, parâmetro com padrão, função nova), nunca renomeando nem mudando tipo. O que fica abaixo de cada módulo é decisão do executor, exceto o que os testes fixam (seção Contratos).
3. Uma dependência de runtime só entra se o código a importa, já que o `deptry` faz parte do gate.
4. O `fake` é o seam de teste, ou seja, o fluxo inteiro roda no pytest sem modelo e sem rede.
5. Sem especulação: pacote vazio é só `__init__.py`, e campo, comando ou ramo que nada lê fica para a unidade que o usa.
6. Um commit fecha uma unidade de trabalho (a spec), nunca um passo do TDD.

### Os 3 fatores de decisão

1. Não retrabalhar o seam do gateway quando o primeiro adaptador real entrar, já que a arquitetura prevê `langchain-anthropic` e `langchain-ollama`.
2. A menor superfície de dependência e de código, como a spec pede ("só o que o código importa").
3. Testar sem modelo com o gate que já existe (`prek` + `pytest --cov` + `deptry`), mudando a configuração do repositório só no necessário para o pacote instalar.

### Opções

**Opção A (escolhida): fatia vertical fina, mensagens do `langchain-core`, CLI com `argparse` e `default_gateway()` dentro do gateway.**

- Prós: o seam do gateway já nasce com o tipo de mensagem dos adaptadores futuros, com só 2 dependências diretas (`langgraph` e `langchain-core`), e o `argparse` não pesa no lock. Além disso, a escolha do provider fica no gateway (`architecture.md`, linha do módulo `gateway`), dessa forma a CLI não muda quando o primeiro adaptador real entrar.
- Contras: o `langchain-core` vira dependência direta antes de existir um adaptador real, e trocar o `argparse` caso a CLI cresça com `eval` e `metrics` é uma mudança futura, porém pequena.

**Opção B: gateway com mensagens próprias (dataclass), sem `langchain-core`.**

- Prós: fica independente do ecossistema langchain e declara uma dependência direta a menos, ganho que é só de declaração, já que o `langgraph` traz o `langchain-core` de qualquer forma.
- Contras: o primeiro adaptador real devolve `AIMessage`, logo a unidade que o trouxer teria de reabrir a interface do gateway, os testes do `fake` e o nó do grafo. Aqui o fator 1 pesa mais que o fator 2.

**Alternativas invalidadas:**

- Função simples sem LangGraph, porque a spec e a arquitetura fixam o `StateGraph` e o AC4 mede o grafo.
- Seleção do provider por variável de ambiente já no esqueleto, que com um único adaptador seria um ramo sem segunda saída. O `default_gateway()` devolve o `fake` sem ler o ambiente, e a seleção entra junto com o primeiro adaptador real.
- `typer`/`click` na CLI, já que um único comando `chat` não justifica uma dependência.
- Embrulhar um fake pronto do `langchain-core` 1.7.1 (`FakeListChatModel`, `GenericFakeChatModel`) no `FakeGateway`, porque nenhum deles guarda as chamadas recebidas, logo exigiria uma classe para o protocolo e mais um callback para as chamadas, saindo mais código que o fake próprio ([architecture.md](architecture.md), decisão 3).

## Contratos que os testes fixam

O `test-engineer` escreve os testes antes do código e o `executor` não edita `tests/`, por isso estes nomes e comportamentos são o acordo entre os dois. O resto (arquivos, helpers, estado do grafo) fica com o executor.

### `pet_helper.gateway`

- `DEFAULT_REPLY: str`, constante pública com a resposta padrão do `fake`.
- `ModelGateway`, protocolo com `complete(messages: Sequence[BaseMessage], tools: Sequence = ()) -> ModelResult`. O `tools` fica porque é a assinatura de `architecture.md`, mesmo que nenhum código o use nesta unidade.
- `ModelResult`, dataclass com um único campo `message` (`AIMessage`). É o tipo de retorno estável do seam e só é construído dentro de `gateway` (adaptadores e fake), já testes e chamadores apenas leem atributos. Dessa forma um campo futuro (`provider`, `usage`, `ttft_ms` e os demais da arquitetura) mexe só no gateway, e ninguém deve "desembrulhar" o resultado para devolver a mensagem direto.
- `FakeGateway(replies: Sequence[str] = (DEFAULT_REPLY,))`, que devolve as respostas em ordem e em ciclo, cada uma embrulhada em `AIMessage`, e guarda em `calls` (lista) uma cópia da lista de mensagens recebida em cada chamada, na ordem. Obs: _`replies` não vazio é pré-condição, sem guarda e sem teste._
- `default_gateway() -> ModelGateway`, que devolve `FakeGateway()` sem ler variável de ambiente.

### `pet_helper.conversation`

- `Reply`, dataclass imutável `Reply(text: str, sources: tuple[str, ...] = (), escalation: None = None)`. O `escalation` fica fixado como `None`, e o tipo dele nasce com a unidade de escalação.
- `Assistant(gateway)` com `turn(session_id: str, text: str) -> Reply`. O gateway recebe exatamente uma `HumanMessage` com o texto do turno, sem prompt de sistema (que chega com a unidade do prompt) e sem histórico. O teste compara `[(m.type, m.content) for m in calls[0]] == [("human", text)]`, isso porque o `add_messages` atribui ids e a igualdade direta de `HumanMessage` falharia. O `session_id` faz parte da interface, porém nada o lê nesta unidade (não há checkpointer), e a config `thread_id` não é passada.
- `Reply.text` vem do `message.text` do `AIMessage`, nunca de `.content`. Na versão resolvida hoje, `langchain-core` 1.7.1, o `text` é propriedade e não método.
- `build_graph(gateway)`, que devolve o grafo compilado com um nó `agent` e sem checkpointer.

### `pet_helper.cli`

- `main(argv=None) -> int`. O `chat` lê com `input("> ")` em laço, e a linha passa por `strip()` antes de ser testada e de ir ao gateway. Linha vazia ou só com espaços é ignorada, sem chamada ao gateway e sem saída, já cada linha com texto imprime a resposta. O EOF (Ctrl-D) encerra com código 0 e sem traceback.
- Sem comando ou com comando desconhecido, o `main` levanta `SystemExit` com código 2, que é o comportamento do `argparse`.
- A CLI monta `Assistant(default_gateway())` e é a única raiz de composição.
- Registrado em `[project.scripts]` como `pet-helper = "pet_helper.cli:main"`.

## Commit e histórico

Por decisão do dono, o commit fecha a unidade de trabalho e não o passo do TDD, logo não existe commit vermelho nem commit intermediário. A saída do `pytest` do vermelho é guardada no hexlog e no corpo do PR, sem ir para o histórico. No fim, depois do gate completo e dos ajustes do `/review-pr --normal`, um único commit Conventional (só subject, até 80 caracteres), como `feat: add walking skeleton with fake gateway and chat command`, reúne testes, código, empacotamento, README e `docs/specs/walking-skeleton/`.

Como o `prek run --all-files` ignora arquivo não rastreado, cada agente termina o seu passo com `git add -A` (só o índice, sem commit), e o passo 6 repete o `git add -A` antes do gate e do `code-simplifier`. Lembrando que a evidência para o hexlog só vale com os arquivos indexados.

## Passos

### 1. Branch, worktree e pasta da spec

Criamos a worktree com `wt` a partir da `main`, na branch `feat/walking-skeleton` ([fluxo-desenvolvimento.md](../../directives/fluxo-desenvolvimento.md)). A pasta `docs/specs/walking-skeleton/` está sem versionar na `main`, então ela é movida para a worktree, dessa forma viaja na branch da unidade e a `main` fica limpa. Dentro da worktree rodamos `uv sync`. A spec de trabalho em `.omc/specs/` é ignorada pelo git e fica onde está.

Aceite (AC7): `wt list` mostra a worktree, `git branch --show-current` nela devolve `feat/walking-skeleton`, `git status` na `main` não lista `docs/specs/` e `uv sync` termina sem erro.

### 2. Empacotamento e pacotes (antes do vermelho)

O `executor` prepara o terreno para que o vermelho falhe por símbolo ausente, e não por pacote ausente:

- No `pyproject.toml`, o `[build-system]` com `uv_build` e limite superior de versão (`uv_build>=0.12.3,<0.14`), já que com o `uv` local em 0.12.3 o backend embutido é usado sem baixar nada do PyPI e o layout `src/pet_helper` funciona sem configuração extra. Entram também `[project.scripts] pet-helper = "pet_helper.cli:main"`, as dependências de runtime com versão mínima (`langgraph` e `langchain-core`, nas versões que o `uv lock` resolver) e o `uv.lock` regerado. O `src` não entra no `pythonpath` do pytest, porque o teste importa o pacote instalado.
- Os 9 pacotes em `src/pet_helper/`, cada um só com `__init__.py` vazio.

Ao terminar: `git add -A`.

Aceite (AC5 parcial): `uv lock --check` e `uv sync --locked` passam, e `uv run python -c "import pet_helper.cli, pet_helper.conversation, pet_helper.gateway, pet_helper.guard, pet_helper.evals, pet_helper.knowledge, pet_helper.orders, pet_helper.catalog, pet_helper.trace"` roda sem erro.

### 3. Vermelho: testes do `test-engineer`

Os testes são escritos antes de qualquer código dos três módulos e confirmados falhando pelo motivo certo, ou seja, por símbolo ausente e não por erro do próprio teste. A saída do `pytest` do vermelho é salva em `.ignore/` (ignorado pelo git) e anexada ao hexlog, sem commit.

- Os 9 pacotes importam (AC1), sendo esta a única checagem permanente de AC1.
- `Assistant.turn` com `FakeGateway` devolve o texto roteirizado em `Reply`, com `sources` vazio e sem escalação, e o gateway recebe só a `HumanMessage` do turno, comparada por `(type, content)`. Dois turnos com o mesmo `session_id` chegam sem o histórico um do outro, o que verificamos em `calls` (AC3, sem memória).
- O grafo de `build_graph` tem exatamente o nó `agent` além de início e fim, e o `checkpointer` é `None` (AC4).
- `main(["chat"])` com `input` simulado e `pet_helper.cli.default_gateway` trocado por um `FakeGateway` roteirizado, com resposta diferente de `DEFAULT_REPLY`, imprime essa resposta para cada linha com texto, ignora a linha em branco e sai com 0 e sem traceback no EOF, já `main([])` levanta `SystemExit` com código 2 (AC2).
- Na entrada do script, `entry_points(group="console_scripts", name="pet-helper")` existe e `ep.load() is main` (AC2).
- `default_gateway()` devolve um gateway que responde `DEFAULT_REPLY`, comportamento só do esqueleto, que a unidade do primeiro adaptador real substitui.
- Um `conftest.py` em `tests/` define `LANGSMITH_TRACING=false` em cada teste, por fixture automática, para que nenhum teste consiga enviar rastro pela rede (AC3).

Aceite: `uv run pytest -q` falha só nos testes novos, cada um por símbolo ausente, e os testes que já existem em `tests/harness/` seguem verdes. Ao terminar: `git add -A`.

### 4. Verde: código do `executor`

O `executor`, sem editar `tests/`, cria o mínimo para os testes passarem:

- `gateway`: protocolo, `ModelResult`, `FakeGateway`, `DEFAULT_REPLY` e `default_gateway`.
- `conversation`: `StateGraph` com um nó `agent`, que chama o gateway com a mensagem do turno e devolve a resposta, `compile()` sem checkpointer, e `Assistant.turn` invocando o grafo e montando o `Reply`.
- `cli`: `main` com `argparse` e o subcomando `chat`.
- Sem trace, guard, tools, escrita em disco ou chamada de modelo.

Aceite (AC2, AC3, AC4): `uv run pytest -q --cov` verde, com cobertura de branches em `src/` >= 80%, e o hook do `executor` roda a verificação completa ao terminar. Ao terminar: `git add -A`.

### 5. README e diretriz

- `README.md` na raiz com o setup em dois comandos, `uv sync` e `uv run pet-helper chat`, e nada além disso (sem arquitetura nem avaliação).
- A linha "Os testes não rodam no `pre-commit`: o vermelho do TDD é commitado de propósito" de [fluxo-desenvolvimento.md](../../directives/fluxo-desenvolvimento.md) (linha 64) é reescrita para a regra nova: o commit fecha a unidade de trabalho e nunca um passo do TDD, logo o vermelho fica fora do histórico e sua saída vai para o hexlog e o PR. A tabela de ganchos e o resto do arquivo não mudam.

Aceite (AC6): `grep -c 'uv sync' README.md` e `grep -c 'uv run pet-helper chat' README.md` retornam 1 ou mais, o README não tem outro bloco de comandos, e `grep -n 'commitado de propósito' docs/directives/fluxo-desenvolvimento.md` não acha nada. Ao terminar: `git add -A`.

### 6. Verificação, revisão, commit e PR

A sequência é: `git add -A`, gate pré-commit (seção Verificação), `code-simplifier` ao fechar a spec, `/review-pr --normal` na worktree, o `executor` aplicando os achados e terminando com `git add -A`, gate pré-commit de novo, atualização da linha de status do plano e da spec para refletir a entrega, `git add -A`, commit único, checagens pós-commit, push e PR em draft para a `main`. Depois vem o `/review-pr --panel` no PR, por agente isolado numa tab nova do Herdr, e só o dono faz o merge.

Aceite (AC5, AC7): gate pré-commit verde e, depois do commit, `cz check` verde e `git log main..HEAD --oneline` com um único commit Conventional de até 80 caracteres, com o PR aberto a partir de `feat/walking-skeleton`.

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| `pyproject.toml` sem `[build-system]`, então o script não instala e o AC2 falha só fora do pytest | O passo 2 o inclui antes do vermelho, e a verificação roda o `pet-helper chat` real com stdin por pipe |
| `uv.lock` desatualizado quebra o hook `uv-lock` e o CI (`uv sync --locked`) | O lock é regerado no passo 2 e conferido com `uv sync --locked` |
| O `pyrefly` barraria um commit vermelho | Resolvido, já que não existe commit vermelho (decisão do dono) e o único commit sai com o gate completo verde |
| O `executor` não edita `tests/`, logo um contrato mal fixado trava o verde | A seção Contratos lista nomes, assinaturas e mensagens exatas, e caso um contrato esteja errado o `test-engineer` ajusta os testes e o plano é corrigido antes do verde |
| Os testes do esqueleto quebram na unidade seguinte, como uma checagem de "6 pacotes vazios" | Essa checagem não vira teste permanente e roda como comando único na Verificação |
| Rastro de rede sem relação com modelo (LangSmith) durante os testes | `LANGSMITH_TRACING=false` por fixture em `tests/conftest.py` (passo 3), além do `grep` de rede na Verificação |
| Ramos novos sem teste derrubam o piso de cobertura | O vermelho cobre EOF, linha em branco e uso inválido da CLI |
| O `pyrefly` reclama de tipos do `langgraph` | O executor ajusta a tipagem no código, e a configuração do `pyrefly` só muda se o erro vier da biblioteca, caso que vai ao critic da cadeira |
| O `executor` escorrega para escopo extra (env de provider, trace, mais comandos) | A seção Fora de escopo delimita, e o `code-simplifier` e o `/review-pr --normal` conferem contra ela |

## Fora de escopo

Adaptadores `anthropic` e `ollama`, seleção de provider por ambiente, prompt de sistema, trace, guard, tools, memória e checkpointer, comandos `eval` e `metrics`, `kit/` e `evals/`.

## Verificação

Tudo roda na worktree com os arquivos indexados (`git add -A`), já que sem isso o `prek --all-files` ignora arquivo novo.

### Gate pré-commit

```sh
git add -A
uv lock --check
uv sync --locked
uv run pytest -q --cov
uv run prek run --all-files
uv run prek run --all-files --hook-stage pre-push
uv run deptry .
printf 'oi\n' | uv run pet-helper chat; echo "exit=$?"
uv run pet-helper chat </dev/null; echo "exit=$?"
# AC3: nenhum teste de modelo coletado (saída esperada: 0 testes, código 5) e nenhum provider, rede ou socket
uv run pytest -m llm --collect-only -q; echo "exit=$?"
grep -rnE 'anthropic|ollama|requests|httpx|socket' src tests || echo "nothing found"
# AC1, checagem única: os 6 pacotes vazios têm só __init__.py, e ele está vazio
find src/pet_helper/guard src/pet_helper/evals src/pet_helper/knowledge src/pet_helper/orders src/pet_helper/catalog src/pet_helper/trace -type f -not -path '*/__pycache__/*' -not -name __init__.py
find src/pet_helper/guard src/pet_helper/evals src/pet_helper/knowledge src/pet_helper/orders src/pet_helper/catalog src/pet_helper/trace -name __init__.py -size +0
```

Esperado: `uv`, `prek` e `deptry` com código 0, os dois `pet-helper chat` com `exit=0` e sem traceback (o primeiro imprimindo `DEFAULT_REPLY`), `pytest -m llm` com 0 testes e `exit=5`, o `grep` imprimindo `nothing found` e os dois `find` sem saída.

### Checagens pós-commit (depois do commit único, antes do push)

```sh
uv run cz check --rev-range main..HEAD -l 80
git log main..HEAD --oneline
```

Esperado: `cz` com código 0 e `git log` com exatamente um commit.

Mapa de aceite: o AC1 é coberto pelo `pytest` (imports) e pelos `find`, o AC2 pelo `pytest` e pelo `pet-helper chat` real, o AC3 pelo `pytest --cov` e pelos dois comandos de ausência, o AC4 pelo `pytest`, o AC5 por `prek` e `deptry` (pré-commit) e pelo `cz` (pós-commit), o AC6 pelos `grep` do passo 5 e o AC7 por `wt list`, pelo `git log` (pós-commit) e pelo PR.

## ADR

**Decisão.** Entregar o esqueleto como uma fatia vertical fina, com um gateway formado por protocolo, `ModelResult` de um campo, `FakeGateway` e `default_gateway()` sobre as mensagens do `langchain-core`, um `Assistant` sobre um `StateGraph` de um nó sem checkpointer, a CLI `argparse` com `chat`, 6 pacotes vazios e um único commit ao fim da unidade.

**Fatores.** Seam do gateway estável para os adaptadores reais, superfície mínima de dependências e teste sem modelo dentro do gate atual.

**Alternativas consideradas.** Mensagens próprias no gateway (Opção B), função sem LangGraph, provider por variável de ambiente já agora, `typer`/`click` na CLI, `main(argv=None, gateway=None)` com injeção (rejeitada por ser um parâmetro que só o teste usaria, já que o `FakeGateway()` padrão de `default_gateway()` dá resposta determinística) e commit vermelho separado (rejeitado pelo dono, porque o commit fecha a unidade e não o passo do TDD).

**Por que esta.** Evita reabrir o contrato do gateway quando o primeiro adaptador real chegar, ao custo de uma dependência direta (`langchain-core`) que o `langgraph` já traz. O `tools` fica na assinatura de `complete` porque é a assinatura de `architecture.md`, e tirá-lo agora seria reabrir o seam depois. Já o `ModelResult` com um campo é o tipo de retorno estável do seam e só o gateway o constrói, ou seja, um campo novo não muda nenhum chamador. O `default_gateway()` mantém a escolha do provider dentro do gateway, como a arquitetura define.

**Consequências.** `langchain-core` e `langgraph` viram dependências diretas, o `session_id` é aceito e ignorado até existir memória, e a CLI fica com `argparse` até haver comandos que justifiquem outra coisa. Quando o primeiro adaptador real entrar, o `default_gateway()` passa a escolher por ambiente sem mudar a CLI, e a regra do fluxo sobre commit de TDD é reescrita nesta mesma unidade.

**Seguimentos.**

- Unidade do `trace`/`guard` com mascaramento, que acrescenta os campos de `ModelResult` que o trace lê.
- Unidade do primeiro adaptador real, em que o `default_gateway()` passa a selecionar por ambiente.
- Unidade do `trace`, que decide o comportamento fora dos testes, já que o `pet-helper chat` real herda o ambiente do dono (variáveis do LangSmith, por exemplo) e só os testes forçam `LANGSMITH_TRACING=false`.
- Unidade do prompt, que introduz a mensagem de sistema (hoje o gateway recebe só a `HumanMessage`).
- `kit/` e `evals/`, cada um como unidade própria, com spec e TDD (decisão do dono, 2026-10-29).
- As decisões de arquitetura desta unidade estão em [architecture.md](architecture.md), sem ADR novo.

## Questões em aberto

Resolvidas pela cadeira do dono em [architecture.md](architecture.md), decisões 2 e 5.
