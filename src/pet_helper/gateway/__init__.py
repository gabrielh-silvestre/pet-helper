from pet_helper.gateway.anthropic import AnthropicGateway
from pet_helper.gateway.config import (
    FallbackMode,
    ProviderName,
    resolve_fallback,
    resolve_provider,
)
from pet_helper.gateway.fake import (
    FakeGateway,
    FakeScriptExhaustedError,
)
from pet_helper.gateway.models import (
    ModelConfigError,
    ModelGateway,
    ModelResult,
    ModelUnavailable,
)
from pet_helper.gateway.ollama import OllamaGateway


def build_gateway() -> ModelGateway:
    if resolve_provider() == "anthropic":
        return AnthropicGateway()
    return OllamaGateway()


__all__ = [
    "AnthropicGateway",
    "FakeGateway",
    "FakeScriptExhaustedError",
    "FallbackMode",
    "ModelConfigError",
    "ModelGateway",
    "ModelResult",
    "ModelUnavailable",
    "OllamaGateway",
    "ProviderName",
    "build_gateway",
    "resolve_fallback",
    "resolve_provider",
]
