from importlib import import_module
from typing import TYPE_CHECKING, Any

from pet_helper.evals.cases import EvalCase, load_cases, validate_structure
from pet_helper.evals.checks import check_reference
from pet_helper.evals.confirm import confirm as ask_confirmation

if TYPE_CHECKING:
    from pet_helper.evals.judge import (
        AnthropicBatchClient,
        AnthropicJudgeClient,
        BatchClient,
        JudgeClient,
    )
    from pet_helper.evals.round import (
        Estimate,
        GitState,
        RoundResult,
        collect_round,
        current_git_state,
        rejudge_round,
        run_round,
    )

# the runner and the judge import inspect_ai, which the cases and checks must not pull in
_LAZY_EXPORTS = {
    "AnthropicBatchClient": "judge",
    "AnthropicJudgeClient": "judge",
    "BatchClient": "judge",
    "Estimate": "round",
    "GitState": "round",
    "JudgeClient": "judge",
    "RoundResult": "round",
    "collect_round": "round",
    "current_git_state": "round",
    "rejudge_round": "round",
    "run_round": "round",
}

__all__ = [
    "AnthropicBatchClient",
    "AnthropicJudgeClient",
    "BatchClient",
    "Estimate",
    "EvalCase",
    "GitState",
    "JudgeClient",
    "RoundResult",
    "ask_confirmation",
    "check_reference",
    "collect_round",
    "current_git_state",
    "load_cases",
    "rejudge_round",
    "run_round",
    "validate_structure",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        return getattr(import_module(f"pet_helper.evals.{_LAZY_EXPORTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
