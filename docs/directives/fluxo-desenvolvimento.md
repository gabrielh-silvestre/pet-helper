# Fluxo de desenvolvimento

Caminho de um trabalho, do pedido ao merge. A fronteira de quem decide está em [cadeira-do-dono.md](cadeira-do-dono.md); os requisitos do desafio, em [requisitos.md](../requisitos.md).

## Macroetapas

As 8 etapas abaixo rodam em três macroetapas encadeadas, cada uma uma skill do projeto. O dono entra só na entrevista, no aval do fim da M1, nos passos pagos que o plano aprovado lista e no merge.

| Macro | Comando | Etapas | Termina em |
|---|---|---|---|
| M1 pré-código | `/pre-code <objetivo>` | 1 a 3 | Pergunta ao dono: aprovar, pedir ajuste ou parar. Aprovar dispara a M2 |
| M2 entrega | `/deliver <slug>` | 4 a 6 | PR em draft; abre a tab `<slug>-panel` do Herdr e dispara a M3, salvo a pausa para os passos do dono (retome com `/deliver <slug>`) |
| M3 painel | `/pr-panel <slug> <PR> <modo>` | 7 | Veredito postado sozinho no head final; o merge segue com o dono |

- Cada macro pode ser chamada à mão com o slug; é assim que se retoma uma unidade parada.
- Impasse (2 voltas esgotadas, hook barrando a mesma coisa, `ralph` sem convergir, critic sem decisão) ou pausa planejada para os passos do dono, que não é falha: a macro para e registra `impasse` no hexlog com fase, motivo e progresso, sem notificação.
- Passos do dono: quando `docs/specs/<slug>/plan.md` tem a seção `## Passos do dono`, a M2 para depois do PR em draft e antes da etapa 7, registra `impasse` com o motivo `owner steps pending`, sem mudar a fase, e mostra os comandos da seção. Os passos rodam na branch, cada comando pago no terminal do dono; a sessão principal prepara, lê os resultados visíveis e commita. Casos reservados gerados nesses passos chegam à `main` por um PR próprio, que o dono mergeia depois de aprovar cada caso, e entram na branch por merge da `main`, para que o diff que o painel lê não os mostre. Quando o dono avisa que terminou e o head está enviado, `/deliver <slug>` retoma na etapa 7.
- Achado de revisão que vira decisão de produto vai ao critic da cadeira, que decide; a macro aplica e registra no PR e no hexlog, e o dono revê no merge.
- Depois das correções de cada revisão, o `code-simplifier` roda de novo sobre o trecho corrigido, só relatando; reprovação volta ao `executor`, no máximo 2 vezes.

## Etapas

1. **Entrevista em camadas.** Por `/oh-my-claudecode:deep-interview --frontier`, respeitando as camadas de decisão.
   - A spec de trabalho fica em `.omc/specs/`, e é ela que os agentes leem; uma cópia passada pelo `/humanizar` vai para `docs/specs/<slug>/spec.md`, versionada e legível para a banca.
   - Com o dono: o objetivo da execução e as specs, behaviors, premissas e diretrizes que o sustentam.
   - Cada execução tem um único objetivo.
   - Cada behavior do assistente sai também como caso de eval: entrada, saída esperada e forma de medir.
   - Pergunta de arquitetura vai ao critic da cadeira; pergunta de código fica com o planner e o executor.
   - Pesquisa de ferramentas existentes, para escrever do zero só o mínimo: pontual quando a unidade usa bibliotecas que a [architecture.md](../architecture.md) já escolheu (conferir o que elas já entregam pronto); ampla quando a unidade traz uma capacidade sem biblioteca escolhida. O agente decide qual das duas e diz por quê; o achado entra na spec.
2. **Planejamento autônomo.** Por `/oh-my-claudecode:ralplan`.
   - O planner refaz a pesquisa pontual sobre o que o plano vai construir; toda peça escrita do zero diz no plano qual ferramenta existente foi considerada e por que não serve.
   - O `ralplan` para no plano aprovado e nunca segue para `team` nem `ralph`: a etapa seguinte é sempre a cadeira do dono.
   - O plano de trabalho fica em `.omc/plans/`; uma cópia passada pelo `/humanizar` vai para `docs/specs/<slug>/plan.md`.
3. **Cadeira do dono.** Por `/owner-seat`: o critic durável, numa tab do Herdr, decide a arquitetura que o plano exige e a registra em `docs/specs/<slug>/architecture.md` e no hexlog (`gap`, `premise`, `evidence`); o dono dá o aval no fim da M1, junto com o plano, e o critic vive até o fim da M3. As regras estão em [cadeira-do-dono.md](cadeira-do-dono.md).
4. **Entrega em loop de TDD.** Por `/oh-my-claudecode:ralph`, com as regras abaixo injetadas nas instruções dele.
   - Vermelho-verde por behavior: o `test-engineer` escreve os testes do behavior e confirma que falham pelo motivo certo; o `executor` os faz passar com o mínimo de código.
   - O `test-engineer` nunca é o `executor`: quem escreve o código não cria teste.
   - A cada `.py` editado, um hook roda o lint com correção automática e devolve ao agente só o que não corrigiu.
   - Toda vez que o `executor` termina, um hook roda a verificação completa (lint, formatação, tipos e a suíte sem modelo com cobertura) antes do `code-simplifier`; falha volta ao `executor` sem contar nas voltas do `code-simplifier`.
   - O `code-simplifier` roda ao fechar cada spec, quando todos os behaviors dela estão verdes, sobre o código de teste e de produção que entrou.
   - Reprovação do `code-simplifier` volta ao `executor`, os testes rodam de novo e o `code-simplifier` reavalia, no máximo 2 vezes; depois disso o impasse vai ao dono.
   - O loop segue spec a spec até a entrega final.
5. **Revisão local.** Por `/review-pr --normal`, apontando para a worktree, antes de existir PR; levanta achados nas severidades da tabela abaixo.
6. **Ajustes e PR.** O executor aplica todos os achados da revisão local, MINOR inclusive, e a unidade vira um commit e um PR em draft.
7. **Revisão do PR.** Por `/review-pr --auto` no PR aberto, rodado por um agente novo e isolado numa tab nova do Herdr, dono exclusivo da worktree até terminar.
   - Modo `--panel --normal` se a entrega toca regra de negócio (RN01-RN04), acesso a Order ou identidade do Customer ([ADR 0001](../adr/0001-order-access-by-cpf-and-order-number.md)), o prompt do modelo, ou mais de 600 linhas de produção; senão `--normal`. Na dúvida, painel. A M2 escolhe e registra o motivo.
   - BLOCKING, URGENT e NORMAL são corrigidos sozinhos num commit `fix:` único; os MINOR vão para uma issue de follow-up que aponta para o PR.
   - Antes do veredito, a M3 confere `git diff <SHA medido>..HEAD -- src/`, com o SHA do `round.json` da baseline; se não estiver vazio, o commit de correções põe no README e no corpo do PR a linha "baseline medida em <sha>, antes de N commits que mudaram `src/`". Rodar a baseline de novo é decisão do dono e pede veredito novo no head que resultar.
   - O veredito sai por `pr-verdict --auto`, preso ao SHA do último push.
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
| NORMAL | Corrigido pela cadeia, salvo refutação justificada |
| MINOR | Corrigido na revisão local; na revisão do PR não bloqueia e vai para a issue de follow-up |

## Gate de verificação

Duas camadas: a compartilhada vale para humano e agente; a de agentes é imposta por hook, nunca só por texto.

### Camada compartilhada

| Momento | O que roda | Teto |
|---|---|---|
| `pre-commit` | Só estático, sobre o que está em stage: formatação, lint e regras de segurança, checagem de tipos do projeto, lint de markdown, higiene de arquivos (fim de linha, espaços, TOML e YAML válidos) e lockfile do uv coerente | ~10 s |
| `commit-msg` | Conventional Commits, subject de até 80 caracteres, sem corpo | — |
| `pre-push` | A cada push: testes unitários e de integração sem modelo, com piso de 80% de cobertura em branches | ~60 s |
| CI | A cada push na `main` e em todo PR: o `pre-commit` sobre todos os arquivos, os testes com o piso, as mensagens de commit do PR e dependências não usadas ou faltando; execuções antigas do mesmo PR são canceladas | — |

- Os testes não rodam no `pre-commit`, já que o commit fecha uma unidade de trabalho (a spec) e nunca um passo do TDD: o vermelho fica fora do histórico, e a saída dele vai para o hexlog e o PR.
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
- Guardas das macroetapas, todas em `.claude/hooks/flow.sh` sobre o estado da unidade em `.git/pet-helper-flow.json`:
  - o registro no hexlog só aceita a fase que a macro declarou (ou `impasse`), nunca uma fase deduzida do comando;
  - depois do handoff para a M3, só a sessão dela escreve na worktree;
  - o PRD do `ralph` precisa de `userStories` e de `feedbackCommands` silenciosos no sucesso;
  - o veredito só é postado com o head da worktree já enviado e preso a esse SHA;
  - `ralph` e `team` só rodam depois do aval do dono;
  - o aval só é gravado com spec, plano, arquitetura, seção `## Ferramentas existentes` no plano e as cópias originais em `.omc/`.
- Todo hook usa `onFailure: "block"`: hook que não consegue rodar barra em vez de deixar passar.
- Uma unidade em voo por clone: `/pre-code` de outro objetivo recusa até a M3 da unidade atual terminar.

### Retorno ao agente

| Situação | O que o agente recebe |
|---|---|
| Sucesso | Nada |
| Falha | Só os erros, `arquivo:linha: problema. Como corrigir`, até 20, com a contagem do restante |
| Bloqueio | Uma frase com o motivo e o caminho permitido |
