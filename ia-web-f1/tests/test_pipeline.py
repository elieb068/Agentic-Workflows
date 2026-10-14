from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from raioxia import pipeline
from raioxia.db import Analysis, Consent, Lead
from raioxia.models import AnalysisReport, Band, CheckResult, Status
from raioxia.pipeline import Confirmation, SubmissionError
from raioxia.tokens import hash_token

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def submit(db, url="exemplo.com.br", email="Ana@Exemplo.com.br", *, newsletter=False, now=NOW, terms=True):
    return pipeline.submit(
        db, url=url, email=email, accepted_terms=terms, newsletter=newsletter,
        ip="203.0.113.7", user_agent="Mozilla/5.0", now=now,
    )


def report(score=71) -> AnalysisReport:
    return AnalysisReport(
        url="https://exemplo.com.br/",
        final_url="https://www.exemplo.com.br/",
        score=score,
        band=Band(id="ajar", label="Entreaberta"),
        checks=[
            CheckResult(id="robots_ai", status=Status.OK, titulo="Robôs", explicacao="ok", peso=25, evidencia={"a": 1}),
            CheckResult(id="js_dependency", status=Status.ERROR, titulo="JS", explicacao="só com JS", como_corrigir="SSR", peso=20),
        ],
        analyzed_at=NOW,
        duration_ms=1946,
        version="0.0.1",
    )


def test_submit_records_lead_consents_and_pending_analysis(db):
    sub = submit(db, newsletter=True)
    a = sub.analysis
    assert a.status == "pending_confirmation"
    assert a.url == "https://exemplo.com.br/" and a.domain == "exemplo.com.br"
    assert a.confirm_token_hash == hash_token(sub.confirm_token) and sub.confirm_token not in a.confirm_token_hash
    assert a.confirm_expires_at == NOW + timedelta(hours=24)
    assert len(a.report_token) >= 43 and a.report_token != sub.confirm_token
    assert a.newsletter_opt_in and str(a.requested_ip) == "203.0.113.7"
    lead = db.scalars(select(Lead)).one()
    assert lead.email == "ana@exemplo.com.br" and not lead.newsletter  # not before confirmation
    kinds = sorted((c.kind, c.granted, c.text_version) for c in db.scalars(select(Consent)))
    assert kinds == [("newsletter", True, pipeline.CONSENT_TEXT_VERSION), ("report_terms", True, pipeline.CONSENT_TEXT_VERSION)]


def test_same_email_reuses_the_lead_and_newsletter_is_optional(db):
    submit(db)
    submit(db, url="outro.com.br", email="ana@exemplo.com.br")
    assert db.scalars(select(Lead)).one().email == "ana@exemplo.com.br"
    assert [c.kind for c in db.scalars(select(Consent))] == ["report_terms", "report_terms"]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (dict(terms=False), "terms_required"),
        (dict(email="not-an-email"), "invalid_email"),
        (dict(url="ftp://exemplo.com.br"), "invalid_url"),
        (dict(url="localhost"), "invalid_url"),
        (dict(url="http://192.168.0.1"), "blocked"),
        (dict(url="https://[::1]/"), "blocked"),
        (dict(url="169.254.169.254"), "blocked"),
    ],
)
def test_submit_refusals_have_ptbr_messages(db, kwargs, code):
    with pytest.raises(SubmissionError) as exc:
        submit(db, **kwargs)
    assert exc.value.code == code and exc.value.message


def test_confirm_queues_and_applies_newsletter_opt_in(db):
    sub = submit(db, newsletter=True)
    result, analysis = pipeline.confirm(db, sub.confirm_token, now=NOW + timedelta(minutes=5))
    assert result is Confirmation.QUEUED and analysis.status == "queued"
    assert analysis.confirmed_at == NOW + timedelta(minutes=5)
    lead = analysis.lead
    assert lead.newsletter and lead.confirmed_at == analysis.confirmed_at


def test_confirm_twice_expired_and_unknown(db):
    sub = submit(db)
    assert pipeline.confirm(db, sub.confirm_token, now=NOW)[0] is Confirmation.QUEUED
    assert pipeline.confirm(db, sub.confirm_token, now=NOW)[0] is Confirmation.ALREADY
    assert pipeline.confirm(db, "nope", now=NOW) == (Confirmation.INVALID, None)

    late = submit(db, url="outro.com.br")
    result, analysis = pipeline.confirm(db, late.confirm_token, now=NOW + timedelta(hours=25))
    assert result is Confirmation.EXPIRED and analysis.status == "expired"
    assert pipeline.confirm(db, late.confirm_token, now=NOW)[0] is Confirmation.EXPIRED


def test_worker_lifecycle_persists_the_report(db):
    sub = submit(db)
    pipeline.confirm(db, sub.confirm_token, now=NOW)
    analysis = pipeline.start(db, sub.analysis.id)
    assert analysis.status == "running"
    pipeline.finish(db, analysis, report(), now=NOW + timedelta(seconds=30))
    db.commit()
    db.expire_all()
    saved = db.get(Analysis, sub.analysis.id)
    assert (saved.status, saved.score, saved.band_id, saved.band_label) == ("done", 71, "ajar", "Entreaberta")
    assert saved.final_url == "https://www.exemplo.com.br/" and saved.duration_ms == 1946
    assert [(r.check_id, r.position, r.status) for r in saved.results] == [("robots_ai", 0, "ok"), ("js_dependency", 1, "erro")]
    assert saved.results[0].evidencia == {"a": 1} and saved.results[1].como_corrigir == "SSR"
    assert pipeline.start(db, sub.analysis.id) is None  # a duplicate job does nothing


def test_fail_records_the_error(db):
    sub = submit(db)
    pipeline.confirm(db, sub.confirm_token, now=NOW)
    analysis = pipeline.start(db, sub.analysis.id)
    pipeline.fail(db, analysis, "dns", "Não encontramos esse domínio.", now=NOW)
    assert (analysis.status, analysis.error_code) == ("failed", "dns") and analysis.finished_at == NOW


def _done(db, url, finished_at):
    sub = submit(db, url=url, now=finished_at - timedelta(minutes=2))
    pipeline.confirm(db, sub.confirm_token, now=finished_at - timedelta(minutes=1))
    pipeline.finish(db, pipeline.start(db, sub.analysis.id), report(), now=finished_at)
    db.flush()
    return sub.analysis


def test_confirm_reuses_a_result_from_the_last_24h(db):
    original = _done(db, "exemplo.com.br", NOW)
    sub = submit(db, url="https://exemplo.com.br/qualquer", email="bia@exemplo.com.br", now=NOW + timedelta(hours=2))
    result, analysis = pipeline.confirm(db, sub.confirm_token, now=NOW + timedelta(hours=3))
    assert result is Confirmation.REUSED
    assert analysis.status == "done" and analysis.reused_from_id == original.id
    assert analysis.report_token != original.report_token and analysis.score == 71
    assert [r.check_id for r in analysis.results] == ["robots_ai", "js_dependency"]

    # A reuse of a reuse still points at the analysis that really ran.
    third = submit(db, url="exemplo.com.br", email="caio@exemplo.com.br", now=NOW + timedelta(hours=4))
    assert pipeline.confirm(db, third.confirm_token, now=NOW + timedelta(hours=4))[1].reused_from_id == original.id


def test_no_reuse_after_24h_or_for_another_host(db):
    _done(db, "exemplo.com.br", NOW)
    old = submit(db, url="exemplo.com.br", email="d@exemplo.com.br", now=NOW + timedelta(hours=24, minutes=1))
    assert pipeline.confirm(db, old.confirm_token, now=NOW + timedelta(hours=24, minutes=1))[0] is Confirmation.QUEUED
    www = submit(db, url="www.exemplo.com.br", email="e@exemplo.com.br", now=NOW + timedelta(hours=1))
    assert pipeline.confirm(db, www.confirm_token, now=NOW + timedelta(hours=1))[0] is Confirmation.QUEUED


def test_expire_unconfirmed(db):
    fresh = submit(db, url="a.com.br", now=NOW)
    stale = submit(db, url="b.com.br", now=NOW - timedelta(hours=30))
    assert pipeline.expire_unconfirmed(db, now=NOW) == 1
    db.expire_all()
    assert db.get(Analysis, stale.analysis.id).status == "expired"
    assert db.get(Analysis, fresh.analysis.id).status == "pending_confirmation"
