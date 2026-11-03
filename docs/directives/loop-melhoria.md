# Loop de melhoria do agente

Como uma rodada de eval vira uma mudança aceita no assistente. Cada volta do loop é uma execução do [fluxo de desenvolvimento](fluxo-desenvolvimento.md); quem decide o quê segue a [cadeira do dono](cadeira-do-dono.md).

## Ciclo

1. **Medir.** Rodam as suítes de capacidade e de regressão; cada caso roda 3 vezes; resultados e traces ficam salvos. Os casos reservados rodam só onde a seção "Casos reservados" manda.
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
- Mover um caso entre suítes é mudar o campo `suite` do arquivo do caso; o CSV da rodada é gerado e anotado por agentes, nunca editado à mão pelo dono.
- Visualizador HTML local só entra se ler as conversas no terminal fizer falta.

## Aceitação

- Um caso passa quando passa nas 3 execuções; passar em 1 ou 2 é instável, não passou.
- A métrica principal da rodada é pass^3.
- Caso decisivo é o caso de regressão que estava em 3 de 3 na rodada de referência (a baseline ou a última rodada aceita) e caiu abaixo disso, e o caso do alvo que chegou a 3 de 3.
- Antes do veredito, cada caso decisivo roda de novo 10 vezes, dentro do teto de custo. Só essas 10 execuções decidem; o pass^3 e o k/3 da rodada continuam calculados nas 3 execuções e aparecem à parte.
- Execução `unable` ou `error` de caso decisivo é refeita até haver 10 execuções válidas. Se o teto de custo interromper antes, o caso fica sem veredito: caso de regressão sem veredito impede a aceitação, e caso do alvo sem veredito não conta como o caso que aceita.
- Uma mudança é rejeitada se algum caso de regressão decisivo falha em 2 ou mais das 10 execuções.
- Uma mudança é aceita só se nenhum caso de regressão impediu a aceitação e ao menos um caso do alvo passa em 9 ou mais das 10 execuções.
- Caso de regressão que já estava abaixo de 3 de 3 na referência aparece no relatório como instável e não decide.
- O relatório mostra, ao lado do pass^3 da suíte, o intervalo de Wilson de 95% (cada caso é uma observação) e a diferença pareada contra a rodada anterior, dada como o número de casos que ganharam e o de casos que perderam o 3 de 3. O intervalo informa e não decide: só os casos decisivos decidem a aceitação.
- Execução `unable` (conversa fora do roteiro ou juiz sem condição de avaliar) não é passa nem falha: aparece no relatório e fica fora das categorias de falha, e caso com execução `unable` não passa em 3 de 3. A causa é conferida no passo "Conferir o avaliador".
- Hesitação ou resposta sem conclusão numa regra calculável (RN01 a RN04) é falha, nunca `unable`: como `unable` ela sairia das categorias de falha e dos checks que falharam. `unable` fica para a ambiguidade do próprio avaliador, com prefixo próprio, e `fail` tem precedência sobre `unable` na mesma execução. Rótulo próprio em `fail` não serve para ruído do avaliador, porque `fail` é execução válida, não é refeita e rejeita um candidato correto.
- Check de texto (`expected_value`, `must_include`, `must_not_include`) muda só por adição, caso a caso: toda entrada positiva nova ganha o gêmeo negado em `must_not_include`, nenhuma entrada de `must_not_include` pode ser trecho da conclusão correta, e cada lista tem teste sem modelo com respostas reais e respostas erradas escritas à mão. Os tipos de check não mudam de semântica, porque os casos reservados usam os mesmos tipos e ninguém pode ler o efeito neles.
- O relatório mostra k/3 por caso.
- A execução com uma só volta por caso é fumaça: não conta como rodada nem decide aceitação.
- Caso de capacidade que passa em 3 de 3 em duas rodadas seguidas que contam, nas quais ele rodou, migra para a suíte de regressão. Suíte em 100% só vale como evidência de regressão e pede casos mais difíceis.

## Execução e custo

- Nada que chame o modelo real roda de forma automática: nem gancho do git, nem CI, nem agendamento.
- Toda rodada é disparada à mão.
- Antes de rodar, o comando mostra a estimativa de tokens e custo e pede confirmação.
- Cada comando pago (rodada de eval, repetição de caso decisivo, re-julgamento, validação do juiz, fumaça) mostra a própria estimativa e para antes de passar de US$ 5, salvando o que já tiver; com um modelo sem preço conhecido o comando começa e para na primeira chamada, salvando o que já tiver.
- Tokens e custo reais de cada caso ficam no arquivo de resultados.

## Correção

- Tudo o que é calculável é conferido por assert no código: citação, número do pedido, RN01 a RN04, recusa de pedido alheio.
- O juiz por modelo só avalia o que não é calculável: tom, julgamento de escalonamento e acerto do "não sei".
- O veredito do juiz é passa ou falha, com uma crítica curta escrita antes do veredito.
- Cada critério tem o seu juiz, com a opção "não sei avaliar".
- O juiz é validado sobre as conversas da baseline antes de qualquer veredito dele contar: o dono rotula às cegas cerca de 50 itens por critério, com as duas classes representadas, completando com falhas plantadas por modelos de texto determinísticos e declaradas como plantadas.
- Os itens se dividem por caso em dev, para ajustar o prompt e tirar exemplos, e teste, lido uma só vez com o prompt congelado.
- Antes de ler o teste, o dono escreve no relatório da unidade o piso que aceita para o limite inferior do intervalo de Wilson de TPR e de TNR, por critério; sem piso escrito, o juiz não está validado.
- A concordância no teste é medida por TPR e TNR por critério, com intervalo de Wilson e contagens brutas, naturais e plantadas à parte; "não sei avaliar" conta como cobertura, fora do TPR e do TNR.
- O dono pode parar com menos rótulos; o relatório mostra o intervalo mais largo. Dez a quinze rótulos servem só de fumaça.
- A validação vale para o par modelo do juiz e prompt congelado, identificado por um hash que vai em cada veredito e na rodada. Juiz sem validação não decide aceitação; mudar o modelo ou o prompt exige nova validação.

## Casos reservados

- Cerca de 5 dos casos de eval são reservados e cobrem RN01 a RN04, "não sei" e um caso adversarial.
- Os agentes que corrigem o assistente não leem os casos reservados: um hook bloqueia os caminhos diretos (leitura, busca, listagem, `git grep`, `git show`, `git log -p` e `git diff` entre revisões sem pathspec, ferramentas hospedadas com caminho). Residual aceito: a pasta está versionada, então o histórico do git (o PR que a criou e o merge commit de `main`) continua alcançável por ferramentas hospedadas que não recebem caminho; o compromisso é de processo, não de cofre.
- Os casos reservados rodam no passo de aceitar ou rejeitar e uma vez na baseline, sempre por script e sem leitura por agente. A baseline não os consome, porque nenhuma correção foi feita olhando para eles, e vira a referência da primeira aceitação.
- Falha de caso reservado na baseline não o consome nem o troca; a troca da última linha desta seção vale só para falha revelada no passo de aceitar ou rejeitar.
- Caso reservado que estava em 3 de 3 na baseline e cai abaixo disso é caso de regressão decisivo, com as 10 execuções sob o mesmo sigilo.
- Resultados reservados não são versionados nem analisados por agentes; a referência guardada é o k/3 por caso e o SHA medido.
- Os casos reservados nascem de um fluxo isolado: um agente que lê só os requisitos, o glossário e os dados do kit, sem código, prompts nem a suíte normal.
- O dono aprova cada caso reservado antes de ele entrar.
- Um caso reservado que revela uma falha passa para a suíte normal e é substituído por um novo.
