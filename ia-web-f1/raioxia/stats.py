"""Simple daily metrics (America/Sao_Paulo days): requests, confirmations, outcomes, newsletter opt-ins."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import Date, Integer, cast, func, select
from sqlalchemy.orm import Session

from raioxia.db import Analysis

TIMEZONE = "America/Sao_Paulo"


@dataclass(frozen=True)
class DayStats:
    day: date
    requested: int
    confirmed: int
    done: int
    failed: int
    newsletter_opt_in: int

    @property
    def confirmation_rate(self) -> float | None:
        return self.confirmed / self.requested if self.requested else None

    @property
    def newsletter_rate(self) -> float | None:
        return self.newsletter_opt_in / self.requested if self.requested else None


def daily(session: Session, *, now: datetime, days: int = 30) -> list[DayStats]:
    day = cast(func.timezone(TIMEZONE, Analysis.created_at), Date)
    rows = session.execute(
        select(
            day.label("day"),
            func.count().label("requested"),
            func.count(Analysis.confirmed_at).label("confirmed"),
            func.count().filter(Analysis.status == "done").label("done"),
            func.count().filter(Analysis.status == "failed").label("failed"),
            func.sum(cast(Analysis.newsletter_opt_in, Integer)).label("newsletter_opt_in"),
        )
        .where(Analysis.created_at >= now - timedelta(days=days))
        .group_by(day)
        .order_by(day)
    ).all()
    return [DayStats(r.day, r.requested, r.confirmed, r.done, r.failed, r.newsletter_opt_in or 0) for r in rows]


def format_table(stats: list[DayStats]) -> str:
    def pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.0%}"

    lines = [f"{'dia':<10} {'pedidos':>7} {'confirm.':>8} {'taxa':>5} {'prontos':>7} {'falhas':>6} {'newsletter':>10}"]
    for s in stats:
        lines.append(
            f"{s.day.isoformat():<10} {s.requested:>7} {s.confirmed:>8} {pct(s.confirmation_rate):>5} "
            f"{s.done:>7} {s.failed:>6} {pct(s.newsletter_rate):>10}"
        )
    total = DayStats(date.min, *(sum(getattr(s, f) for s in stats) for f in ("requested", "confirmed", "done", "failed", "newsletter_opt_in")))
    lines.append(
        f"{'total':<10} {total.requested:>7} {total.confirmed:>8} {pct(total.confirmation_rate):>5} "
        f"{total.done:>7} {total.failed:>6} {pct(total.newsletter_rate):>10}"
    )
    return "\n".join(lines)
