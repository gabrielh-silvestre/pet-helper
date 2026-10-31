from typing import Any


def __getattr__(name: str) -> Any:
    # the runner imports inspect_ai, which the cases and checks must not pull in
    if name == "run_round":
        # the module arrives in US-9; drop the ignore then
        from pet_helper.evals.round import run_round  # pyrefly: ignore[missing-import]

        return run_round
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
