# pet-helper

Assistente de atendimento de um e-commerce pet fictício.

## Setup nativo

```sh
uv sync
PET_HELPER_TODAY=2026-11-03 uv run pet-helper chat
```

`PET_HELPER_TODAY` fixa a data de hoje do assistente. Sem ela, o assistente usa a data real, e os prazos dos pedidos do kit (calculados a partir de 2026-11-03, a `reference_date` de `kit/orders.json`) saem errados.

Por padrão, a CLI carrega políticas, catálogo e pedidos do diretório `kit` relativo ao diretório de trabalho. Use `PET_HELPER_KIT_DIR` para apontar para outro caminho do kit.

O provedor do modelo vem de três variáveis:

- `PET_HELPER_PROVIDER`: `anthropic` ou `ollama`. Sem ela, a CLI usa `anthropic` quando `ANTHROPIC_API_KEY` está definida e `ollama` nos demais casos.
- `ANTHROPIC_API_KEY`: chave da API da Anthropic.
- `OLLAMA_BASE_URL`: endereço do servidor Ollama, quando não for o padrão.

Se a conversa terminar com a causa `model_unavailable`, o provedor não respondeu: confira as variáveis acima e rode o comando de novo.

## Rastros da conversa

O `pet-helper chat` grava um rastro em `traces/AAAA-MM-DD/<session_id>.jsonl`, relativo ao diretório de trabalho: um evento por turno, chamada ao modelo e chamada de ferramenta. O conteúdo sai mascarado (CPF, telefone e e-mail). Com `PET_HELPER_TRACE_CONTENT=0`, os campos de texto livre gravam só o tamanho.

## Setup sem chave (Ollama)

Para avaliar sem chave de API paga, o Docker Compose sobe o Ollama (`ollama/ollama:0.40.12`), baixa o modelo configurado (padrão `llama3.1:8b`) antes de iniciar o app e abre o chat no terminal:

```sh
docker compose run --rm app
```

Em máquinas com pouca RAM, sobrescreva a tag do modelo pela variável `PET_HELPER_OLLAMA_MODEL`:

```sh
PET_HELPER_OLLAMA_MODEL=llama3.2:3b docker compose run --rm app
```

Built with Llama (Meta Llama 3.1, sob a Llama 3.1 Community License).
