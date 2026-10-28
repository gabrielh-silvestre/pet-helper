# Estratégia

Premissas atemporais do pet-helper, em linguagem de produto: o que não pode quebrar e por quê. Cada decisão registrada deve se apoiar em pelo menos uma delas, ou declarar que nenhuma se aplica. Só mudam com validação do dono; o agente nunca as edita. O detalhe técnico mora no doc ligado em cada linha.

## Desafio e entrega

- `aderencia-ao-desafio`: O projeto segue as instruções do desafio técnico o mais estritamente possível; quando o desafio e uma preferência nossa divergem, vale o desafio. Ver: [requisitos.md](../requisitos.md).
- `prazo-de-entrega`: A entrega acontece até 03/11/2026; o que não couber no prazo fica registrado no README como trabalho futuro, não como pendência escondida. Ver: [requisitos.md](../requisitos.md).
- `avaliar-sem-pagar`: Quem avalia sobe e roda o projeto sem chave paga: a Anthropic é o provedor principal e um Llama em Docker é o caminho sem custo. Ver: [requisitos.md](../requisitos.md).
- `enunciado-fora-do-git`: O enunciado do desafio é confidencial e nunca entra no repositório; o repositório fica privado até o dia da entrega. Ver: [requisitos.md](../requisitos.md).

## Foco do produto

- `simples-e-medido`: Um núcleo mínimo e bem medido vale mais que um assistente amplo sem medição; o esforço vai para a melhoria contínua guiada por eval, métricas e tracing. Ver: [loop-melhoria.md](loop-melhoria.md).
- `comportamento-nasce-como-eval`: Todo comportamento esperado do assistente nasce como caso de eval antes de existir código. Ver: [fluxo-desenvolvimento.md](fluxo-desenvolvimento.md).
- `qualidade-vem-do-sistema`: O assistente roda num modelo fraco de propósito: a qualidade vem do sistema e é provada pela eval, não pela força do modelo. Ver: [loop-melhoria.md](loop-melhoria.md).

## Confiança do cliente

- `nao-inventa`: O assistente nunca inventa regra, produto ou dado de pedido; o que não está nas políticas, no catálogo ou no pedido vira "não sei" ou escalonamento. Ver: [GLOSSARY.md](../../GLOSSARY.md).
- `pedido-so-do-dono`: Um pedido só aparece para quem informa o CPF do dono e o número do pedido, e a resposta nunca revela se o pedido de outra pessoa existe. Ver: [ADR 0001](../adr/0001-order-access-by-cpf-and-order-number.md).
- `dado-pessoal-mascarado`: Nenhum dado pessoal, como CPF ou e-mail, aparece sem máscara em log ou trace. Ver: [requisitos.md](../requisitos.md).

## Custo e governança

- `modelo-real-so-a-mao`: Nada que chame o modelo real roda de forma automática; toda rodada é disparada à mão, com estimativa antes e teto de custo. Ver: [loop-melhoria.md](loop-melhoria.md).
- `quem-decide`: O dono decide produto e estratégia; a arquitetura é decidida pelo critic da cadeira, com aval do dono, e o código pelo executor. Ver: [cadeira-do-dono.md](cadeira-do-dono.md).
