from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pet_helper.evals import stats


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    @property
    def measured(self) -> bool:
        return self.total > 0


@dataclass(frozen=True)
class CriterionRates:
    tpr: Rate
    tnr: Rate
    # coverage lost to `unable` is reported, but two judges that differ only there are equal
    unable: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str


def rates_from_pairs(pairs: Sequence[tuple[str, str]]) -> CriterionRates:
    # an `unable` verdict is coverage: neither a hit nor a miss
    judged = [(label, verdict) for label, verdict in pairs if verdict != "unable"]

    def rate(label: str) -> Rate:
        verdicts = [verdict for human, verdict in judged if human == label]
        return Rate(verdicts.count(label), len(verdicts))

    unable = len(pairs) - len(judged)
    return CriterionRates(tpr=rate("pass"), tnr=rate("fail"), unable=unable)


def compare(current: CriterionRates, candidate: CriterionRates) -> Decision:
    both = [
        (before, after)
        for before, after in (
            (current.tpr, candidate.tpr),
            (current.tnr, candidate.tnr),
        )
        if before.measured and after.measured
    ]
    if not both:
        return Decision(False, "no_measure")
    # hits/total compared by cross-multiplication, so no float decides a tie
    if any(
        after.hits * before.total < before.hits * after.total for before, after in both
    ):
        return Decision(False, "worse")
    return Decision(True, "better_or_equal")


def _outcome(decision: Decision) -> str:
    if decision.accepted:
        return "accept"
    return "reject" if decision.reason == "worse" else "keep"


def format_report(
    criterion: str,
    current: CriterionRates,
    candidate: CriterionRates,
    decision: Decision,
) -> str:
    lines = [f"{criterion}: {_outcome(decision)} ({decision.reason})"]
    for name, before, after in (
        ("TPR", current.tpr, candidate.tpr),
        ("TNR", current.tnr, candidate.tnr),
    ):
        sides = []
        for side, rate in (("current", before), ("candidate", after)):
            if rate.measured:
                low, high = stats.wilson_interval(rate.hits, rate.total)
                interval = f"{low:.2f}-{high:.2f}"
            else:
                interval = "undefined"
            sides.append(f"{side} {rate.hits}/{rate.total} wilson95 {interval}")
        lines.append(f"  {name} {' '.join(sides)}")
    lines.append(f"  unable current {current.unable} candidate {candidate.unable}")
    return "\n".join(lines)


def validate_candidate(validation: Callable[..., int], round_dir: Path) -> int:
    # the one place that forces a second read of the test split, after the owner confirmed it
    return validation(round_dir, force_new_validation=True)
