# Loop de melhoria do agente

Como uma rodada de eval vira uma mudança aceita no assistente. Cada volta do loop é uma execução do [fluxo de desenvolvimento](fluxo-desenvolvimento.md); quem decide o quê segue a [cadeira do dono](cadeira-do-dono.md).

## Ciclo

1. **Medir.** Rodam as suítes de capacidade, de regressão e os casos reservados; cada caso roda 3 vezes; resultados e traces ficam salvos.
2. **Conferir o avaliador.** Antes de culpar o assistente, a conversa que falhou é lida; se o avaliador errou, corrige-se o eval primeiro.
3. **Diagnosticar.** Cada falha recebe uma nota livre sobre a primeira coisa que deu errado e uma categoria, separando busca de geração; as categorias nascem dos dados, não de uma lista prévia.
4. **Escolher.** A categoria de maior frequência vezes severidade vira o objetivo da próxima execução do fluxo.
5. **Corrigir.** Pelo fluxo de desenvolvimento; toda falha nova encontrada vira caso de eval.
6. **Aceitar ou rejeitar.** Pela regra de aceitação abaixo.

## Papéis

- O critic da cadeira anota cada falha e propõe a categoria.
- O dono confirma as categorias, lê uma amostra de cerca de 5 conversas e escolhe o alvo.
- O dono pode assumir a anotação inteira quando quiser.

## Análise de erro

- A fonte da verdade é um CSV por rodada, uma linha por caso, com nota da primeira falha, categoria, rótulo humano e suíte.
- A contagem por categoria sai de um script sobre o CSV.
- Mover um caso entre suítes é mudar a coluna de suíte, à mão.
- Visualizador HTML local só entra se ler as conversas no terminal fizer falta.

## Aceitação

- Um caso passa quando passa nas 3 execuções; passar em 1 ou 2 é instável, não passou.
- A métrica principal da rodada é pass^3.
- Uma mudança é aceita só se nenhum caso de regressão deixa de passar em 3 de 3.
- Uma mudança é aceita só se ao menos um caso do alvo passa a 3 de 3.
- O relatório mostra k/3 por caso.
- Caso que passa em 3 de 3 em rodadas seguidas pode migrar da suíte de capacidade para a de regressão.

## Execução e custo

- Nada que chame o modelo real roda de forma automática: nem gancho do git, nem CI, nem agendamento.
- Toda rodada é disparada à mão.
- Antes de rodar, o comando mostra a estimativa de tokens e custo e pede confirmação.
- Uma rodada para ao passar de US$ 5 e salva o que já tiver.
- Tokens e custo reais de cada caso ficam no arquivo de resultados.

## Correção

- Tudo o que é calculável é conferido por assert no código: citação, número do pedido, RN01 a RN04, recusa de pedido alheio.
- O juiz por modelo só avalia o que não é calculável: tom, julgamento de escalonamento e acerto do "não sei".
- O veredito do juiz é passa ou falha, com uma crítica curta escrita antes do veredito.
- Cada critério tem o seu juiz, com a opção "não sei avaliar".
- Antes da primeira rodada que conta, o dono rotula de 10 a 15 conversas sem ver o veredito do juiz, e a concordância é medida por TPR e TNR.
- Validado o juiz, o prompt dele fica congelado; mudá-lo exige nova validação.

## Casos reservados

- Cerca de 5 dos casos de eval são reservados e cobrem RN01 a RN04, "não sei" e um caso adversarial.
- Os agentes que corrigem o assistente não leem os casos reservados; um hook bloqueia a leitura.
- Os casos reservados rodam só no passo de aceitar ou rejeitar.
- Os casos reservados nascem de um fluxo isolado: um agente que lê só os requisitos, o glossário e os dados do kit, sem código, prompts nem a suíte normal.
- O dono aprova cada caso reservado antes de ele entrar.
- Um caso reservado que revela uma falha passa para a suíte normal e é substituído por um novo.
