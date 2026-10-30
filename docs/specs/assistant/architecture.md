# Arquitetura: assistente de atendimento

Decisões da cadeira do dono para a unidade, tomadas sobre o [plan.md](plan.md) e a [spec.md](spec.md), com base na [architecture.md](../../architecture.md), nos ADRs [0001](../../adr/0001-order-access-by-cpf-and-order-number.md) e [0002](../../adr/0002-business-rules-in-policies-facts-from-tools.md), no [GLOSSARY.md](../../../GLOSSARY.md) e na [cadeira-do-dono.md](../../directives/cadeira-do-dono.md). As quatro perguntas foram classificadas como `common` e respondidas pelo critic da cadeira com base na regra `architecture-baseline`. Nenhuma decisão contradiz diretriz, premissa ou ADR, por isso nenhuma vira ADR, porém a decisão 4 acrescenta uma variável ao contrato da §1 da arquitetura. As quatro ajustam o plano, e os ajustes estão listados no fim.

## 1. Forma do grafo da conversa

### Escolha

O grafo fixado na US-7a é a arquitetura do módulo `conversation`. Os nós são `input_gate` (sessão encerrada nunca chama o modelo), `input_guard`, `agent`, `tools` (`ToolNode` com tools puras em `content_and_artifact`), `record` (único escritor do estado depois das tools), `output_guard` e `handoff`. As arestas são `agent -> tools | output_guard | handoff`, `tools -> record -> agent`, `output_guard -> agent` (a regeneração única) e `handoff -> END`. O estado leva `messages`, `user_text` (zerado depois da máscara), `session_cpf` (o primeiro vence), `failures`, `retrieved`, `escalation` (gravado uma única vez), `ended` e os campos por turno `tool_rounds`, `regen_used`, `steps_counted` e `injection_suspected`, zerados no `input_gate`.

### Motivo

É um refinamento da §4 e não um desvio, já que cada nó novo faz algo que a coluna "Hides" da linha `conversation` já lista: o `input_gate` cumpre o fim de sessão do escalonamento, o `record` guarda o teto do loop, o contador de Repeated Failure e o estado do escalonamento, a aresta `output_guard -> agent` é a regeneração única e o `handoff` é o escalonamento. O caminho vivo (guard de entrada, agente e tools em loop, guard de saída), o `Assistant.turn`/`Reply` e o `InMemorySaver` com `thread_id = session_id` não mudam. A identidade fica no estado (ADR 0001), as tools seguem puras devolvendo fatos (ADR 0002) e o texto cru é apagado depois da máscara (`dado-pessoal-mascarado`).

### Alternativas

- Os 4 nós literais, com escalonamento, teto, regeneração e fim de sessão dentro do `agent`: rejeitada, porque esconde o desvio de fluxo, impede afirmar a topologia em teste e, sem um escritor único, as escritas paralelas do `ToolNode` levantam `InvalidUpdateError`.
- `create_react_agent` com hooks antes e depois do modelo: já rejeitada na §4.
- Subgrafos por fase: rejeitada por `simples-e-medido`.

`origin: simulated-approved` (premissa `docs-specs-assistant:01a18cd7-637c-7537-8e45-7d067f60ebf1`)

## 2. Modelo de falhas

### Escolha

Adotado o modelo de falhas do plano. O fallback da Anthropic para o Ollama é um `try/except` no adapter, só em erro de transporte (incluindo o 529 `OverloadedError`), nunca em 4xx nem em recusa, e `PET_HELPER_FALLBACK=none` o desliga, com padrão `ollama` no chat. Viram `repeated_failure` a mesma etapa falhando em três turnos seguidos do cliente (um incremento por etapa por turno, com o sucesso zerando), a recusa, os dois provedores fora do ar (`ModelUnavailable`), o teto do loop (`tool_rounds` no estado, com `recursion_limit` só como rede de segurança) e o guard de saída ainda inválido depois da regeneração. O 400/401 vira `ModelConfigError` e o CLI sai com erro de uma linha.
Além disso, o estado da US-7a ganha uma causa do escalonamento gravada uma única vez junto com o motivo (por exemplo `model_unavailable`, `refusal`, `loop_cap`, `step:<etapa>`, `output_guard`, com os nomes a cargo do executor), afirmada nas US-9 e US-10, sem mudar o `Reply`.

### Motivo

O mapeamento segue o GLOSSARY: três falhas seguidas da mesma etapa ou qualquer falha irrecuperável, e o "não sei" sobre um Uncovered Topic nunca conta. O fallback segue a §1 exatamente, e o `try/except` no lugar do `with_fallbacks` é escolha de código que entrega as marcas direto e mantém os blocos de thinking longe do Ollama. Uma chave inválida é defeito de operação, não falha de conversa, e escalar a esconderia do avaliador. Já a causa existe porque a §1 e a §5 pedem fallback desligado nas rodadas de eval e contam erro de infraestrutura como `error`. Sem ela, numa queda o `esc-repeated-failure` passaria por engano e os outros casos contariam como `fail`.

### Alternativas

- `ModelUnavailable` saindo do `turn`: rejeitada, porque o cliente do chat precisa do handoff e o GLOSSARY trata o caso como falha irrecuperável.
- `ModelConfigError` virando `repeated_failure`: rejeitada, porque esconde a chave inválida.
- `with_fallbacks`: rejeitada, como no plano.
- `recursion_limit` como único teto: rejeitada, porque o superstep é uma unidade grossa e o erro escapa do grafo.

`origin: simulated-approved` (premissa `docs-specs-assistant:01a18cd8-b82c-7040-9ad7-ec26f8f41e7f`)

## 3. Contrato de citação e handoff

### Escolha

O modelo cita em uma linha `Fonte: <título da política>`. O guard extrai essas linhas, normaliza (acento, ênfase de markdown, bullet, pontuação e parêntese finais) e aponta todo título citado que não esteja entre os recuperados na sessão, sendo título desconhecido contado como não recuperado. O `check_reply` recebe títulos e mantém a assinatura da §4, o `Reply.sources` leva os nomes de arquivo, mapeados dos hits pelo `conversation`, e os SKUs presentes nas mensagens do próprio cliente ficam isentos, filtrados no nó `output_guard`. No escalonamento o texto do modelo é descartado e a resposta é um template único do backend, em pt-BR e sem o motivo.

### Motivo

O título é o nome que o modelo lê e cita, e o nome de arquivo é o id estável para a eval. Uma linha fixa é um contrato determinístico para um modelo fraco (`qualidade-vem-do-sistema`), e contar título desconhecido como não recuperado aplica `nao-inventa` às fontes. Os 8 títulos H1 das políticas são distintos depois da remoção de acento. O escopo é a sessão porque uma pergunta de continuação ("e se foi no Pix?") cita a política recuperada num turno anterior, e o estado `retrieved` do plano já é a união da sessão. O template garante por construção o "nunca diagnostica" e o motivo fora do texto (AC11), e como a sessão termina, nada útil ao cliente se perde.

### Alternativas

- Citação estruturada (tool ou campo JSON): rejeitada, porque cria um segundo canal que o modelo fraco precisa preencher e diverge da §4.
- Citar pelo nome de arquivo: rejeitada, porque não é linguagem de cliente.
- Casamento aproximado de título: rejeitada, porque aceitaria título inventado parecido.
- Handoff escrito pelo modelo sob regras do prompt: rejeitada, porque diagnóstico e motivo vazariam conforme o modelo fraco.
- Um template por motivo: desnecessário para o AC11, e a redação fica com o dono.

`origin: simulated-approved` (premissa `docs-specs-assistant:01a18cda-12b6-7f32-b78a-c6b50bedf2de`)

## 4. Variável `PET_HELPER_KIT_DIR`

### Escolha

O contrato de variáveis de ambiente da §1 ganha `PET_HELPER_KIT_DIR`, com padrão `kit` relativo ao diretório de trabalho. O `build_assistant` carrega dali as políticas, os pedidos e o catálogo, e o kit ausente gera um erro de uma linha com o caminho resolvido.

### Motivo

É aditiva e segue o estilo `PET_HELPER_` da §1, como o `PET_HELPER_ORDER_API_FAILURE` que o `orders/api.py` já lê. Uma variável cobre a imagem Docker, os testes apontando para um kit de fixture e o avaliador rodando o CLI de outro diretório, e o erro com o caminho resolvido explica o engano mais provável, o diretório errado (`avaliar-sem-pagar`). A alteração ao vivo continua sendo um arquivo em `kit/policies` (ADR 0002).

### Alternativas

- Caminho relativo ao pacote: rejeitada, porque quebra com o wheel instalado e prende o código ao layout do repositório.
- Caminho fixo relativo ao diretório de trabalho, sem variável: viável porém rígida, já que os testes precisariam de `chdir` e o avaliador ficaria sem como sobrescrever.
- Flag `--kit-dir`: rejeitada, porque o CLI e o runner de eval precisariam dela, enquanto a variável chega aos dois.

`origin: simulated-approved` (premissa `docs-specs-assistant:01a18cda-d858-77cc-8030-e20cf31b78a1`)

## Fora desta unidade

- Rodar as rodadas de eval com `PET_HELPER_FALLBACK=none` e contar a causa `model_unavailable` como `error`: fica com a unidade do runner de eval (decisão 2).
- Um caso adversarial medindo o modelo repetindo um SKU inexistente afirmado pelo cliente: fica para a unidade de eval (decisão 3).
- Um caso "reclamação comum não escala" (CPF válido, pedido Enviado dentro do prazo, "já faz 5 dias e nada, que demora, hein?", com `tool_not_called escalate_to_human` e juiz `escalation_judgment`), par de contraste de `esc-strong-negative-sentiment` e bom candidato a held-out: fica para a unidade de eval (decisão do critic na revisão local, premissa `docs-specs-assistant:01a18dfb-09c9-79fd-82a5-67160e68d817`).

## Emendas na entrega

- Histórico enviado ao modelo fica intacto (sem poda de tool calls de turnos anteriores); o checkpointer usa serde pickle porque o `model_dump` achata o `artifact` das `ToolMessage`s (premissa `docs-specs-assistant:01a18db8-6eb8-707d-8344-0f71ac53330b`).
- Exceção na §2 para a regeneração do guard de saída, decidida pelo dono (gap `docs-specs-assistant:01a18db9-16c9-76b8-a11f-215f2a12e06f`).
- `PET_HELPER_OLLAMA_MODEL` nomeia o modelo do Ollama (primário e fallback) e `PET_HELPER_ASSISTANT_MODEL` passa a ser só o modelo Anthropic (premissa `docs-specs-assistant:01a18dfa-f1b8-7951-994b-6f8e8d23d039`).

## Ajustes no plano

1. US-7a: o teste do checkpoint não pode afirmar que o CPF cru some de todo checkpoint, já que o `InMemorySaver` guarda um checkpoint por superstep e o de entrada ainda leva o `user_text` cru. O executor escolhe entre passar o texto cru pelo contexto de execução do LangGraph (fora do checkpoint) ou restringir a afirmação e o teste ao estado mais recente. Fica só em memória e nunca vai para log (decisão 1).
2. US-7a: o roteador do `agent` é próprio, não um `tools_condition` puro, por causa da aresta `agent -> handoff`, e mantém a regra de nunca pular o guard de saída (decisão 1).
3. US-10: o guard de saída ainda inválido depois da regeneração leva ao `handoff` com `escalation=repeated_failure` e `ended`, pelo mesmo caminho de gravação única (decisão 1).
4. US-7a: o estado ganha a causa do escalonamento, gravada uma única vez junto com o motivo, e as US-9 e US-10 a afirmam (decisão 2).
5. US-4, US-7b e US-10: onde o texto disser "recuperado neste turno", vale "recuperado na sessão", e o nó `output_guard` passa a união da sessão ao `check_reply` (decisão 3).
6. Riscos: nova linha para a isenção de SKU digitado pelo cliente, que também deixa o modelo repetir um SKU inexistente afirmado pelo cliente ("recomende o PX-9999"). Risco aceito, porque o assistente não o inventa e um detector de negação seria frágil (decisão 3).
7. US-6: o `WORKDIR` do Dockerfile é o diretório que contém o `kit/` copiado (ou a imagem define `PET_HELPER_KIT_DIR`), afirmado no `test_compose.py` (decisão 4).
8. US-6 ou US-7a: a lista de variáveis da §1 da [architecture.md](../../architecture.md) recebe `PET_HELPER_KIT_DIR` nesta unidade, além do README (decisão 4).
