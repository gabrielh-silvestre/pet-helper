"""The Wilson 95% interval the analysis and the label tooling share (pure, no model)."""

import pytest
from pet_helper.evals.stats import wilson_interval


def test_it_should_give_a_lower_bound_of_about_065_for_7_of_7():
    lower, upper = wilson_interval(7, 7)

    assert lower == pytest.approx(0.65, abs=0.005)
    assert upper == pytest.approx(1.0)


def test_it_should_give_058_to_092_for_16_of_20():
    lower, upper = wilson_interval(16, 20)

    assert (lower, upper) == pytest.approx((0.58, 0.92), abs=0.005)


def test_it_should_give_a_lower_bound_of_zero_for_no_success():
    lower, upper = wilson_interval(0, 5)

    assert lower == pytest.approx(0.0, abs=1e-9)
    assert upper == pytest.approx(0.43, abs=0.005)


def test_it_should_give_an_upper_bound_of_one_for_all_successes():
    _, upper = wilson_interval(5, 5)

    assert upper == pytest.approx(1.0)


def test_it_should_be_symmetric_between_successes_and_failures():
    lower, upper = wilson_interval(3, 20)

    flipped_lower, flipped_upper = wilson_interval(17, 20)

    assert (lower, upper) == pytest.approx((1 - flipped_upper, 1 - flipped_lower))


def test_it_should_widen_the_interval_when_the_sample_shrinks():
    small = wilson_interval(2, 3)
    large = wilson_interval(20, 30)

    assert small[1] - small[0] > large[1] - large[0]


def test_it_should_return_the_whole_range_when_there_are_no_items():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_it_should_reject_more_successes_than_items():
    with pytest.raises(ValueError):
        wilson_interval(4, 3)


def test_it_should_reject_a_negative_count():
    with pytest.raises(ValueError):
        wilson_interval(-1, 3)
