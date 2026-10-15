"""LGPD: unsubscribe, data deletion and retention (purge).

Retention (approved 2026-10-14):
- unconfirmed requests: lead (if never confirmed), consents and analyses deleted after 7 days;
- reports: 180 days after they finish the report link stops working and the analysis is
  anonymized (no lead, no IP); results stay for statistics. Leads with nothing recent and no
  newsletter subscription are then deleted (consents and outbox rows go with them).
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, select, update
from sqlalchemy.orm import Session

from raioxia import pipeline
from raioxia.db import Analysis, Consent, EmailOutbox, Lead
from raioxia.tokens import new_token

log = logging.getLogger(__name__)

UNCONFIRMED_RETENTION = timedelta(days=7)
REPORT_RETENTION = timedelta(days=180)


def unsubscribe(session: Session, lead_id: int, *, ip: str | None, user_agent: str | None, now: datetime) -> bool:
    """Stop the newsletter (and with it every e-mail the person didn't ask for). Idempotent."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        return False
    if lead.unsubscribed_at is None or lead.newsletter:
        lead.newsletter = False
        lead.unsubscribed_at = now
        session.add(Consent(lead=lead, kind="newsletter", granted=False, text_version=pipeline.CONSENT_TEXT_VERSION, ip=ip, user_agent=user_agent))
        log.info("lead unsubscribed", extra={"lead_id": lead_id})
    return True


def subscribe(session: Session, lead: Lead, *, ip: str | None, user_agent: str | None, now: datetime) -> None:
    """Newsletter opt-in for an already confirmed e-mail (from the report page). Idempotent."""
    if lead.newsletter:
        return
    lead.newsletter = True
    lead.unsubscribed_at = None
    session.add(Consent(lead=lead, kind="newsletter", granted=True, text_version=pipeline.CONSENT_TEXT_VERSION, ip=ip, user_agent=user_agent))
    log.info("lead subscribed from report", extra={"lead_id": lead.id})


def find_lead(session: Session, email: str) -> Lead | None:
    return session.scalar(select(Lead).where(Lead.email == email))


def delete_lead_data(session: Session, lead_id: int) -> bool:
    """Erase everything about a person: analyses (and their results/e-mails), consents, outbox, the lead."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        return False
    session.execute(delete(Analysis).where(Analysis.lead_id == lead_id))
    session.delete(lead)
    session.flush()
    log.info("lead data deleted", extra={"lead_id": lead_id})
    return True


def purge(session: Session, *, now: datetime) -> dict[str, int]:
    counts = {"expired": pipeline.expire_unconfirmed(session, now=now)}

    unconfirmed_cutoff = now - UNCONFIRMED_RETENTION
    counts["unconfirmed_analyses_deleted"] = session.execute(
        delete(Analysis).where(Analysis.status.in_(("pending_confirmation", "expired")), Analysis.created_at < unconfirmed_cutoff)
    ).rowcount
    counts["unconfirmed_leads_deleted"] = session.execute(
        delete(Lead).where(
            Lead.confirmed_at.is_(None),
            Lead.created_at < unconfirmed_cutoff,
            ~exists().where(Analysis.lead_id == Lead.id),
        )
    ).rowcount

    report_cutoff = now - REPORT_RETENTION
    old = session.scalars(select(Analysis).where(Analysis.finished_at < report_cutoff, Analysis.lead_id.is_not(None))).all()
    for analysis in old:
        analysis.lead_id = None
        analysis.requested_ip = None
        analysis.report_token = new_token()  # nobody holds this one: the old link stops working
    counts["reports_anonymized"] = len(old)
    session.flush()

    recent = exists().where(Analysis.lead_id == Lead.id)
    counts["inactive_leads_deleted"] = session.execute(
        delete(Lead).where(Lead.newsletter.is_(False), Lead.created_at < report_cutoff, ~recent)
    ).rowcount
    counts["outbox_rows_deleted"] = session.execute(
        delete(EmailOutbox).where(EmailOutbox.status != "queued", EmailOutbox.created_at < report_cutoff)
    ).rowcount
    session.execute(update(Analysis).where(Analysis.finished_at < report_cutoff).values(requested_ip=None))
    log.info("purge done", extra=counts)
    return counts
