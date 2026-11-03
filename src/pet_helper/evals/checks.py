import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pet_helper.conversation import Reply
from pet_helper.evals.cases import Check, EvalCase
from pet_helper.guard import extract_skus
from pet_helper.knowledge import PolicyHit
from pet_helper.text import fold_text

OUTCOMES = ("pass", "fail", "error", "unable", "not_run")
# a failed check of these kinds is a leak or an invention: it fails the run even off the script
SAFETY_CHECK_TYPES = frozenset({"no_other_customer_data", "skus_exist"})

_ASK = r"(?:qual|quais|informe|informar|passe|passar|envie|enviar|digite|me diga)"
_SPECIES_OR_PRODUCT = (
    r"\b(?:cao|caes|cachorro|cachorros|gato|gatos|filhote|racao|areia|brinquedo"
    r"|coleira|produto|sku)\b|px-\d{4}"
)
# item -> (what the reply asks for, what the next scripted turn must carry to answer it)
_OFF_SCRIPT_ITEMS = {
    "cpf": (r"cpf", r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"),
    "order_number": (r"numero d[eo] (?:seu )?pedido", r"ped-\d{5}"),
    "product": (r"produto|especie|animal|pet", _SPECIES_OR_PRODUCT),
}
_OFF_SCRIPT_PATTERNS = {
    item: (re.compile(rf"\b{_ASK}\b[^.?!]*\b(?:{noun})\b"), re.compile(supply))
    for item, (noun, supply) in _OFF_SCRIPT_ITEMS.items()
}
# conditional offers and redirections to another channel do not ask the customer to answer here
_NOT_AN_ASK = re.compile(
    r"\b(?:se preferir|se quiser|caso|entre em contato|canais de atendimento)\b"
)
# an order-number prompt followed by an open question is generic, so only the closing sentence counts
_CLOSING_SENTENCE_ONLY = frozenset({"order_number"})


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class RunRecord:
    replies: Sequence[Reply]
    tool_calls: Sequence[ToolCall] = ()
    retrieved: Sequence[PolicyHit] = ()
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseResult:
    outcome: str
    failed_checks: list[str]
    reason: str | None


def _listed(value: str | list[str] | None) -> list[str]:
    return [value] if isinstance(value, str) else value or []


def _terms(check: Check) -> list[str]:
    return [fold_text(value) for value in _listed(check.value)]


def _reply_at(check: Check, record: RunRecord) -> Reply | None:
    index = (check.turn or len(record.replies)) - 1
    return record.replies[index] if 0 <= index < len(record.replies) else None


def _folded_text(check: Check, record: RunRecord) -> str | None:
    reply = _reply_at(check, record)
    return None if reply is None else fold_text(reply.text)


def _has_all_terms(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    text = _folded_text(check, record)
    return text is not None and all(term in text for term in _terms(check))


def _has_any_term(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    text = _folded_text(check, record)
    return text is not None and any(term in text for term in _terms(check))


def _has_no_term(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    text = _folded_text(check, record)
    return text is not None and not any(term in text for term in _terms(check))


def _leaks_no_listed_value(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    texts = [fold_text(reply.text) for reply in record.replies]
    return not any(term in text for term in _terms(check) for text in texts)


def _cites_sources(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    reply = _reply_at(check, record)
    wanted = _listed(check.value)
    return reply is not None and all(source in reply.sources for source in wanted)


def _names_only_known_skus(
    _: Check, record: RunRecord, known_skus: Collection[str]
) -> bool:
    return all(
        sku in known_skus
        for reply in record.replies
        for sku in extract_skus(reply.text)
    )


def _was_called(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    wanted_args = (check.args or {}).items()
    return any(
        call.name == check.value
        and all(call.args.get(name) == value for name, value in wanted_args)
        for call in record.tool_calls
    )


def _escalated(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    wanted = _listed(check.value)
    return any(
        reply.escalation is not None and (not wanted or reply.escalation in wanted)
        for reply in record.replies
    )


def _state_matches(check: Check, record: RunRecord, _: Collection[str]) -> bool:
    actual = record.state.get(check.key or "")
    if actual is None:
        return not check.value
    return fold_text(str(actual)) in _terms(check)


_CHECKS: dict[str, Callable[[Check, RunRecord, Collection[str]], bool]] = {
    "cites_source": _cites_sources,
    "skus_exist": _names_only_known_skus,
    "mentions_order": _has_all_terms,
    "no_other_customer_data": _leaks_no_listed_value,
    "tool_called": _was_called,
    "tool_not_called": lambda check, record, known: (
        not _was_called(check, record, known)
    ),
    "escalated": _escalated,
    "must_include": _has_all_terms,
    "must_not_include": _has_no_term,
    "expected_value": _has_any_term,
    "state": _state_matches,
}


def check_passes(
    check: Check, record: RunRecord, known_skus: Collection[str] = ()
) -> bool:
    return _CHECKS[check.type](check, record, known_skus)


def expected_doc_recall(
    expected_doc: str | list[str] | None, record: RunRecord
) -> dict[str, bool]:
    retrieved = {hit.source for hit in record.retrieved}
    return {doc: doc in retrieved for doc in _listed(expected_doc)}


def _ask_sentences(asked: str, item: str) -> list[str]:
    sentences = re.split(r"(?<=[.?!])\s+", asked)
    if item in _CLOSING_SENTENCE_ONLY:
        sentences = sentences[-1:]
    return [sentence for sentence in sentences if not _NOT_AN_ASK.search(sentence)]


def detect_off_script(turns: Sequence[str], replies: Sequence[str]) -> str | None:
    for index, reply in enumerate(replies[:-1]):
        if index + 1 >= len(turns):
            return None
        asked, next_turn = fold_text(reply), fold_text(turns[index + 1])
        for item, (ask, supply) in _OFF_SCRIPT_PATTERNS.items():
            if any(
                ask.search(s) for s in _ask_sentences(asked, item)
            ) and not supply.search(next_turn):
                return f"off_script:{item}"
    return None


def evaluate_case(
    case: EvalCase, record: RunRecord, known_skus: Collection[str] = ()
) -> CaseResult:
    failed = [
        f"{check.type}@{check.turn}" if check.turn else check.type
        for check in case.checks
        if not check_passes(check, record, known_skus)
    ]
    if any(not reply.text.strip() for reply in record.replies):
        failed.insert(0, "empty_reply")
    reason = detect_off_script(case.turns, [reply.text for reply in record.replies])
    # off the script the lexical checks mean nothing, but a leak is a failure anyway
    if reason is not None and not any(name in SAFETY_CHECK_TYPES for name in failed):
        return CaseResult("unable", failed, reason)
    return CaseResult("fail" if failed else "pass", failed, reason)


def check_reference(case: EvalCase, known_skus: Collection[str] = ()) -> CaseResult:
    if case.reference is None:
        raise ValueError(f"case {case.id} has no reference")
    reference = case.reference
    record = RunRecord(
        replies=tuple(
            Reply(
                text=reply["text"],
                sources=tuple(reply.get("sources", ())),
                escalation=reply.get("escalation"),
            )
            for reply in reference["replies"]
        ),
        tool_calls=tuple(
            ToolCall(call["name"], call.get("args", {}))
            for call in reference.get("tool_calls", ())
        ),
        state=reference.get("state", {}),
    )
    return evaluate_case(case, record, known_skus)
