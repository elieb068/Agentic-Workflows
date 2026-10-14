import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from raioxia.db import Analysis, Base, CheckResultRow, Consent, EmailOutbox, Lead
from raioxia.db.migrate import downgrade, upgrade

pytestmark = pytest.mark.db

TABLES = {"leads", "consents", "analyses", "check_results", "email_outbox"}


def test_migrations_match_models(db_engine):
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_roundtrip(db_url, db_engine):
    downgrade(db_url)
    assert TABLES.isdisjoint(inspect(db_engine).get_table_names())
    upgrade(db_url)
    assert TABLES <= set(inspect(db_engine).get_table_names())


def _analysis(lead: Lead | None, report_token: str, **values) -> Analysis:
    return Analysis(lead=lead, url="https://exemplo.com.br/", domain="exemplo.com.br", report_token=report_token, **values)


def test_deleting_a_lead_keeps_anonymized_analyses(db):
    lead = Lead(email="ana@exemplo.com.br")
    analysis = _analysis(lead, requested_ip="203.0.113.7", report_token="tok-1")
    analysis.results.append(CheckResultRow(check_id="basics", position=0, status="ok", titulo="t", explicacao="e", peso=6))
    db.add_all([lead, analysis, Consent(lead=lead, kind="report_terms", granted=True, text_version="2026-10", ip="203.0.113.7")])
    db.flush()
    db.add(EmailOutbox(lead_id=lead.id, analysis_id=analysis.id, kind="report", to_address=lead.email))
    db.commit()

    db.delete(lead)
    db.commit()
    db.expire_all()
    assert db.scalars(select(Consent)).all() == []
    assert db.scalars(select(EmailOutbox)).all() == []
    kept = db.scalars(select(Analysis)).one()
    assert kept.lead_id is None and len(kept.results) == 1


def test_deleting_an_analysis_cascades_to_results_and_outbox(db):
    lead = Lead(email="bia@exemplo.com.br")
    analysis = _analysis(lead, report_token="tok-2")
    analysis.results.append(CheckResultRow(check_id="sitemap", position=0, status="aviso", titulo="t", explicacao="e", peso=6))
    db.add(analysis)
    db.flush()
    db.add(EmailOutbox(lead_id=lead.id, analysis_id=analysis.id, kind="confirm", to_address=lead.email))
    db.commit()

    db.delete(analysis)
    db.commit()
    assert db.scalars(select(CheckResultRow)).all() == []
    assert db.scalars(select(EmailOutbox)).all() == []
    assert db.scalars(select(Lead)).one().email == "bia@exemplo.com.br"


@pytest.mark.parametrize(
    "row",
    [
        lambda: _analysis(None, status="bogus", report_token="tok-3"),
        lambda: _analysis(None, score=101, report_token="tok-4"),
        lambda: EmailOutbox(kind="spam", to_address="x@exemplo.com.br"),
    ],
)
def test_check_constraints(db, row):
    db.add(row())
    with pytest.raises(IntegrityError):
        db.flush()


def test_email_and_tokens_are_unique(db):
    db.add_all([Lead(email="c@exemplo.com.br"), Lead(email="c@exemplo.com.br")])
    with pytest.raises(IntegrityError):
        db.flush()
