# Cadeira do dono

Quem decide o quê neste projeto e como a decisão fica registrada. Vale para a sessão principal e para todo subagente. A mecânica de lançar o critic e de registrar no hexlog chega com a `hexlog-setup`; até lá, estas regras orientam o trabalho sem registro.

## Camadas de decisão

- Produto e estratégia são do dono: escopo, comportamento do assistente, regras de negócio, prioridades e o que fica de fora.
- A decisão do dono é gravada com a origem `human`.
- Arquitetura é do critic da cadeira: camadas MSC, modelo e runtime, stack de observabilidade, estratégia de eval.
- O dono dá sugestões leves de arquitetura; o critic as pondera como insumo, não como ordem.
- A decisão do critic é gravada com a origem `simulated` e passa a `simulated-approved` quando o dono a aprova no fim da M1.
- Exceção a produto: achado de revisão que vira decisão de produto dentro da M2 ou da M3 vai ao critic da cadeira, que decide com base em [estrategia.md](estrategia.md) e nos requisitos; a decisão é gravada com a origem `simulated`, aplicada e registrada no PR, e o dono a revê no merge.
- Código é do executor e do planner: arquivos dentro das camadas, bibliotecas auxiliares, nomes, detalhes de teste.
- A decisão de código é uma `decision` comum, sem passar pela cadeira.

## Classe da pergunta

- É crítica a escolha que altera, remove ou contraria uma diretriz ou premissa já registrada.
- Toda escolha crítica vai à cadeira e exige o aval do dono antes de valer.
- Só o dono emenda uma premissa de [estrategia.md](estrategia.md).
- Regra nova que não altera nenhuma existente é comum.

## Ocupante

- O ocupante da cadeira é o `critic` durável: um por trabalho, vivo até o fim dele.
- O critic responde a pergunta que recebe e registra o que respondeu; nunca a devolve a quem o lançou.
- Sem o critic disponível, a pergunta de arquitetura vai ao dono.

## Exceção de modelo

- O critic da cadeira roda em `opus`; é exceção autorizada pelo dono à regra global de subagentes em `sonnet` ou `haiku` e vale só neste repositório.
- Os subagentes e jurados que o critic lança seguem em `sonnet` ou `haiku`.
