from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from genai_prices import Usage, calc_price

FREE_PROVIDER = "ollama"


class UnknownPriceError(LookupError):
    pass


def cost_usd(
    provider: str, model: str, usage: Mapping[str, Any], strict: bool = False
) -> Decimal | None:
    if provider == FREE_PROVIDER:
        return Decimal(0)
    details = usage.get("input_token_details") or {}
    # LangChain's input_tokens already counts the cache read and write tokens
    priced_usage = Usage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=details.get("cache_read", 0),
        cache_write_tokens=details.get("cache_creation", 0),
    )
    try:
        return calc_price(priced_usage, model, provider_id=provider).total_price
    except LookupError as error:
        if strict:
            raise UnknownPriceError(f"No price for {provider}/{model}") from error
        return None
