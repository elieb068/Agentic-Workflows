"""Weighted score: sum(weight * status_value) / sum(weight) * 100, skipping indeterminado."""

from collections.abc import Iterable

from raioxia.config import ScoringConfig
from raioxia.models import CheckResult, Status


def compute_score(results: Iterable[CheckResult], scoring: ScoringConfig) -> int | None:
    earned = total = 0.0
    for result in results:
        if result.status == Status.UNKNOWN or result.peso <= 0:
            continue
        earned += result.peso * scoring.status_values[result.status]
        total += result.peso
    if total == 0:
        return None
    return round(earned / total * 100)
