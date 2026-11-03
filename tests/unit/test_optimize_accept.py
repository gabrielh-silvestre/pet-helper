"""The acceptance rule of a judge candidate (no model, no dspy).

Assumed public surface of `pet_helper.optimize.accept`:

- `Rate(hits: int, total: int)`: frozen; `measured` is `total > 0` (0/0 is unmeasured, never a tie).
- `CriterionRates(tpr: Rate, tnr: Rate)`: frozen; the true-positive rate (human `pass` judged `pass`)
  and the true-negative rate (human `fail` judged `fail`) of one criterion on the test split.
- `rates_from_pairs(pairs: Sequence[tuple[str, str]]) -> CriterionRates`: pairs are `(human label,
  judge verdict)`; a verdict `unable` is coverage, counted as neither hit nor miss (as `labels tpr-tnr` does).
- `Decision(accepted: bool, reason: str)`: frozen; reason is `better_or_equal`, `worse` or `no_measure`.
- `compare(current: CriterionRates, candidate: CriterionRates) -> Decision`: a metric unmeasured on either
  side is left out; with none left, `Decision(False, "no_measure")` and the current prompt stays; any
  measured metric worse than the current one is `Decision(False, "worse")`; otherwise
  `Decision(True, "better_or_equal")` (a tie accepts).
- `format_report(criterion: str, current: CriterionRates, candidate: CriterionRates, decision: Decision)
  -> str`: one text with, for each of TPR and TNR, the raw counts `k/n` of both sides and the Wilson
  interval as `wilson95 <low>-<high>` (two decimals, `stats.wilson_interval`), `undefined` for 0/0, and
  the outcome as one of the words `accept`, `reject` or `keep` (the current prompt stays).
"""

import pytest

from pet_helper.evals.stats import wilson_interval
from pet_helper.optimize.accept import (
    CriterionRates,
    Decision,
    Rate,
    compare,
    format_report,
    rates_from_pairs,
)

UNMEASURED = Rate(0, 0)


def rates(tpr: Rate, tnr: Rate) -> CriterionRates:
    return CriterionRates(tpr=tpr, tnr=tnr)


def wilson_text(hits: int, total: int) -> str:
    low, high = wilson_interval(hits, total)
    return f"wilson95 {low:.2f}-{high:.2f}"


def test_it_should_accept_a_candidate_equal_to_the_current_judge():
    current = rates(Rate(3, 4), Rate(2, 2))

    decision = compare(current, current)

    assert decision == Decision(True, "better_or_equal")


def test_it_should_accept_a_candidate_better_on_one_metric_and_equal_on_the_other():
    current = rates(Rate(2, 4), Rate(2, 2))
    candidate = rates(Rate(3, 4), Rate(2, 2))

    decision = compare(current, candidate)

    assert decision == Decision(True, "better_or_equal")


def test_it_should_reject_a_candidate_with_a_worse_true_positive_rate():
    current = rates(Rate(3, 4), Rate(1, 2))
    candidate = rates(Rate(2, 4), Rate(2, 2))

    decision = compare(current, candidate)

    assert decision == Decision(False, "worse")


def test_it_should_reject_a_candidate_with_a_worse_true_negative_rate():
    current = rates(Rate(2, 4), Rate(2, 2))
    candidate = rates(Rate(4, 4), Rate(1, 2))

    decision = compare(current, candidate)

    assert decision == Decision(False, "worse")


def test_it_should_compare_rates_not_counts_when_the_totals_differ():
    current = rates(Rate(1, 2), Rate(1, 1))
    candidate = rates(Rate(2, 4), Rate(2, 2))

    decision = compare(current, candidate)

    assert decision == Decision(True, "better_or_equal")


def test_it_should_leave_out_a_metric_the_current_judge_could_not_measure():
    current = rates(UNMEASURED, Rate(2, 2))
    candidate = rates(Rate(4, 4), Rate(2, 2))

    decision = compare(current, candidate)

    assert decision == Decision(True, "better_or_equal")


def test_it_should_reject_on_a_measured_metric_even_when_the_other_one_is_unmeasured():
    current = rates(Rate(3, 4), UNMEASURED)
    candidate = rates(Rate(1, 4), Rate(2, 2))

    decision = compare(current, candidate)

    assert decision == Decision(False, "worse")


def test_it_should_keep_the_current_prompt_when_no_metric_is_measured_on_both_sides():
    current = rates(UNMEASURED, Rate(2, 2))
    candidate = rates(Rate(4, 4), UNMEASURED)

    decision = compare(current, candidate)

    assert decision == Decision(False, "no_measure")


def test_it_should_keep_the_current_prompt_of_a_criterion_with_no_labelled_item():
    nothing = rates(UNMEASURED, UNMEASURED)

    decision = compare(nothing, nothing)

    assert decision == Decision(False, "no_measure")


def test_it_should_count_hits_per_class_from_label_and_verdict_pairs():
    pairs = [("pass", "pass"), ("pass", "fail"), ("fail", "fail"), ("fail", "pass")]

    result = rates_from_pairs(pairs)

    assert result == rates(Rate(1, 2), Rate(1, 2))


def test_it_should_ignore_unable_verdicts_when_counting_rates():
    pairs = [("pass", "pass"), ("pass", "unable"), ("fail", "unable")]

    result = rates_from_pairs(pairs)

    assert result == rates(Rate(1, 1), UNMEASURED)


def test_it_should_leave_both_rates_unmeasured_without_any_pair():
    result = rates_from_pairs([])

    assert result == rates(UNMEASURED, UNMEASURED)


def test_it_should_report_the_raw_counts_and_the_wilson_interval_of_both_sides():
    current = rates(Rate(3, 4), Rate(2, 2))
    candidate = rates(Rate(4, 4), Rate(1, 2))
    decision = compare(current, candidate)

    report = format_report("escalation_judgment", current, candidate, decision)

    for expected in ("escalation_judgment", "TPR", "TNR", "3/4", "4/4", "2/2", "1/2"):
        assert expected in report
    for hits, total in ((3, 4), (4, 4), (2, 2), (1, 2)):
        assert wilson_text(hits, total) in report


def test_it_should_report_an_unmeasured_metric_as_undefined():
    current = rates(UNMEASURED, Rate(2, 2))
    candidate = rates(Rate(1, 1), Rate(2, 2))
    decision = compare(current, candidate)

    report = format_report("tone", current, candidate, decision)

    assert "undefined" in report


@pytest.mark.parametrize(
    ("current", "candidate", "word"),
    [
        (rates(Rate(1, 2), Rate(1, 2)), rates(Rate(1, 2), Rate(1, 2)), "accept"),
        (rates(Rate(2, 2), Rate(1, 2)), rates(Rate(1, 2), Rate(1, 2)), "reject"),
        (rates(UNMEASURED, UNMEASURED), rates(UNMEASURED, UNMEASURED), "keep"),
    ],
)
def test_it_should_state_the_outcome_in_the_report(current, candidate, word):
    decision = compare(current, candidate)

    report = format_report("dont_know_accuracy", current, candidate, decision)

    assert word in report.lower()
