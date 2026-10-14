"""The only way the rest of the app sends e-mail: write an outbox row, then dispatch it to the "email" queue.

Callers commit first and dispatch after, so the worker always finds the row:

    row = queue_report_email(session, analysis, base_url=...)
    session.commit()
    dispatch(email_queue, [row.id])

`requeue_stale` re-dispatches rows that never reached the queue (e.g. Redis was down at commit time);
sending is idempotent, so a duplicate job is harmless.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from rq import Queue, Retry
from sqlalchemy import select
from sqlalchemy.orm import Session

from raioxia.db import Analysis, EmailOutbox, Lead
from raioxia.pipeline import CONFIRM_TTL

# Context keys holding secrets (links with tokens): removed from the row once the message is sent.
SECRET_CONTEXT_KEYS = ("confirm_url", "delete_url")
RETRY = Retry(max=3, interval=[60, 300, 1800])  # 1 min, 5 min, 30 min
DELETION_LINK_TTL = timedelta(hours=24)
TOP_PROBLEMS = 3
_SEVERITY = {"erro": 0, "aviso": 1}


def queue_email(session: Session, *, kind: str, to: str, lead_id: int | None, analysis_id: int | None, context: dict) -> EmailOutbox:
    row = EmailOutbox(kind=kind, to_address=to, lead_id=lead_id, analysis_id=analysis_id, context=context)
    session.add(row)
    session.flush()
    return row


def _recipient(analysis: Analysis) -> Lead:
    if analysis.lead is None:
        raise ValueError(f"analysis {analysis.id} has no lead to e-mail")
    return analysis.lead


def queue_confirm_email(session: Session, analysis: Analysis, confirm_token: str, *, base_url: str) -> EmailOutbox:
    lead = _recipient(analysis)
    context = {
        "domain": analysis.domain,
        "confirm_url": f"{base_url}/confirmar/{confirm_token}",
        "expires_hours": int(CONFIRM_TTL.total_seconds() // 3600),
    }
    return queue_email(session, kind="confirm", to=lead.email, lead_id=lead.id, analysis_id=analysis.id, context=context)


def top_problems(analysis: Analysis, limit: int = TOP_PROBLEMS) -> list[dict]:
    """Errors before warnings, heavier checks first."""
    problems = [r for r in analysis.results if r.status in _SEVERITY]
    problems.sort(key=lambda r: (_SEVERITY[r.status], -r.peso, r.position))
    return [{"check_id": r.check_id, "titulo": r.titulo, "status": r.status} for r in problems[:limit]]


def queue_report_email(session: Session, analysis: Analysis, *, base_url: str) -> EmailOutbox:
    lead = _recipient(analysis)
    context = {
        "domain": analysis.domain,
        "score": analysis.score,
        "band_label": analysis.band_label,
        "problems": top_problems(analysis),
        "report_url": f"{base_url}/r/{analysis.report_token}",
    }
    return queue_email(session, kind="report", to=lead.email, lead_id=lead.id, analysis_id=analysis.id, context=context)


def queue_failed_email(session: Session, analysis: Analysis) -> EmailOutbox:
    lead = _recipient(analysis)
    context = {"domain": analysis.domain, "error_message": analysis.error_message or ""}
    return queue_email(session, kind="failed", to=lead.email, lead_id=lead.id, analysis_id=analysis.id, context=context)


def queue_deletion_email(session: Session, lead: Lead, delete_token: str, *, base_url: str) -> EmailOutbox:
    context = {
        "delete_url": f"{base_url}/excluir-dados/{delete_token}",
        "expires_hours": int(DELETION_LINK_TTL.total_seconds() // 3600),
    }
    return queue_email(session, kind="deletion", to=lead.email, lead_id=lead.id, analysis_id=None, context=context)


def dispatch(queue: Queue, outbox_ids: Iterable[int]) -> None:
    from raioxia.email.jobs import send_email  # the job module imports this one

    for outbox_id in outbox_ids:
        queue.enqueue(send_email, outbox_id, retry=RETRY, job_timeout=60, result_ttl=3600, failure_ttl=7 * 86400)


def requeue_stale(session: Session, queue: Queue, *, now: datetime, older_than: timedelta = timedelta(minutes=45)) -> int:
    """Re-dispatch rows still queued long after their last retry window (default > 1+5+30 min)."""
    ids = session.scalars(
        select(EmailOutbox.id).where(EmailOutbox.status == "queued", EmailOutbox.created_at < now - older_than)
    ).all()
    dispatch(queue, ids)
    return len(ids)
