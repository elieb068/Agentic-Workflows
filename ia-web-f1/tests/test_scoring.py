import pytest

from raioxia.config import load_config
from raioxia.models import CheckResult, Status
from raioxia.scoring import compute_score

SCORING = load_config().scoring


def r(status: Status, peso: float) -> CheckResult:
    return CheckResult(id="x", status=status, titulo="t", explicacao="e", peso=peso)


def test_all_ok_is_100():
    assert compute_score([r(Status.OK, 25), r(Status.OK, 6)], SCORING) == 100


def test_weighted_mix():
    # (25*1 + 10*0.5 + 15*0) / 50 = 0.6
    assert compute_score([r(Status.OK, 25), r(Status.WARNING, 10), r(Status.ERROR, 15)], SCORING) == 60


def test_unknown_leaves_denominator():
    assert compute_score([r(Status.OK, 10), r(Status.UNKNOWN, 90)], SCORING) == 100


def test_zero_weight_ignored():
    assert compute_score([r(Status.ERROR, 0), r(Status.OK, 5)], SCORING) == 100


@pytest.mark.parametrize("results", [[], [r(Status.UNKNOWN, 10)]])
def test_nothing_scorable_is_none(results):
    assert compute_score(results, SCORING) is None


def test_rounds_to_int():
    # (1*1 + 2*0) / 3 = 33.33
    assert compute_score([r(Status.OK, 1), r(Status.ERROR, 2)], SCORING) == 33
