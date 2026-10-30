import os
from typing import Literal

from pet_helper.gateway.models import ModelConfigError

PROVIDER_ENV = "PET_HELPER_PROVIDER"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ASSISTANT_MODEL_ENV = "PET_HELPER_ASSISTANT_MODEL"
EFFORT_ENV = "PET_HELPER_EFFORT"
FALLBACK_ENV = "PET_HELPER_FALLBACK"
OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"
DEFAULT_EFFORT = "low"

type ProviderName = Literal["anthropic", "ollama"]
type FallbackMode = Literal["ollama", "none"]


def resolve_provider() -> ProviderName:
    configured = os.environ.get(PROVIDER_ENV)
    if configured:
        if configured == "anthropic":
            return "anthropic"
        if configured == "ollama":
            return "ollama"
        raise ModelConfigError(f"Unsupported {PROVIDER_ENV} value: {configured!r}")
    if os.environ.get(ANTHROPIC_API_KEY_ENV):
        return "anthropic"
    return "ollama"


def resolve_fallback() -> FallbackMode:
    configured = os.environ.get(FALLBACK_ENV)
    if not configured or configured == "ollama":
        return "ollama"
    if configured == "none":
        return "none"
    raise ModelConfigError(f"Unsupported {FALLBACK_ENV} value: {configured!r}")


def resolve_anthropic_model() -> str:
    return os.environ.get(ASSISTANT_MODEL_ENV) or DEFAULT_ANTHROPIC_MODEL


def resolve_ollama_model() -> str:
    return os.environ.get(ASSISTANT_MODEL_ENV) or DEFAULT_OLLAMA_MODEL


def resolve_ollama_base_url() -> str | None:
    return os.environ.get(OLLAMA_BASE_URL_ENV) or None


def resolve_effort() -> str:
    return os.environ.get(EFFORT_ENV) or DEFAULT_EFFORT
