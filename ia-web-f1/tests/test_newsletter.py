from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from raioxia import newsletter, privacy, stats
from raioxia.db import BrevoRemoval, Consent, Lead
from raioxia.newsletter import BrevoClient, BrevoError
from raioxia.settings import Settings
from tests.test_privacy import NOW, count, done, submit

pytestmark = pytest.mark.db

WEBHOOK_SECRET = "w" * 32
STATS_TOKEN = "s" * 32


RUNS: list[int] = []


def fake_sync_job():  # RQ only enqueues importable functions
    RUNS.append(1)


@pytest.fixture
def runs(monkeypatch):
    RUNS.clear()
    monkeypatch.setattr(newsletter, "sync_job", fake_sync_job)
    return RUNS


class FakeBrevo:
    def __init__(self):
        self.contacts: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.failing: set[str] = set()

    def add(self, email):
        self.calls.append(("add", email))
        if email in self.failing:
            raise BrevoError("boom")
        self.contacts.add(email)

    def remove(self, email):
        self.calls.append(("remove", email))
        if email in self.failing:
            raise BrevoError("boom")
        self.contacts.discard(email)


def test_sync_adds_only_confirmed_subscribers_and_removes_the_rest(db):
    ana = done(db, "ana@exemplo.com.br", newsletter=True).lead
    done(db, "bia@exemplo.com.br", "outro.com.br", newsletter=False)
    submit(db, "caio@exemplo.com.br", "terceiro.com.br", newsletter=True)  # never confirmed: no newsletter
    brevo = FakeBrevo()

    assert newsletter.sync(db, brevo) == {"added": 1, "removed": 0, "deleted_leads_removed": 0, "failed": 0}
    assert brevo.contacts == {"ana@exemplo.com.br"} and ana.brevo_synced
    assert newsletter.sync(db, brevo)["added"] == 0  # idempotent

    privacy.unsubscribe(db, ana.id, ip=None, user_agent=None, now=NOW)
    assert newsletter.sync(db, brevo)["removed"] == 1
    assert brevo.contacts == set() and not ana.brevo_synced


def test_failed_contacts_stay_pending(db):
    ana = done(db, "ana@exemplo.com.br", newsletter=True).lead
    brevo = FakeBrevo()
    brevo.failing.add("ana@exemplo.com.br")
    assert newsletter.sync(db, brevo)["failed"] == 1 and not ana.brevo_synced
    brevo.failing.clear()
    assert newsletter.sync(db, brevo)["added"] == 1 and ana.brevo_synced


def test_deleting_a_subscriber_removes_the_brevo_contact_and_keeps_nothing(db):
    ana = done(db, "ana@exemplo.com.br", newsletter=True).lead
    brevo = FakeBrevo()
    newsletter.sync(db, brevo)
    privacy.delete_lead_data(db, ana.id)
    db.flush()
    assert db.scalars(select(BrevoRemoval.email)).all() == ["ana@exemplo.com.br"]
    assert newsletter.sync(db, brevo)["deleted_leads_removed"] == 1
    assert brevo.contacts == set() and count(db, BrevoRemoval) == 0
    # never in Brevo: nothing to remember
    bia = done(db, "bia@exemplo.com.br", "outro.com.br").lead
    privacy.delete_lead_data(db, bia.id)
    assert count(db, BrevoRemoval) == 0


def test_sync_status_flags_a_backlog_brevo_never_cleared(db):
    assert newsletter.sync_status(db, now=NOW) == {"pending_removals": 0, "pending_additions": 0, "oldest_pending_hours": None, "stale": False}
    ana = done(db, "ana@exemplo.com.br", newsletter=True).lead
    bia = done(db, "bia@exemplo.com.br", "outro.com.br", newsletter=True).lead
    assert newsletter.sync_status(db, now=NOW)["pending_additions"] == 2
    newsletter.sync(db, FakeBrevo())
    # Brevo down from here on: a deletion and an unsubscribe wait for it
    db.add(BrevoRemoval(email="carla@exemplo.com.br", created_at=NOW - timedelta(hours=10)))
    privacy.unsubscribe(db, bia.id, ip=None, user_agent=None, now=NOW - timedelta(hours=30))
    db.flush()
    status = newsletter.sync_status(db, now=NOW)
    assert status == {"pending_removals": 2, "pending_additions": 0, "oldest_pending_hours": 30, "stale": False}
    assert newsletter.sync_status(db, now=NOW + timedelta(hours=20))["stale"]
    assert ana.brevo_synced  # still subscribed: not part of the backlog


def test_purge_keeps_a_lead_until_brevo_forgot_it(db):
    old = NOW - timedelta(days=400)
    lead = done(db, "ana@exemplo.com.br", at=old, newsletter=True).lead
    lead.brevo_synced = True
    lead.newsletter = False  # unsubscribed, but the sync has not run yet
    lead.unsubscribed_at = old
    db.flush()
    privacy.purge(db, now=NOW)
    assert db.get(Lead, lead.id) is not None
    lead.brevo_synced = False
    db.flush()
    privacy.purge(db, now=NOW)
    assert db.get(Lead, lead.id) is None


def test_brevo_client_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.params.get("identifierType"), request.headers["api-key"], request.content))
        if request.method == "DELETE" and "gone" in request.url.path:
            return httpx.Response(404)
        if request.method == "POST" and b"bad@" in request.content:
            return httpx.Response(400, json={"code": "invalid_parameter"})
        return httpx.Response(201 if request.method == "POST" else 204)

    client = BrevoClient("key-123", 7, httpx.Client(transport=httpx.MockTransport(handler)))
    client.add("ana@exemplo.com.br")
    client.remove("ana+x@exemplo.com.br")
    client.remove("gone@exemplo.com.br")  # 404 = already gone
    with pytest.raises(BrevoError):
        client.add("bad@exemplo.com.br")
    assert seen[0][:2] == ("POST", "/v3/contacts") and seen[0][3] == "key-123"
    assert b'"listIds":[7]' in seen[0][4].replace(b" ", b"") and b'"updateEnabled":true' in seen[0][4].replace(b" ", b"")
    assert seen[1][:3] == ("DELETE", "/v3/contacts/ana+x@exemplo.com.br", "email_id")


def test_settings_need_a_list_with_the_key():
    with pytest.raises(ValueError, match="BREVO_LIST_ID"):
        Settings(secret_key="k" * 40, brevo_api_key="xkeysib-1")
    assert BrevoClient.from_settings(Settings(secret_key="k" * 40)) is None


def test_dispatch_only_when_configured(pipeline_env, runs):
    queue = pipeline_env.queues["email"]
    newsletter.dispatch_sync(queue, pipeline_env.settings)
    assert runs == []
    pipeline_env.settings.brevo_api_key = SecretStr("xkeysib-1")
    pipeline_env.settings.brevo_list_id = 3
    newsletter.dispatch_sync(queue, pipeline_env.settings)
    assert runs == [1]


def test_consent_changes_trigger_a_sync(client, pipeline_env, runs):
    pipeline_env.settings.brevo_api_key = SecretStr("xkeysib-1")
    pipeline_env.settings.brevo_list_id = 3
    from tests.test_web_ads import FORM, report_location

    report_location(client, pipeline_env)  # confirmed without newsletter: no sync
    assert runs == []
    client.post("/pedido", data=FORM | {"url": "outro.com.br", "newsletter": "1"})
    email = next(e for e in reversed(pipeline_env.sender.sent) if e.subject.startswith("Confirme"))
    import re

    link = re.search(r"https://raiox\.primesolutions\.net\.br(/confirmar/\S+)", email.text).group(1)
    client.get(link)
    assert runs == [1]


def test_brevo_webhook_unsubscribes(client, pipeline_env, db, runs):
    pipeline_env.settings.brevo_api_key = SecretStr("xkeysib-1")
    pipeline_env.settings.brevo_list_id = 3
    lead = done(db, "ana@exemplo.com.br", newsletter=True).lead
    db.commit()

    event = {"event": "unsubscribed", "email": "Ana@Exemplo.com.br"}
    assert client.post("/webhooks/brevo/errado", json=event).status_code == 404  # no secret configured
    pipeline_env.settings.brevo_webhook_secret = SecretStr(WEBHOOK_SECRET)
    assert client.post("/webhooks/brevo/" + "x" * 32, json=event).status_code == 404
    assert client.post("/webhooks/brevo/" + WEBHOOK_SECRET, json={"event": "opened", "email": "ana@exemplo.com.br"}).json()["unsubscribed"] == 0
    response = client.post("/webhooks/brevo/" + WEBHOOK_SECRET, json=[event, {"event": "spam", "email": "ninguem@x.com"}])
    assert response.status_code == 200 and response.json() == {"ok": True, "unsubscribed": 1} and runs == [1]
    db.expire_all()
    lead = db.get(Lead, lead.id)
    assert not lead.newsletter and lead.unsubscribed_at is not None
    withdrawal = db.scalars(select(Consent).where(Consent.lead_id == lead.id, Consent.granted.is_(False))).one()
    assert withdrawal.user_agent == "brevo webhook: unsubscribed"
    assert client.post("/webhooks/brevo/" + WEBHOOK_SECRET, json=event).json()["unsubscribed"] == 0  # idempotent


def test_newsletter_stats_are_anonymous_and_need_the_token(client, pipeline_env, db):
    for i in range(3):
        done(db, f"p{i}@exemplo.com.br", f"site{i}.com.br", newsletter=i == 0)
    done(db, "p9@exemplo.com.br", "site0.com.br", at=NOW + timedelta(days=2))  # reuses site0: counted once
    db.commit()
    assert client.get("/api/newsletter/stats").status_code == 401
    pipeline_env.settings.newsletter_stats_token = SecretStr(STATS_TOKEN)
    assert client.get("/api/newsletter/stats", headers={"Authorization": "Bearer nope"}).status_code == 401

    body = stats.findings(db, now=NOW + timedelta(days=3), days=7)
    assert body["period"]["sites"] == 3 and not body["period"]["enough_data"] and body["subscribers"] == 1
    assert body["period"]["average_score"] == 71 and sum(body["period"]["bands_pct"].values()) == 100
    assert body["period"]["most_failed_check"] in body["period"]["checks_pct"]
    assert body["brevo_sync"]["pending_additions"] == 1 and not body["brevo_sync"]["stale"]
    response = client.get("/api/newsletter/stats?days=30", headers={"Authorization": f"Bearer {STATS_TOKEN}"})
    assert response.status_code == 200 and response.json()["period_days"] == 30
    text = response.text
    assert "exemplo.com.br" not in text and "site0" not in text and "@" not in text


def test_empty_env_values_mean_unset(monkeypatch):
    for name in ("BREVO_API_KEY", "BREVO_LIST_ID", "BREVO_WEBHOOK_SECRET", "NEWSLETTER_STATS_TOKEN", "NEWSLETTER_ARCHIVE_TOKEN", "INDEXNOW_KEY"):
        monkeypatch.setenv(name, "")
    settings = Settings(secret_key="k" * 40)
    assert settings.brevo_list_id is None and settings.brevo_webhook_secret is None and settings.indexnow_key is None
