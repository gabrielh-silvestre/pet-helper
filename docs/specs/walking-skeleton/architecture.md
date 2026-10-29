# Arquitetura: esqueleto base

Decisões da cadeira do dono para a unidade, tomadas sobre o [plan.md](plan.md) e a [spec.md](spec.md), com base na [architecture.md](../../architecture.md) e na [cadeira-do-dono.md](../../directives/cadeira-do-dono.md). Nenhuma decisão altera diretriz, premissa, ADR ou a arquitetura aprovada, por isso nenhuma vira ADR.

## 1. Tipo de mensagem no seam do gateway

### Escolha

O gateway usa as mensagens do `langchain_core.messages`, com `BaseMessage` na entrada de `complete` e `AIMessage` em `ModelResult.message`.

### Motivo

O tipo já decorre de decisões aprovadas: o `conversation` é um `StateGraph` cujo estado de mensagens (`add_messages`) é `BaseMessage`, os adaptadores previstos são `langchain-anthropic` e `langchain-ollama` e o trace mede `model.call` por um `BaseCallbackHandler` do LangChain. Além disso, o `langgraph` 1.2.18 exige `langchain-core>=1.4.7,<2`, logo o `langchain-core` está no runtime de qualquer forma.

### Alternativas

- Mensagens próprias em dataclass, rejeitada porque criaria tradução nos dois sentidos entre grafo e gateway sem ganhar independência, já que o grafo continua tipado pelo LangChain.
- Registrar como ADR, rejeitado porque a escolha não surpreende nem é um trade-off real, ou seja, a irreversibilidade vem da escolha do LangGraph e dos adaptadores LangChain, que já está registrada.

`origin: simulated-approved`

## 2. `ModelResult` só com `message`

### Escolha

Nesta unidade o `ModelResult` tem apenas o campo `message`, e só é construído dentro do módulo `gateway` (adaptadores e fake), já testes e `conversation` apenas leem atributos.

### Motivo

Nada produz nem lê `provider`, `model`, `usage`, `ttft_ms`, `fell_back` ou `refused` agora, e o formato de `usage` e `ttft_ms` ainda depende do trace, que mede TTFT e usage pelo callback handler. Como só o gateway constrói o resultado, um campo novo, obrigatório ou com padrão, mexe só no gateway, dessa forma a unidade que trouxer o campo decide se ele tem padrão. O plano passa a declarar este invariante no lugar de "os campos futuros chegam com valor padrão", que levaria a valores falsos como `provider=""`.

O princípio 2 do plano é reescrito como regra de compatibilidade: toda interface exposta é um subconjunto ou um acréscimo compatível da interface de `architecture.md`, com os mesmos nomes e tipos, e o que falta entra por acréscimo, nunca renomeando nem mudando tipo.

### Alternativas

- Interface completa já agora, rejeitada por ser especulação, com os tipos de `usage` e `ttft_ms` ainda indefinidos.
- `complete` devolvendo `AIMessage` direto, rejeitada porque obrigaria a mudar todo chamador quando o primeiro campo entrar.

Obs: _se a unidade do trace concluir que `usage` e `ttft_ms` não pertencem ao `ModelResult`, retirá-los da interface aprovada é decisão crítica daquela unidade e precisa do aval do dono._

`origin: simulated-approved`

## 3. Adaptador `fake`

### Escolha

O `FakeGateway(replies)` próprio, implementando `ModelGateway` direto e guardando as chamadas em `calls`.

### Motivo

O seam de que o `conversation` depende é o `ModelGateway`, não um `BaseChatModel`, e a `architecture.md` descreve o fake como respostas roteirizadas nesse nível. Conferimos no `langchain-core` 1.7.1 que nenhum dos fakes prontos (`FakeListChatModel`, `GenericFakeChatModel`, `FakeMessagesListChatModel`, `ParrotFakeChatModel`) guarda as entradas, logo embrulhá-los exigiria uma classe para o protocolo e o `ModelResult` e ainda um callback ou subclasse para as chamadas, saindo mais código que o fake próprio e trazendo a maquinaria de Runnable para dentro do seam de teste.

### Alternativas

- Um adaptador genérico `BaseChatModel -> ModelGateway` sobre um fake do `langchain-core`, rejeitado agora por ser código da unidade do primeiro adaptador real.
- O `BaseChatModel` como interface do gateway, rejeitado porque contraria a `architecture.md` aprovada, que esconde fallback e recusa no gateway (`fell_back`, `refused`).

### Seguimentos

- A unidade do primeiro adaptador real usa o `GenericFakeChatModel` para testar sem rede o adaptador sobre `BaseChatModel` (streaming, callback, `tool_calls`).
- A unidade de tools amplia `replies` para aceitar `AIMessage` com `tool_calls`, por acréscimo.

`origin: simulated-approved`

## 4. `default_gateway()` e a raiz de composição

### Escolha

O `gateway` expõe a função pública `default_gateway()`, que nesta unidade devolve `FakeGateway()` sem ler o ambiente, e a CLI é a única raiz de composição.

### Motivo

A `architecture.md` esconde a seleção de provider por ambiente dentro do gateway, e uma fábrica no próprio módulo mantém a CLI fina, como a seção 4 a descreve. É um acréscimo à interface, não uma contradição.

Porém, pela seção 1 da `architecture.md`, sem `PET_HELPER_PROVIDER` o provider é `anthropic` quando existe `ANTHROPIC_API_KEY`, logo na unidade do adaptador o `default_gateway()` deixa de devolver o fake. Se os testes da CLI dependessem do padrão, passariam a chamar o modelo real no pytest de quem tiver a chave, violando `modelo-real-so-a-mao`. Por isso os testes de laço da CLI trocam `pet_helper.cli.default_gateway` por um `FakeGateway` roteirizado, com resposta diferente de `DEFAULT_REPLY`, e o teste "`default_gateway()` responde `DEFAULT_REPLY`" fica marcado como comportamento só do esqueleto.

### Alternativas

- `main(argv=None, gateway=None)`, rejeitada por ser um parâmetro que só o teste usaria, já que o patch no ponto de composição resolve.
- Seleção por ambiente já agora, rejeitada por ser um ramo sem segunda saída com um único adaptador.

### Seguimentos

- A unidade do adaptador decide como os testes chegam ao fake. Um valor `fake` em `PET_HELPER_PROVIDER` não está no contrato da seção 1, e acrescentá-lo é decisão de arquitetura daquela unidade.

`origin: simulated-approved`

## 5. `Reply.escalation` tipado como `None`

### Escolha

`escalation: None = None` até a unidade de escalação, que alarga o tipo para `EscalationReason | None` sem quebrar quem lê o campo.

### Motivo

O valor é sempre `None` nesta unidade e o tipo diz exatamente isso, assim como a spec fixa o formato `Reply(text, sources, escalation)`, então o campo existe.

### Alternativas

- Criar já o enum `EscalationReason` com os 4 valores do [GLOSSARY.md](../../../GLOSSARY.md), rejeitada porque nada o produz nem o lê, e o lugar do enum e a prioridade entre motivos são da unidade de escalação.
- `str | None`, rejeitada porque convida a motivo em texto livre, enquanto o glossário pede exatamente um motivo.
- Omitir o campo, rejeitada porque contraria a spec.

`origin: simulated-approved`
