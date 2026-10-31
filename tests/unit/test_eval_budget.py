import threading
from decimal import Decimal

import pytest

from pet_helper.evals.budget import (
    ASSISTANT_CALL_RESERVE_USD,
    JUDGE_CALL_RESERVE_USD,
    BudgetExceeded,
    RoundBudget,
    batch_reserve_usd,
)
from pet_helper.gateway.anthropic import DEFAULT_MAX_TOKENS
from pet_helper.trace import UnknownPriceError, cost_usd

RESERVE = Decimal("0.005")
THREADS = 8
# the assistant prompt, tools and history stay under this many input tokens
ASSISTANT_INPUT_LIMIT_TOKENS = 10_000
# the judge's explicit max_tokens (US-7) and the input of its slice
JUDGE_MAX_TOKENS = 4096
JUDGE_INPUT_LIMIT_TOKENS = 10_000


def _worst_case_usage(input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_token_details": {"cache_creation": input_tokens},
    }


def _spend(budget: RoundBudget, calls: int, actual: Decimal | None) -> None:
    for _ in range(calls):
        budget.before_call(RESERVE)
        if actual is None:
            budget.on_error(None)
        else:
            budget.on_cost(actual)


# the cap


def test_it_should_stop_before_the_first_call_when_the_cap_is_lower_than_one_call():
    budget = RoundBudget(RESERVE - Decimal("0.001"))

    with pytest.raises(BudgetExceeded):
        budget.before_call(RESERVE)


def test_it_should_allow_calls_up_to_the_cap_and_stop_at_the_next():
    budget = RoundBudget(3 * RESERVE)
    _spend(budget, 3, RESERVE)

    with pytest.raises(BudgetExceeded):
        budget.before_call(RESERVE)


def test_it_should_count_the_reservations_in_flight_against_the_cap():
    budget = RoundBudget(3 * RESERVE)
    for _ in range(3):
        budget.before_call(RESERVE)

    with pytest.raises(BudgetExceeded):
        budget.before_call(RESERVE)


def test_it_should_leave_nothing_reserved_when_a_call_is_refused():
    budget = RoundBudget(RESERVE)
    with pytest.raises(BudgetExceeded):
        budget.before_call(2 * RESERVE)

    budget.before_call(RESERVE)


# eight threads starting at once


def test_it_should_let_at_most_three_calls_through_when_eight_threads_start_together():
    budget = RoundBudget(3 * RESERVE)
    start = threading.Barrier(THREADS)
    all_attempted = threading.Event()
    lock = threading.Lock()
    outcomes: list[bool] = []

    def attempt() -> None:
        start.wait()
        try:
            budget.before_call(RESERVE)
            admitted = True
        except BudgetExceeded:
            admitted = False
        with lock:
            outcomes.append(admitted)
            if len(outcomes) == THREADS:
                all_attempted.set()
        if admitted:
            # no cost returns until every thread has asked: only the reservations hold the cap
            all_attempted.wait(timeout=5)
            budget.on_cost(RESERVE)

    threads = [threading.Thread(target=attempt) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(True) == 3


def test_it_should_keep_the_large_reserve_in_flight_when_a_small_call_on_another_thread_settles_first():
    budget = RoundBudget(Decimal("0.03"))
    large_reserved = threading.Event()
    small_settled = threading.Event()

    def large_call() -> None:
        budget.before_call(Decimal("0.02"))
        large_reserved.set()
        small_settled.wait(timeout=5)
        budget.on_cost(Decimal("0.02"))

    def small_call() -> None:
        budget.before_call(Decimal("0.005"))
        budget.on_cost(Decimal("0.001"))
        small_settled.set()

    large = threading.Thread(target=large_call)
    large.start()
    assert large_reserved.wait(timeout=5)
    small = threading.Thread(target=small_call)
    small.start()
    small.join()

    # 0.001 spent plus the 0.02 still in flight leaves 0.009 under the cap
    with pytest.raises(BudgetExceeded):
        budget.before_call(Decimal("0.01"))
    large.join()


# reconciliation


def test_it_should_replace_the_reserve_with_the_real_cost_when_a_call_finishes():
    budget = RoundBudget(3 * RESERVE)

    _spend(budget, 5, RESERVE / 10)

    budget.before_call(RESERVE)


def test_it_should_keep_the_reserve_charged_when_a_call_errors_without_usage():
    budget = RoundBudget(3 * RESERVE)
    _spend(budget, 3, None)

    with pytest.raises(BudgetExceeded):
        budget.before_call(RESERVE)


def test_it_should_replace_the_reserve_of_the_call_that_finished():
    budget = RoundBudget(Decimal("0.03"))
    budget.before_call(Decimal("0.01"))
    budget.on_cost(Decimal("0.002"))
    budget.before_call(Decimal("0.02"))
    budget.on_error(None)

    # spent 0.002 + 0.02 leaves 0.008 under the cap
    budget.before_call(Decimal("0.008"))


def test_it_should_refuse_a_call_that_would_pass_the_cap_after_a_costly_one():
    budget = RoundBudget(Decimal("0.03"))
    budget.before_call(Decimal("0.01"))
    budget.on_cost(Decimal("0.002"))
    budget.before_call(Decimal("0.02"))
    budget.on_error(None)

    with pytest.raises(BudgetExceeded):
        budget.before_call(Decimal("0.0081"))


# the reserve constants


def test_it_should_reserve_at_least_the_assistant_worst_case_priced_by_genai_prices():
    worst_case = cost_usd(
        "anthropic",
        "claude-haiku-5-5",
        _worst_case_usage(ASSISTANT_INPUT_LIMIT_TOKENS, DEFAULT_MAX_TOKENS),
    )

    assert worst_case <= ASSISTANT_CALL_RESERVE_USD


def test_it_should_reserve_at_least_the_judge_worst_case_priced_by_genai_prices():
    worst_case = cost_usd(
        "anthropic",
        "claude-sonnet-5-5",
        _worst_case_usage(JUDGE_INPUT_LIMIT_TOKENS, JUDGE_MAX_TOKENS),
    )

    assert worst_case <= JUDGE_CALL_RESERVE_USD


# batch


def test_it_should_reserve_a_batch_at_its_worst_case_at_the_batch_price():
    items = 12
    output_only = cost_usd(
        "anthropic",
        "claude-sonnet-5-5",
        {"input_tokens": 0, "output_tokens": JUDGE_MAX_TOKENS},
    )

    reserve = batch_reserve_usd(items, JUDGE_MAX_TOKENS)

    # batch calls cost half
    assert reserve >= items * output_only / 2


def test_it_should_reserve_more_for_a_larger_batch():
    small = batch_reserve_usd(5, JUDGE_MAX_TOKENS)

    large = batch_reserve_usd(10, JUDGE_MAX_TOKENS)

    assert large > small


def test_it_should_stop_before_submitting_a_batch_the_cap_cannot_cover():
    budget = RoundBudget(batch_reserve_usd(5, JUDGE_MAX_TOKENS))

    with pytest.raises(BudgetExceeded):
        budget.before_call(batch_reserve_usd(10, JUDGE_MAX_TOKENS))


def test_it_should_free_the_batch_reserve_at_collection_when_the_real_cost_is_lower():
    reserve = batch_reserve_usd(10, JUDGE_MAX_TOKENS)
    budget = RoundBudget(reserve)
    budget.before_call(reserve)
    budget.on_cost(reserve / 100)

    budget.before_call(reserve / 2)


def test_it_should_refuse_to_reserve_a_batch_for_a_model_without_a_price():
    with pytest.raises(UnknownPriceError):
        batch_reserve_usd(3, JUDGE_MAX_TOKENS, model="claude-unknown-9")
