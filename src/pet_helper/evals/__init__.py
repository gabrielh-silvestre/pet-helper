from typing import Any


def __getattr__(name: str) -> Any:
    # the runner imports inspect_ai, which the cases and checks must not pull in
    if name == "run_round":
        from pet_helper.evals.round import run_round

        return run_round
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
