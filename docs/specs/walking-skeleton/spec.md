# Esqueleto base (primeira entrega de código)

Status: aprovada e entregue em `feat/walking-skeleton`. Entrevista de 29/10/2026, com ambiguidade final de 11%.

## Objetivo

É o menor caminho de ponta a ponta do assistente, no qual o cliente digita no `pet-helper chat` e o turno passa por `Assistant.turn`, entrando num grafo LangGraph de um nó só que chama o gateway `fake`, até a resposta roteirizada aparecer no terminal.
Além disso, os 9 pacotes da [architecture.md](../../architecture.md) passam a existir no disco, dessa forma as próximas unidades já encontram o mapa fixo.

## O que entra

- Os 9 pacotes em `src/pet_helper/`, sendo que só `cli`, `conversation` e `gateway` têm código, já os outros 6 são pacotes vazios, sem stub nem assinatura.
- O gateway só com o adaptador `fake`, sem nenhuma chamada a modelo real.
- O grafo com um único nó de agente e sem memória entre turnos, ou seja, cada turno é independente.
- A interface da architecture.md, `Assistant.turn(session_id, text) -> Reply(text, sources, escalation)`.
- A CLI só com o comando `chat`.
- Só as dependências que o código importa, o entry point `pet-helper` e o README com o setup em 2 comandos.

## O que fica de fora

- `kit/` e `evals/`, que viram unidades próprias.
- Os adaptadores Anthropic e Ollama, o fallback, o compose do Ollama e o contrato de variáveis de ambiente.
- Memória da conversa, tools, guard, trace, políticas, pedidos e catálogo.
- Os comandos `eval` e `metrics`.

## Critério de aceite

1. `src/pet_helper/` tem os 9 pacotes, e os 6 fora do caminho estão vazios.
2. `uv sync` e `uv run pet-helper chat` abrem o loop, respondem cada linha com o fake e saem com Ctrl-D.
3. O pytest cobre o fluxo por `Assistant.turn` com o fake, sem modelo, com cobertura de branch ≥ 80% em `src/`.
4. O grafo tem exatamente um nó de agente e nenhum checkpointer.
5. `uv run prek run --all-files` e o pre-push passam, com o deptry incluído.
6. O README traz o setup em 2 comandos.
7. A entrega chega como PR, saído de uma branch e de uma worktree `wt` próprias.
