# Fluxo de desenvolvimento

Caminho de um trabalho, do pedido ao merge. A fronteira de quem decide está em [cadeira-do-dono.md](cadeira-do-dono.md); os requisitos do desafio, em [requisitos.md](../requisitos.md).

## Etapas

1. **Entrevista em camadas.** Uma pergunta por turno, respeitando as camadas de decisão.
   - Com o dono: o objetivo da execução e as specs, behaviors, premissas e diretrizes que o sustentam.
   - Cada execução tem um único objetivo.
   - Cada behavior do assistente sai também como caso de eval: entrada, saída esperada e forma de medir.
   - Pergunta de arquitetura vai ao critic da cadeira; pergunta de código fica com o planner e o executor.
2. **Planejamento autônomo.**
3. **Cadeira do dono.** O critic decide a arquitetura que o plano exige; o dono dá o aval no pré-PR.
4. **Entrega em loop de TDD.**
   - Vermelho-verde por behavior: o `test-engineer` escreve os testes do behavior e confirma que falham pelo motivo certo; o `executor` os faz passar com o mínimo de código.
   - O `test-engineer` nunca é o `executor`: quem escreve o código não cria teste.
   - O `code-simplifier` roda ao fechar cada spec, quando todos os behaviors dela estão verdes, sobre o código de teste e de produção que entrou.
   - Reprovação do `code-simplifier` volta ao `executor`, os testes rodam de novo e o `code-simplifier` reavalia, no máximo 2 vezes; depois disso o impasse vai ao dono.
   - O loop segue spec a spec até a entrega final.
5. **Revisão local.** Levanta achados nas severidades da tabela abaixo.
6. **Ajustes e PR.** O executor aplica os achados da revisão local e abre o PR em draft.
7. **Revisão em painel.** No PR aberto; os MINOR vão para uma issue de follow-up que aponta para o PR.
8. **Merge.** Só o dono faz o merge, como merge commit; o fluxo nunca faz merge sozinho.

O loop de melhoria do agente (evals, métricas e tracing guiando mudanças no assistente) é outro fluxo, definido à parte.

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

As camadas do gate (formatação, lint, testes, mutação) e seus comandos são definidos na entrevista do harness.
