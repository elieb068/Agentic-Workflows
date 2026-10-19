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


MIN_SAMPLE = 10  # below this, percentages are anecdotes: the newsletter must not quote them


def findings(session: Session, *, now: datetime, days: int = 7) -> dict:
    """Aggregate, anonymous view of what the analyses found, for the weekly newsletter (/api/newsletter/stats).

    One row per site (its latest analysis that really ran), so a site checked twice counts once. No URL,
    domain or e-mail leaves this function.
    """
    from raioxia.db import CheckResultRow, Lead
    from raioxia.newsletter import WANTS_NEWSLETTER

    def summarize(since: datetime | None) -> dict:
        query = select(Analysis).where(Analysis.status == "done", Analysis.reused_from_id.is_(None))
        if since is not None:
            query = query.where(Analysis.finished_at >= since)
        latest: dict[str, Analysis] = {}
        for analysis in session.scalars(query.order_by(Analysis.finished_at)):
            latest[analysis.domain] = analysis
        sites = list(latest.values())
        n = len(sites)
        result: dict = {"sites": n, "enough_data": n >= MIN_SAMPLE}
        if not n:
            return result
        scores = [a.score for a in sites if a.score is not None]
        result["average_score"] = round(sum(scores) / len(scores)) if scores else None
        bands: dict[str, int] = {}
        for a in sites:
            bands[a.band_label or "?"] = bands.get(a.band_label or "?", 0) + 1
        result["bands_pct"] = {label: round(100 * count / n) for label, count in sorted(bands.items(), key=lambda kv: -kv[1])}
        per_check: dict[str, dict[str, int]] = {}
        for check_id, status, count in session.execute(
            select(CheckResultRow.check_id, CheckResultRow.status, func.count())
            .where(CheckResultRow.analysis_id.in_([a.id for a in sites]))
            .group_by(CheckResultRow.check_id, CheckResultRow.status)
        ):
            per_check.setdefault(check_id, {})[status] = count
        result["checks_pct"] = {
            check_id: {status: round(100 * count / n) for status, count in sorted(statuses.items())}
            for check_id, statuses in sorted(per_check.items())
        }
        failing = {c: s.get("erro", 0) + s.get("aviso", 0) for c, s in result["checks_pct"].items()}
        result["most_failed_check"] = max(failing, key=failing.get) if failing else None
        return result

    return {
        "period_days": days,
        "generated_at": now.isoformat(),
        "min_sample": MIN_SAMPLE,
        "period": summarize(now - timedelta(days=days)),
        "all_time": summarize(None),
        "subscribers": session.scalar(select(func.count()).select_from(Lead).where(WANTS_NEWSLETTER)) or 0,
    }
