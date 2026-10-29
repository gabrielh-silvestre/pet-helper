from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Check(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    type: str
    value: str | list[str] | None = None


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    id: str
    suite: str | None = None
    rule: str | None = None
    today: date
    turns: list[str]
    checks: list[Check] = []
    expected_doc: str | None = None
    judge: list[str] = []
    behaviors: list[str] = []
    expected_outcome: str | None = None
    absent_refs: list[str] = []


def load_cases(directory: Path) -> list[EvalCase]:
    return [
        EvalCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    ]


def to_sample_kwargs(case: EvalCase) -> dict:
    return {
        "id": case.id,
        "input": [{"role": "user", "content": turn} for turn in case.turns],
        "metadata": {
            "suite": case.suite,
            "rule": case.rule,
            "today": case.today.isoformat(),
            "checks": [check.model_dump(exclude_none=True) for check in case.checks],
            "expected_doc": case.expected_doc,
            "judge": case.judge,
            "behaviors": case.behaviors,
            "expected_outcome": case.expected_outcome,
            "absent_refs": case.absent_refs,
        },
    }
