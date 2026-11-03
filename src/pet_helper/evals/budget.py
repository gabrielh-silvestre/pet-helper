import threading
from decimal import Decimal
from typing import cast

from langchain_core.messages.ai import UsageMetadata

from pet_helper.gateway import DEFAULT_ANTHROPIC_MODEL, DEFAULT_MAX_TOKENS
from pet_helper.trace import BudgetExceeded, cost_usd

__all__ = [
    "ASSISTANT_CALL_RESERVE_USD",
    "JUDGE_CALL_RESERVE_USD",
    "JUDGE_INPUT_LIMIT_TOKENS",
    "BudgetExceeded",
    "RoundBudget",
    "batch_reserve_usd",
    "worst_case_usd",
]

JUDGE_MODEL = "claude-sonnet-5-5"
# the assistant prompt, tools and history stay under this many input tokens
ASSISTANT_INPUT_LIMIT_TOKENS = 10_000
# the judge sets this max_tokens explicitly; its slice of the case stays under the input limit
JUDGE_MAX_TOKENS = 4096
JUDGE_INPUT_LIMIT_TOKENS = 10_000
# the Batch API bills half of the synchronous price
BATCH_PRICE_FACTOR = Decimal("0.5")


def worst_case_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        # every input token billed at the cache-write price, the dearest case
        "input_token_details": {"cache_creation": input_tokens},
    }
    # strict raises on an unknown price, so the result is never None
    return cast(Decimal, cost_usd("anthropic", model, usage, strict=True))


ASSISTANT_CALL_RESERVE_USD = worst_case_usd(
    DEFAULT_ANTHROPIC_MODEL, ASSISTANT_INPUT_LIMIT_TOKENS, DEFAULT_MAX_TOKENS
)


JUDGE_CALL_RESERVE_USD = worst_case_usd(
    JUDGE_MODEL, JUDGE_INPUT_LIMIT_TOKENS, JUDGE_MAX_TOKENS
)


def batch_reserve_usd(
    items: int, max_tokens: int, *, model: str = JUDGE_MODEL
) -> Decimal:
    per_item = worst_case_usd(model, JUDGE_INPUT_LIMIT_TOKENS, max_tokens)
    return items * per_item * BATCH_PRICE_FACTOR


class _ThreadReserves(threading.local):
    def __init__(self) -> None:
        self.stack: list[Decimal] = []


class RoundBudget:
    def __init__(self, cap: Decimal):
        self.cap = cap
        self.halted = False
        self.price_unknown = False
        self._cap = cap
        self.spent = Decimal(0)
        self._in_flight_total = Decimal(0)
        # LangChain runs the sync callbacks of a call on the thread that makes it, so the hooks
        # of one call share a thread; async callbacks may not, and would need ids instead
        self._reserves = _ThreadReserves()
        self._lock = threading.Lock()

    def before_call(self, reserve: Decimal) -> None:
        if self.halted:
            raise BudgetExceeded("A call ended without usage, the round stopped")
        with self._lock:
            committed = self.spent + self._in_flight_total
            if committed + reserve > self._cap:
                raise BudgetExceeded(
                    f"Round cap of US$ {self._cap} reached "
                    f"(US$ {committed} committed, US$ {reserve} needed)"
                )
            self._in_flight_total += reserve
            self._reserves.stack.append(reserve)

    def on_cost(self, actual: Decimal) -> None:
        self._settle(actual)

    def on_error(self, usage: UsageMetadata | None) -> None:
        # priced at the judge model, the dearest in a round, so a cut call is never undercounted
        cost = cost_usd("anthropic", JUDGE_MODEL, usage) if usage else None
        self._settle(cost)

    def _settle(self, actual: Decimal | None) -> None:
        with self._lock:
            stack = self._reserves.stack
            reserve = stack.pop() if stack else Decimal(0)
            self._in_flight_total -= reserve
            self.spent += reserve if actual is None else actual
