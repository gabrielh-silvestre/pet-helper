from collections.abc import Mapping
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict

CHECK_TYPES = frozenset(
    {
        "cites_source",
        "skus_exist",
        "mentions_order",
        "no_other_customer_data",
        "tool_called",
        "tool_not_called",
        "escalated",
        "must_include",
        "must_not_include",
        "expected_value",
        "state",
    }
)
# these compare the reply with the terms in `value`, so an empty value is a mistake
VALUED_CHECK_TYPES = frozenset(
    {
        "cites_source",
        "mentions_order",
        "no_other_customer_data",
        "tool_called",
        "tool_not_called",
        "must_include",
        "must_not_include",
        "expected_value",
    }
)
# the judge criteria, kept here so the pre-flight does not import the judge
JUDGE_CRITERIA = ("tone", "escalation_judgment", "dont_know_accuracy")
# these look at the whole conversation, so a turn makes no sense on them
TURNLESS_CHECK_TYPES = frozenset(
    {
        "skus_exist",
        "no_other_customer_data",
        "tool_called",
        "tool_not_called",
        "escalated",
        "state",
    }
)
# snapshot() values a case may assert on; US-6 checks this list against kit/KIT.md
PUBLIC_STATE_KEYS = frozenset(
    {
        "session_cpf",
        "cpf_conflict",
        "escalation",
        "escalation_cause",
        "ended",
        "injection_suspected",
    }
)


class Check(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    type: str
    value: str | list[str] | None = None
    turn: int | None = None
    args: dict | None = None
    key: str | None = None


class JudgeEntry(BaseModel):
    criterion: str
    turn: int | None = None


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    id: str
    suite: str | None = None
    rule: str | None = None
    today: date
    turns: list[str]
    checks: list[Check] = []
    expected_doc: str | list[str] | None = None
    judge: list[str | JudgeEntry] = []
    behaviors: list[str] = []
    expected_outcome: str | None = None
    absent_refs: list[str] = []
    fixture: str | None = None
    reference: dict | None = None


def load_cases(directory: Path) -> list[EvalCase]:
    return [
        EvalCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    ]


@cache
def _tool_params() -> dict[str, frozenset[str]]:
    # imported here so the cases module stays light; the tools only close over
    # their collaborators, so none is needed to read their signatures
    from pet_helper.conversation import build_tool_registry

    registry = build_tool_registry(
        policies=cast(Any, None), catalog=cast(Any, None), orders=cast(Any, None)
    )
    return {tool.name: frozenset(tool.args) for tool in registry.tools}


def _is_valid_today(today: Any) -> bool:
    if isinstance(today, date):
        return True
    try:
        date.fromisoformat(today)
    except (TypeError, ValueError):
        return False
    return True


def _check_errors(check: Mapping[str, Any]) -> list[str]:
    check_type = check.get("type")
    errors = []
    if not set(check) <= set(Check.model_fields):
        errors.append("unknown_key")
    if check_type not in CHECK_TYPES:
        errors.append("unknown_check_type")
    if check_type in TURNLESS_CHECK_TYPES and check.get("turn") is not None:
        errors.append("turn_not_allowed")
    if check_type == "state" and check.get("key") not in PUBLIC_STATE_KEYS:
        errors.append("state_key_not_public")
    if check_type in {"tool_called", "tool_not_called"}:
        params = _tool_params().get(str(check.get("value")))
        if params is None:
            errors.append("unknown_tool")
        elif not set(check.get("args") or ()) <= params:
            errors.append("unknown_tool_arg")
    # a term check without terms passes on nothing (all of an empty list), which hides a typo
    if check_type in VALUED_CHECK_TYPES and not check.get("value"):
        errors.append("missing_value")
    return errors


def _judge_errors(entry: str | Mapping[str, Any], turns: int) -> list[str]:
    errors = []
    if isinstance(entry, Mapping):
        if not set(entry) <= set(JudgeEntry.model_fields):
            errors.append("unknown_key")
        criterion, turn = entry.get("criterion"), entry.get("turn")
    else:
        criterion, turn = entry, None
    if criterion not in JUDGE_CRITERIA:
        errors.append("unknown_judge_criterion")
    if (
        turn is not None
        and turns
        and not (isinstance(turn, int) and 1 <= turn <= turns)
    ):
        errors.append("judge_turn_out_of_range")
    elif turn is None and criterion == "dont_know_accuracy" and turns > 1:
        errors.append("judge_turn_required")
    return errors


def validate_structure(raw: Mapping[str, Any]) -> list[str]:
    errors = []
    if not set(raw) <= set(EvalCase.model_fields):
        errors.append("unknown_key")
    if not raw.get("turns"):
        errors.append("empty_turns")
    if not _is_valid_today(raw.get("today")):
        errors.append("bad_today")
    for entry in raw.get("judge") or []:
        errors.extend(_judge_errors(entry, len(raw.get("turns") or [])))
    for check in raw.get("checks") or []:
        errors.extend(_check_errors(check))
    return list(dict.fromkeys(errors))


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
            "judge": [
                entry if isinstance(entry, str) else entry.model_dump(exclude_none=True)
                for entry in case.judge
            ],
            "behaviors": case.behaviors,
            "expected_outcome": case.expected_outcome,
            "absent_refs": case.absent_refs,
            "fixture": case.fixture,
            "reference": case.reference,
        },
    }
