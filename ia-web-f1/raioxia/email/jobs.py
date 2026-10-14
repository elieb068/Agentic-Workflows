"""The e-mail job, run by its own worker on the "email" queue. The only code that calls a mail provider."""

import logging
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache

from sqlalchemy.orm import Session

from raioxia.db import EmailOutbox
from raioxia.db.engine import session_scope
from raioxia.email.base import EmailError, EmailSender
from raioxia.email.outbox import RETRY, SECRET_CONTEXT_KEYS
from raioxia.email.render import render
from raioxia.settings import Settings, get_settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 1 + RETRY.max


class Outcome(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"  # already sent / gone: duplicate job
    RETRY = "retry"  # transient failure, RQ will run the job again
    FAILED = "failed"  # permanent failure or out of attempts


class TransientSendFailure(Exception):
    """Raised after the attempt is recorded, so RQ schedules the next retry."""


def deliver(session: Session, outbox_id: int, *, sender: EmailSender, settings: Settings, now: datetime) -> Outcome:
    row = session.get(EmailOutbox, outbox_id, with_for_update=True)
    if row is None or row.status != "queued":
        return Outcome.SKIPPED
    row.attempts += 1
    try:
        email = render(row.kind, row.context, to=row.to_address, lead_id=row.lead_id, settings=settings, now=now)
        message_id = sender.send(email)
    except EmailError as exc:
        row.last_error = str(exc)[:2000]
        if exc.transient and row.attempts < MAX_ATTEMPTS:
            log.warning("e-mail send failed, will retry", extra={"outbox_id": row.id, "attempt": row.attempts, "error": str(exc)})
            return Outcome.RETRY
        row.status = "failed"
        log.error("e-mail send failed for good", extra={"outbox_id": row.id, "attempt": row.attempts, "error": str(exc)})
        return Outcome.FAILED
    row.status = "sent"
    row.sent_at = now
    row.provider_message_id = message_id[:128]
    row.last_error = None
    row.context = {k: v for k, v in row.context.items() if k not in SECRET_CONTEXT_KEYS}
    log.info("e-mail sent", extra={"outbox_id": row.id, "kind": row.kind, "attempt": row.attempts})
    return Outcome.SENT


@lru_cache(maxsize=1)
def get_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_sender == "gmail":
        from raioxia.email.gmail import GmailApiSender

        return GmailApiSender.from_settings(settings)
    from raioxia.email.base import ConsoleSender

    return ConsoleSender(from_name=settings.email_from_name, from_address=settings.email_from_address)


def send_email(outbox_id: int) -> str:
    """RQ entry point."""
    with session_scope() as session:
        outcome = deliver(session, outbox_id, sender=get_sender(), settings=get_settings(), now=datetime.now(UTC))
    if outcome is Outcome.RETRY:
        raise TransientSendFailure(f"outbox {outbox_id}: transient failure, retrying")
    return outcome.value
