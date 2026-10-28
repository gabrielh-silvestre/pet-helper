# Fluxo de desenvolvimento

Caminho de um trabalho, do pedido ao merge. A fronteira de quem decide está em [cadeira-do-dono.md](cadeira-do-dono.md); os requisitos do desafio, em [requisitos.md](../requisitos.md).

## Etapas

1. **Entrevista em camadas.** Por `/oh-my-claudecode:deep-interview --frontier`, respeitando as camadas de decisão.
   - A spec de trabalho fica em `.omc/specs/`, e é ela que os agentes leem; uma cópia passada pelo `/humanizar` vai para `docs/specs/<slug>/spec.md`, versionada e legível para a banca.
   - Com o dono: o objetivo da execução e as specs, behaviors, premissas e diretrizes que o sustentam.
   - Cada execução tem um único objetivo.
   - Cada behavior do assistente sai também como caso de eval: entrada, saída esperada e forma de medir.
   - Pergunta de arquitetura vai ao critic da cadeira; pergunta de código fica com o planner e o executor.
2. **Planejamento autônomo.** Por `/oh-my-claudecode:ralplan`.
   - O `ralplan` para no plano aprovado e nunca segue para `team` nem `ralph`: a etapa seguinte é sempre a cadeira do dono.
   - O plano de trabalho fica em `.omc/plans/`; uma cópia passada pelo `/humanizar` vai para `docs/specs/<slug>/plan.md`.
3. **Cadeira do dono.** Por `/owner-seat`: o critic decide a arquitetura que o plano exige e a registra em `docs/specs/<slug>/architecture.md`; o dono dá o aval no pré-PR.
4. **Entrega em loop de TDD.** Por `/oh-my-claudecode:ralph`, com as regras abaixo injetadas nas instruções dele.
   - Vermelho-verde por behavior: o `test-engineer` escreve os testes do behavior e confirma que falham pelo motivo certo; o `executor` os faz passar com o mínimo de código.
   - O `test-engineer` nunca é o `executor`: quem escreve o código não cria teste.
   - A cada `.py` editado, um hook roda o lint com correção automática e devolve ao agente só o que não corrigiu.
   - Toda vez que o `executor` termina, um hook roda a verificação completa (lint, formatação, tipos e a suíte sem modelo com cobertura) antes do `code-simplifier`; falha volta ao `executor` sem contar nas voltas do `code-simplifier`.
   - O `code-simplifier` roda ao fechar cada spec, quando todos os behaviors dela estão verdes, sobre o código de teste e de produção que entrou.
   - Reprovação do `code-simplifier` volta ao `executor`, os testes rodam de novo e o `code-simplifier` reavalia, no máximo 2 vezes; depois disso o impasse vai ao dono.
   - O loop segue spec a spec até a entrega final.
5. **Revisão local.** Por `/review-pr --normal`, apontando para a worktree, antes de existir PR; levanta achados nas severidades da tabela abaixo.
6. **Ajustes e PR.** O executor aplica os achados da revisão local e abre o PR em draft.
7. **Revisão em painel.** Por `/review-pr --panel <N>` no PR aberto, rodado por um agente novo e isolado numa tab nova do Herdr; os MINOR vão para uma issue de follow-up que aponta para o PR.
8. **Merge.** Só o dono faz o merge, como merge commit; o fluxo nunca faz merge sozinho.

O loop de melhoria do agente (evals, métricas e tracing guiando mudanças no assistente) está em [loop-melhoria.md](loop-melhoria.md).

## Branches

| Item | Regra |
|---|---|
| Nome | `<tipo>/<descrição-curta>`, minúsculas e hífens, tipo do Conventional Commits (`feat`, `fix`, `chore`, `docs`, `test`, `refactor`) |
| Base e destino | Parte de `main` e o PR volta para `main`, sem branch `dev` |
| Isolamento | Um trabalho = uma branch = uma worktree criada com `wt` |
| Vida | A branch morre no merge |
| Commits | Conventional Commits, só subject, até 80 caracteres |

## Severidades dos achados

| Severidade | Regra |
|---|---|
| BLOCKING | Corrigido obrigatoriamente, salvo refutação justificada |
| URGENT | Igual ao BLOCKING |
| NORMAL | O custo da correção é ponderado; a decisão registra "corrigir" ou "aceitar", com justificativa |
| MINOR | Não bloqueia; vai para a issue de follow-up |

## Gate de verificação

Duas camadas: a compartilhada vale para humano e agente; a de agentes é imposta por hook, nunca só por texto.

### Camada compartilhada

| Momento | O que roda | Teto |
|---|---|---|
| `pre-commit` | Só estático, sobre o que está em stage: formatação, lint e regras de segurança, checagem de tipos do projeto, lint de markdown, higiene de arquivos (fim de linha, espaços, TOML e YAML válidos) e lockfile do uv coerente | ~10 s |
| `commit-msg` | Conventional Commits, subject de até 80 caracteres, sem corpo | — |
| `pre-push` | A cada push: testes unitários e de integração sem modelo, com piso de 80% de cobertura em branches | ~60 s |
| CI | A cada push na `main` e em todo PR: o `pre-commit` sobre todos os arquivos, os testes com o piso, as mensagens de commit do PR e dependências não usadas ou faltando; execuções antigas do mesmo PR são canceladas | — |

- Os testes não rodam no `pre-commit`: o vermelho do TDD é commitado de propósito.
- Nada que chame o modelo real roda de forma automática: nem gancho do git, nem CI, nem agendamento.
- O projeto não usa teste de mutação.
- Gancho local pode ser pulado; o CI é a barreira que não depende da máquina de quem commita.

### Camada de agentes

- Lint com correção automática a cada `.py` editado.
- Verificação completa toda vez que o `executor` termina, o que inclui o fechamento de cada spec, antes do `code-simplifier`; um hook não enxerga onde a spec termina.
- Bloqueio de atalhos que pulam verificação (`--no-verify` e variáveis que desligam ganchos).
- Só o dono edita `docs/directives/estrategia.md`.
- O `executor` não altera os testes do `test-engineer`, nem por ferramenta de edição nem pelo shell.
- Os agentes que corrigem o assistente não leem os casos de eval reservados.

### Retorno ao agente

| Situação | O que o agente recebe |
|---|---|
| Sucesso | Nada |
| Falha | Só os erros, `arquivo:linha: problema. Como corrigir`, até 20, com a contagem do restante |
| Bloqueio | Uma frase com o motivo e o caminho permitido |
