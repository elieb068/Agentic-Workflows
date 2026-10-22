"""Newsletter subscribers in Brevo: Raio-X keeps the consent (leads + consents), Brevo only the sending list.

State reconciliation instead of one event per change: `leads.brevo_synced` says whether the contact is in the
Brevo list, and `sync` makes Brevo match what each lead wants (subscribed, confirmed, not unsubscribed). It is
idempotent, so callers just dispatch it after committing a consent change, and the daily purge runs it too (a
missed job or a Brevo outage only delays it). Deleted leads leave their e-mail in `brevo_removals` until Brevo
deletes the contact. Unsubscribing removes the contact from Brevo entirely: the history stays in `consents`.

Unsubscribes that happen in Brevo (its own link, hard bounces, spam complaints) come back through the webhook
in raioxia.api.routes and go through privacy.unsubscribe like any other.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from rq import Queue, Retry
from sqlalchemy import and_, func, not_, select
from sqlalchemy.orm import Session

from raioxia.db import BrevoRemoval, Lead
from raioxia.settings import Settings

log = logging.getLogger(__name__)

API = "https://api.brevo.com/v3"
RETRY = Retry(max=3, interval=[60, 300, 1800])
# Brevo webhook events that mean "stop sending to this person".
STOP_EVENTS = {"unsubscribed", "unsubscribe", "hard_bounce", "hardbounce", "spam", "blocked", "contact_deleted"}
# A removal still waiting after this means Brevo has been failing for longer than the daily purge + RQ retries cover.
STALE_AFTER = timedelta(hours=48)


class BrevoError(Exception):
    pass


WANTS_NEWSLETTER = and_(Lead.newsletter.is_(True), Lead.unsubscribed_at.is_(None), Lead.confirmed_at.is_not(None))


def wants_newsletter(lead: Lead) -> bool:
    return lead.newsletter and lead.unsubscribed_at is None and lead.confirmed_at is not None


@dataclass
class BrevoClient:
    api_key: str
    list_id: int
    http: httpx.Client

    @classmethod
    def from_settings(cls, settings: Settings, http: httpx.Client | None = None) -> "BrevoClient | None":
        if settings.brevo_api_key is None or settings.brevo_list_id is None:
            return None
        return cls(settings.brevo_api_key.get_secret_value(), settings.brevo_list_id, http or httpx.Client(timeout=15))

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self.http.request(method, f"{API}{path}", headers={"api-key": self.api_key, "accept": "application/json"}, **kwargs)
        except httpx.HTTPError as exc:
            raise BrevoError(f"{method} {path}: {type(exc).__name__}") from exc
        return response

    def add(self, email: str) -> None:
        """Create or update the contact and put it in the newsletter list (no other data leaves Raio-X)."""
        response = self._request("POST", "/contacts", json={"email": email, "listIds": [self.list_id], "updateEnabled": True})
        if response.status_code not in (200, 201, 204):
            raise BrevoError(f"add contact: HTTP {response.status_code} {response.text[:200]}")

    def remove(self, email: str) -> None:
        """Delete the contact from Brevo altogether. Already gone is fine."""
        response = self._request("DELETE", f"/contacts/{quote(email, safe='')}", params={"identifierType": "email_id"})
        if response.status_code not in (200, 204, 404):
            raise BrevoError(f"delete contact: HTTP {response.status_code} {response.text[:200]}")


def sync(session: Session, client: BrevoClient) -> dict[str, int]:
    """Make Brevo match Raio-X. Commits after each contact, so a failure halfway keeps what was done."""
    counts = {"added": 0, "removed": 0, "deleted_leads_removed": 0, "failed": 0}
    pending = [
        (lead_id, email, True) for lead_id, email in session.execute(
            select(Lead.id, Lead.email).where(WANTS_NEWSLETTER, Lead.brevo_synced.is_(False))
        )
    ] + [
        (lead_id, email, False) for lead_id, email in session.execute(
            select(Lead.id, Lead.email).where(Lead.brevo_synced.is_(True), not_(WANTS_NEWSLETTER))
        )
    ]
    for lead_id, email, add in pending:
        try:
            client.add(email) if add else client.remove(email)
        except BrevoError as exc:
            counts["failed"] += 1
            log.warning("brevo sync failed", extra={"lead_id": lead_id, "error": str(exc)})
            continue
        lead = session.get(Lead, lead_id)
        if lead is not None:
            lead.brevo_synced = add
        session.commit()
        counts["added" if add else "removed"] += 1

    for removal in session.scalars(select(BrevoRemoval)).all():
        try:
            client.remove(removal.email)
        except BrevoError as exc:
            counts["failed"] += 1
            log.warning("brevo removal failed", extra={"removal_id": removal.id, "error": str(exc)})
            continue
        session.delete(removal)
        session.commit()
        counts["deleted_leads_removed"] += 1
    if any(counts.values()):
        log.info("brevo sync", extra=counts)
    return counts


def sync_status(session: Session, *, now: datetime) -> dict:
    """Backlog Brevo should already have caught up on, for the weekly review (/api/newsletter/stats).

    The daily purge and the RQ retries clear it within a day, so anything waiting longer than STALE_AFTER means
    Brevo has been failing: people who unsubscribed or deleted their data would still get the next edition.
    """
    to_remove = and_(Lead.brevo_synced.is_(True), not_(WANTS_NEWSLETTER))
    oldest_removal = session.scalar(select(func.min(BrevoRemoval.created_at)))
    oldest_unsubscribe = session.scalar(select(func.min(Lead.unsubscribed_at)).where(to_remove))
    waiting = [t for t in (oldest_removal, oldest_unsubscribe) if t is not None]
    oldest = min(waiting) if waiting else None
    return {
        "pending_removals": (session.scalar(select(func.count()).select_from(BrevoRemoval)) or 0)
        + (session.scalar(select(func.count()).select_from(Lead).where(to_remove)) or 0),
        "pending_additions": session.scalar(
            select(func.count()).select_from(Lead).where(WANTS_NEWSLETTER, Lead.brevo_synced.is_(False))
        ) or 0,
        "oldest_pending_hours": round((now - oldest).total_seconds() / 3600) if oldest else None,
        "stale": oldest is not None and now - oldest > STALE_AFTER,
    }


def forget(session: Session, lead: Lead) -> None:
    """Before deleting a lead: keep its e-mail just long enough to delete the Brevo contact."""
    if lead.brevo_synced:
        session.add(BrevoRemoval(email=lead.email))


def sync_job() -> dict[str, int]:
    """RQ job (email queue). Raises on failures so RQ retries; does nothing when Brevo is not configured."""
    from raioxia.db.engine import session_scope
    from raioxia.settings import get_settings

    client = BrevoClient.from_settings(get_settings())
    if client is None:
        return {}
    with client.http, session_scope() as session:
        counts = sync(session, client)
    if counts["failed"]:
        raise BrevoError(f"{counts['failed']} contact(s) not synced; will retry")
    return counts


def dispatch_sync(queue: Queue, settings: Settings) -> None:
    """Call after committing a consent change. Never fails the request: the daily purge syncs anyway."""
    if settings.brevo_api_key is None:
        return
    try:
        queue.enqueue(sync_job, retry=RETRY, job_timeout=300, result_ttl=3600, failure_ttl=7 * 86400)
    except Exception:  # noqa: BLE001 - Redis down: the daily purge catches up
        log.warning("brevo sync not dispatched", exc_info=True)
