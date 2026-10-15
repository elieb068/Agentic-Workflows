from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from raioxia import pipeline, privacy, stats
from raioxia.db import Analysis, Consent, EmailOutbox, Lead
from tests.conftest import sample_report

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def submit(db, email, url="exemplo.com.br", *, at=NOW, newsletter=False):
    sub = pipeline.submit(db, url=url, email=email, accepted_terms=True, newsletter=newsletter, ip="203.0.113.7", user_agent="UA", now=at)
    sub.analysis.created_at = at
    sub.analysis.lead.created_at = min(sub.analysis.lead.created_at or at, at)
    db.flush()
    return sub


def done(db, email, url="exemplo.com.br", *, at=NOW, newsletter=False):
    sub = submit(db, email, url, at=at, newsletter=newsletter)
    pipeline.confirm(db, sub.confirm_token, now=at)
    if sub.analysis.status == "queued":
        pipeline.finish(db, pipeline.start(db, sub.analysis.id), sample_report(), now=at)
    db.flush()
    return sub.analysis


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_unsubscribe_is_logged_and_idempotent(db):
    lead = done(db, "ana@exemplo.com.br", newsletter=True).lead
    assert lead.newsletter
    assert privacy.unsubscribe(db, lead.id, ip="198.51.100.1", user_agent="Gmail", now=NOW)
    assert privacy.unsubscribe(db, lead.id, ip="198.51.100.1", user_agent="Gmail", now=NOW + timedelta(hours=1))
    assert not lead.newsletter and lead.unsubscribed_at == NOW
    withdrawals = db.scalars(select(Consent).where(Consent.kind == "newsletter", Consent.granted.is_(False))).all()
    assert len(withdrawals) == 1 and str(withdrawals[0].ip) == "198.51.100.1"
    assert not privacy.unsubscribe(db, 999_999, ip=None, user_agent=None, now=NOW)


def test_delete_lead_data_erases_the_person_only(db):
    ana = done(db, "ana@exemplo.com.br")
    bia_reuse = done(db, "bia@exemplo.com.br", at=NOW + timedelta(hours=1))  # reuses Ana's analysis
    assert bia_reuse.reused_from_id == ana.id
    db.add(EmailOutbox(lead_id=ana.lead_id, analysis_id=ana.id, kind="report", to_address="ana@exemplo.com.br"))
    db.flush()

    assert privacy.delete_lead_data(db, ana.lead_id)
    db.expire_all()
    assert [lead.email for lead in db.scalars(select(Lead))] == ["bia@exemplo.com.br"]
    assert db.scalars(select(Consent.lead_id)).all() == [bia_reuse.lead_id]
    assert db.get(Analysis, ana.id) is None and count(db, EmailOutbox) == 0
    bia = db.get(Analysis, bia_reuse.id)
    assert bia.reused_from_id is None and len(bia.results) == 3  # Bia's copy survives
    assert not privacy.delete_lead_data(db, ana.lead_id)


def test_purge_unconfirmed_after_7_days(db):
    submit(db, "velho@exemplo.com.br", "a.com.br", at=NOW - timedelta(days=8))
    submit(db, "novo@exemplo.com.br", "b.com.br", at=NOW - timedelta(days=2))
    kept = done(db, "ok@exemplo.com.br", "c.com.br", at=NOW - timedelta(days=8))
    submit(db, "ok@exemplo.com.br", "d.com.br", at=NOW - timedelta(days=8))  # confirmed lead's stale request

    counts = privacy.purge(db, now=NOW)
    assert counts["expired"] == 3  # every pending request past its 24h link, before the 7-day cleanup
    assert counts["unconfirmed_analyses_deleted"] == 2 and counts["unconfirmed_leads_deleted"] == 1
    assert sorted(lead.email for lead in db.scalars(select(Lead))) == ["novo@exemplo.com.br", "ok@exemplo.com.br"]
    assert [a.domain for a in db.scalars(select(Analysis).order_by(Analysis.domain))] == ["b.com.br", "c.com.br"]
    assert db.get(Analysis, kept.id).status == "done"


def test_purge_anonymizes_reports_after_180_days(db):
    old = done(db, "antigo@exemplo.com.br", "a.com.br", at=NOW - timedelta(days=181))
    sub_old = done(db, "assinante@exemplo.com.br", "b.com.br", at=NOW - timedelta(days=181), newsletter=True)
    recent = done(db, "recente@exemplo.com.br", "c.com.br", at=NOW - timedelta(days=10))
    token = old.report_token
    db.add(EmailOutbox(lead_id=None, analysis_id=old.id, kind="report", to_address="antigo@exemplo.com.br", status="sent",
                       created_at=NOW - timedelta(days=181)))
    db.flush()

    counts = privacy.purge(db, now=NOW)
    assert counts["reports_anonymized"] == 2 and counts["inactive_leads_deleted"] == 1 and counts["outbox_rows_deleted"] == 1
    db.expire_all()
    anonymized = db.get(Analysis, old.id)
    assert anonymized.lead_id is None and anonymized.requested_ip is None and anonymized.report_token != token
    assert len(anonymized.results) == 3 and anonymized.score == 71  # stats survive
    emails = sorted(lead.email for lead in db.scalars(select(Lead)))
    assert emails == ["assinante@exemplo.com.br", "recente@exemplo.com.br"]  # subscriber kept, inactive gone
    assert db.get(Analysis, recent.id).lead_id is not None and db.get(Analysis, sub_old.id).lead_id is None


def test_daily_stats(db):
    done(db, "a@exemplo.com.br", "a.com.br", at=NOW - timedelta(days=1), newsletter=True)
    submit(db, "b@exemplo.com.br", "b.com.br", at=NOW - timedelta(days=1))
    done(db, "c@exemplo.com.br", "c.com.br", at=NOW)
    days = stats.daily(db, now=NOW, days=30)
    assert [(d.day.isoformat(), d.requested, d.confirmed, d.done, d.newsletter_opt_in) for d in days] == [
        ("2026-10-13", 2, 1, 1, 1), ("2026-10-14", 1, 1, 1, 0),
    ]
    assert days[0].confirmation_rate == 0.5
    table = stats.format_table(days)
    assert "2026-10-13" in table and table.splitlines()[-1].split()[:3] == ["total", "3", "2"]
