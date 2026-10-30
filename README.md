# pet-helper

Assistente de atendimento de um e-commerce pet fictício.

## Setup

```sh
uv sync
uv run pet-helper chat
```

Por padrão, a CLI carrega políticas, catálogo e pedidos do diretório `kit` relativo ao diretório de trabalho. Use `PET_HELPER_KIT_DIR` para apontar para outro caminho do kit.

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
