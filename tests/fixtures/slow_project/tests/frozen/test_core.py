import pytest

from slowpkg.core import closed_form, compute, slow_sum


@pytest.mark.parametrize("n", [0, 1, 10, 1000])
def test_AC1__slow_matches_closed_form(n):
    assert slow_sum(n) == closed_form(n)


def test_AC1__compute_is_zero():
    assert compute(5000) == 0


def test_AC2__deliberately_wrong():
    assert compute(10) == 1, "fixture failure on purpose"


def test_helper_without_criterion():
    assert True
