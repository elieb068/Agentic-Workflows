from datetime import UTC, datetime

import pytest
from jinja2 import UndefinedError
from sqlalchemy import select

from raioxia import pipeline
from raioxia.db import Analysis, CheckResultRow, EmailOutbox, Lead
from raioxia.email import jobs, outbox
from raioxia.email.base import ConsoleSender, EmailError, OutgoingEmail, build_mime
from raioxia.email.jobs import Outcome, deliver
from raioxia.email.render import render
from raioxia.settings import Settings
from raioxia.tokens import verify

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
BASE = "https://raiox.primesolutions.net.br"


@pytest.fixture
def settings():
    return Settings(_env_file=None, secret_key="k" * 40, app_base_url=BASE + "/")


CONTEXTS = {
    "confirm": {"domain": "exemplo.com.br", "confirm_url": f"{BASE}/confirmar/TOKEN", "expires_hours": 24},
    "report": {
        "domain": "exemplo.com.br", "score": 71, "band_label": "Entreaberta", "report_url": f"{BASE}/r/REPORT",
        "problems": [{"check_id": "js_dependency", "titulo": "Conteúdo só aparece com JavaScript", "status": "erro"}],
    },
    "failed": {"domain": "exemplo.com.br", "error_message": "Não encontramos esse domínio."},
    "deletion": {"delete_url": f"{BASE}/excluir-dados/DEL", "expires_hours": 24},
}


@pytest.mark.parametrize("kind", list(CONTEXTS))
def test_every_template_renders_in_ptbr(settings, kind):
    email = render(kind, CONTEXTS[kind], to="ana@exemplo.com.br", lead_id=42, settings=settings, now=NOW)
    assert email.to == "ana@exemplo.com.br" and "\n" not in email.subject
    assert f"{BASE}/privacidade" in email.html and f"{BASE}/privacidade" in email.text
    for value in CONTEXTS[kind].values():
        if isinstance(value, str) and value.startswith("http"):
            assert value in email.html and value in email.text
    if kind == "deletion":
        assert "descadastrar" not in email.html and "List-Unsubscribe" not in email.headers
    else:
        link = email.headers["List-Unsubscribe"].strip("<>")
        assert link in email.html and link in email.text
        assert verify(settings.secret_key.get_secret_value(), "unsubscribe", link.rsplit("/", 1)[1], NOW) == 42
        assert email.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_report_subject_and_body(settings):
    email = render("report", CONTEXTS["report"], to="a@b.com.br", lead_id=1, settings=settings, now=NOW)
    assert email.subject == "Raio-X IA de exemplo.com.br: nota 71 de 100"
    assert "Entreaberta" in email.text and "1. Conteúdo só aparece com JavaScript (problema)" in email.text
    no_score = render("report", CONTEXTS["report"] | {"score": None, "problems": []}, to="a@b.com.br", lead_id=1, settings=settings, now=NOW)
    assert no_score.subject == "Seu Raio-X IA de exemplo.com.br está pronto"
    assert "Parabéns" in no_score.text


def test_html_is_escaped_and_missing_context_is_an_error(settings):
    evil = CONTEXTS["failed"] | {"error_message": "<script>alert(1)</script>"}
    email = render("failed", evil, to="a@b.com.br", lead_id=1, settings=settings, now=NOW)
    assert "<script>" not in email.html and "&lt;script&gt;" in email.html
    with pytest.raises(UndefinedError):
        render("confirm", {"domain": "x.com.br"}, to="a@b.com.br", lead_id=1, settings=settings, now=NOW)


def test_mime_from_alias_and_alternatives():
    email = OutgoingEmail(to="ana@exemplo.com.br", subject="Olá ção", html="<p>oi</p>", text="oi", headers={"List-Unsubscribe": "<https://x/y>"})
    mime = build_mime(email, from_name="Raio-X IA", from_address="raiox@primesolutions.net.br")
    assert str(mime["From"]) == "Raio-X IA <raiox@primesolutions.net.br>"
    assert mime["Reply-To"] == "raiox@primesolutions.net.br" and mime["List-Unsubscribe"] == "<https://x/y>"
    assert mime["Message-ID"].endswith("@primesolutions.net.br>")
    assert [p.get_content_type() for p in mime.iter_parts()] == ["text/plain", "text/html"]


def test_top_problems_order():
    a = Analysis(results=[
        CheckResultRow(check_id="sitemap", position=6, status="aviso", titulo="s", explicacao="", peso=6),
        CheckResultRow(check_id="basics", position=7, status="erro", titulo="b", explicacao="", peso=6),
        CheckResultRow(check_id="robots_ai", position=0, status="ok", titulo="r", explicacao="", peso=25),
        CheckResultRow(check_id="llms_txt", position=2, status="aviso", titulo="l", explicacao="", peso=10),
        CheckResultRow(check_id="js_dependency", position=4, status="erro", titulo="j", explicacao="", peso=20),
        CheckResultRow(check_id="edge_block", position=1, status="indeterminado", titulo="e", explicacao="", peso=10),
    ])
    assert [p["check_id"] for p in outbox.top_problems(a)] == ["js_dependency", "basics", "llms_txt"]


# --- outbox + delivery (database) ----------------------------------------------------------------


class FlakySender(ConsoleSender):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def send(self, email):
        if self.errors:
            raise self.errors.pop(0)
        return super().send(email)


def _confirmable(db):
    sub = pipeline.submit(
        db, url="exemplo.com.br", email="ana@exemplo.com.br", accepted_terms=True, newsletter=False,
        ip="203.0.113.7", user_agent="UA", now=NOW,
    )
    return sub, outbox.queue_confirm_email(db, sub.analysis, sub.confirm_token, base_url=BASE)


@pytest.mark.db
def test_confirm_email_is_sent_and_its_secret_link_scrubbed(db, settings):
    sub, row = _confirmable(db)
    assert row.context["confirm_url"] == f"{BASE}/confirmar/{sub.confirm_token}"
    sender = ConsoleSender()
    assert deliver(db, row.id, sender=sender, settings=settings, now=NOW) is Outcome.SENT
    assert sub.confirm_token in sender.sent[0].text
    assert (row.status, row.attempts, row.sent_at) == ("sent", 1, NOW) and row.provider_message_id
    assert "confirm_url" not in row.context and row.context["domain"] == "exemplo.com.br"
    assert deliver(db, row.id, sender=sender, settings=settings, now=NOW) is Outcome.SKIPPED
    assert len(sender.sent) == 1


@pytest.mark.db
def test_transient_errors_retry_then_give_up(db, settings):
    _, row = _confirmable(db)
    sender = FlakySender([EmailError("quota", transient=True)] * jobs.MAX_ATTEMPTS)
    outcomes = [deliver(db, row.id, sender=sender, settings=settings, now=NOW) for _ in range(jobs.MAX_ATTEMPTS)]
    assert outcomes == [Outcome.RETRY] * (jobs.MAX_ATTEMPTS - 1) + [Outcome.FAILED]
    assert (row.status, row.attempts, row.last_error) == ("failed", jobs.MAX_ATTEMPTS, "quota")


@pytest.mark.db
def test_transient_then_success_and_permanent_failure(db, settings):
    _, row = _confirmable(db)
    sender = FlakySender([EmailError("timeout", transient=True)])
    assert deliver(db, row.id, sender=sender, settings=settings, now=NOW) is Outcome.RETRY
    assert deliver(db, row.id, sender=sender, settings=settings, now=NOW) is Outcome.SENT
    assert row.attempts == 2 and row.last_error is None

    other = outbox.queue_failed_email(db, db.scalars(select(Analysis)).one())
    bad = FlakySender([EmailError("invalid recipient", transient=False)])
    assert deliver(db, other.id, sender=bad, settings=settings, now=NOW) is Outcome.FAILED
    assert other.status == "failed" and other.attempts == 1


@pytest.mark.db
def test_report_email_context(db):
    lead = Lead(email="ana@exemplo.com.br")
    analysis = Analysis(
        lead=lead, url="https://exemplo.com.br/", domain="exemplo.com.br", report_token="REPORT", status="done",
        score=71, band_label="Entreaberta",
        results=[CheckResultRow(check_id="basics", position=0, status="aviso", titulo="Básicos", explicacao="", peso=6)],
    )
    db.add(analysis)
    db.flush()
    row = outbox.queue_report_email(db, analysis, base_url=BASE)
    assert row.context == {
        "domain": "exemplo.com.br", "score": 71, "band_label": "Entreaberta", "report_url": f"{BASE}/r/REPORT",
        "problems": [{"check_id": "basics", "titulo": "Básicos", "status": "aviso"}],
    }
    assert (row.kind, row.to_address, row.lead_id, row.analysis_id) == ("report", lead.email, lead.id, analysis.id)


@pytest.mark.db
def test_dispatch_runs_the_job_through_rq(db, db_sessionmaker, settings, monkeypatch):
    from contextlib import contextmanager

    import fakeredis
    from rq import Queue

    _, row = _confirmable(db)
    db.commit()
    sender = ConsoleSender()

    @contextmanager
    def scope():
        session = db_sessionmaker()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(jobs, "session_scope", scope)
    monkeypatch.setattr(jobs, "get_sender", lambda: sender)
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    queue = Queue("email", connection=fakeredis.FakeStrictRedis(), is_async=False)
    outbox.dispatch(queue, [row.id])
    assert len(sender.sent) == 1
    db.expire_all()
    assert db.get(EmailOutbox, row.id).status == "sent"


@pytest.mark.db
def test_requeue_stale_only_old_queued_rows(db):
    import fakeredis
    from rq import Queue

    _, fresh = _confirmable(db)
    fresh.created_at = NOW
    old = outbox.queue_email(db, kind="failed", to="x@exemplo.com.br", lead_id=None, analysis_id=None, context={})
    old.created_at = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    db.flush()
    queue = Queue("email", connection=fakeredis.FakeStrictRedis())
    assert outbox.requeue_stale(db, queue, now=NOW) == 1
    assert [j.args for j in queue.get_jobs()] == [(old.id,)]
