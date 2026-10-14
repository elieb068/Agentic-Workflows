import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from raioxia.api.app import create_app
from raioxia.api.deps import AppDeps
from raioxia.api.ratelimit import RateLimiter, load_limits
from raioxia.db import Analysis, Consent, EmailOutbox, Lead

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
FORM = {"url": "exemplo.com.br", "email": "Ana@Exemplo.com.br", "accepted_terms": True, "newsletter": True, "turnstile_token": "tok"}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


class FakeTurnstile:
    def __init__(self):
        self.ok = True
        self.calls = []

    def __call__(self, token, ip):
        self.calls.append((token, ip))
        return self.ok


@pytest.fixture
def turnstile():
    return FakeTurnstile()


@pytest.fixture
def client(pipeline_env, db_sessionmaker, clock, turnstile):
    app = create_app(AppDeps(
        settings=pipeline_env.settings, sessions=db_sessionmaker, redis=pipeline_env.redis,
        queues=pipeline_env.queues, clock=clock, turnstile=turnstile,
        limiter=RateLimiter(pipeline_env.redis, load_limits()),
    ))
    with TestClient(app, client=("203.0.113.9", 50000), follow_redirects=False) as c:
        yield c


def confirm_link(env) -> str:
    [email] = [e for e in env.sender.sent if e.subject.startswith("Confirme")]
    return re.search(r"https://raiox\.primesolutions\.net\.br(/confirmar/\S+)", email.text).group(1)


def test_submit_sends_confirmation_and_records_consents(client, pipeline_env, db):
    response = client.post("/api/analyses", json=FORM, headers={"User-Agent": "Mozilla/5.0 teste"})
    assert response.status_code == 202
    assert response.json()["status"] == "confirm_email"
    [email] = pipeline_env.sender.sent
    assert email.to == "ana@exemplo.com.br" and "exemplo.com.br" in email.subject
    analysis = db.scalars(select(Analysis)).one()
    assert analysis.status == "pending_confirmation" and str(analysis.requested_ip) == "203.0.113.9"
    consents = db.scalars(select(Consent)).all()
    assert {(c.kind, str(c.ip), c.user_agent) for c in consents} == {
        ("report_terms", "203.0.113.9", "Mozilla/5.0 teste"), ("newsletter", "203.0.113.9", "Mozilla/5.0 teste"),
    }
    assert pipeline_env.analyze_calls == []  # nothing runs before confirmation


def test_confirm_runs_analysis_and_redirects_to_report(client, pipeline_env, db):
    client.post("/api/analyses", json=FORM)
    response = client.get(confirm_link(pipeline_env))
    assert response.status_code == 303
    analysis = db.scalars(select(Analysis)).one()
    assert response.headers["location"] == f"/r/{analysis.report_token}"
    assert pipeline_env.analyze_calls == ["https://exemplo.com.br/"]
    assert [e.subject for e in pipeline_env.sender.sent][-1] == "Raio-X IA de exemplo.com.br: nota 71 de 100"
    assert db.scalars(select(Lead)).one().newsletter  # double opt-in completed

    report = client.get(f"/api/r/{analysis.report_token}").json()
    assert report["status"] == "done" and report["score"] == 71 and report["band"] == {"id": "ajar", "label": "Entreaberta"}
    assert [c["id"] for c in report["checks"]] == ["robots_ai", "js_dependency", "llms_txt"]
    assert set(report["checks"][0]) == {"id", "status", "titulo", "explicacao", "como_corrigir", "evidencia", "peso"}
    page = client.get(f"/r/{analysis.report_token}")
    assert page.status_code == 200 and "71" in page.text and "Conteúdo só aparece com JavaScript" in page.text
    assert page.headers["x-robots-tag"] == "noindex, nofollow" and page.headers["referrer-policy"] == "no-referrer"

    again = client.get(confirm_link(pipeline_env))  # second click: just the report, no new run
    assert again.status_code == 303 and pipeline_env.analyze_calls == ["https://exemplo.com.br/"]


def test_second_request_within_24h_reuses_the_result(client, pipeline_env, db, clock):
    client.post("/api/analyses", json=FORM)
    client.get(confirm_link(pipeline_env))
    pipeline_env.sender.sent.clear()
    clock.now = NOW + timedelta(hours=2)
    client.post("/api/analyses", json=FORM | {"email": "bia@exemplo.com.br", "url": "https://exemplo.com.br/contato"})
    response = client.get(confirm_link(pipeline_env))
    assert response.status_code == 303
    assert pipeline_env.analyze_calls == ["https://exemplo.com.br/"]  # not analyzed again
    reused = db.scalars(select(Analysis).where(Analysis.reused_from_id.is_not(None))).one()
    assert reused.status == "done" and reused.score == 71
    assert pipeline_env.sender.sent[-1].to == "bia@exemplo.com.br"


def test_pending_report_and_failure(client, pipeline_env, db):
    client.post("/api/analyses", json=FORM)
    analysis = db.scalars(select(Analysis)).one()
    assert client.get(f"/api/r/{analysis.report_token}").json()["status"] == "pending_confirmation"

    from raioxia.runner import AnalysisError

    pipeline_env.analyze_result = AnalysisError("dns", "Não encontramos esse domínio.")
    client.get(confirm_link(pipeline_env))
    report = client.get(f"/api/r/{analysis.report_token}").json()
    assert report["status"] == "failed" and report["error"] == {"code": "dns", "message": "Não encontramos esse domínio."}
    assert report["checks"] == [] and report["score"] is None


def test_invalid_and_expired_links(client, pipeline_env, clock):
    assert client.get("/confirmar/nope").status_code == 404
    assert client.get("/api/r/nope").status_code == 404
    assert client.get("/r/nope").status_code == 404
    client.post("/api/analyses", json=FORM)
    clock.now = NOW + timedelta(hours=25)
    response = client.get(confirm_link(pipeline_env))
    assert response.status_code == 410 and "expirado" in response.text.lower()
    assert pipeline_env.analyze_calls == []


@pytest.mark.parametrize(
    ("form", "code"),
    [
        (FORM | {"accepted_terms": False}, "terms_required"),
        (FORM | {"email": "nada"}, "invalid_email"),
        (FORM | {"url": "http://192.168.0.1"}, "blocked"),
        ({"url": "exemplo.com.br"}, "invalid_request"),
        (FORM | {"url": "x" * 3000}, "invalid_request"),
    ],
)
def test_submit_refusals(client, pipeline_env, form, code):
    response = client.post("/api/analyses", json=form)
    assert response.status_code == 422
    assert response.json()["error"] == code and response.json()["message"]
    assert pipeline_env.sender.sent == []


def test_healthz(client, pipeline_env):
    response = client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"ok": True, "database": "ok", "redis": "ok"}
    class DownRedis:
        def ping(self):
            raise ConnectionError("down")

    client.app.state.deps.redis = DownRedis()
    response = client.get("/healthz")
    assert response.status_code == 503 and response.json() == {"ok": False, "database": "ok", "redis": "ConnectionError"}


def test_turnstile_failure_is_refused_before_anything_else(client, pipeline_env, turnstile, db):
    turnstile.ok = False
    response = client.post("/api/analyses", json=FORM)
    assert response.status_code == 403 and response.json()["error"] == "turnstile_failed"
    assert turnstile.calls == [("tok", "203.0.113.9")]
    assert db.scalars(select(Analysis)).all() == [] and pipeline_env.sender.sent == []


def test_ip_limit_three_per_hour(client, pipeline_env, clock):
    for i in range(3):
        assert client.post("/api/analyses", json=FORM | {"email": f"p{i}@exemplo.com.br", "url": f"s{i}.com.br"}).status_code == 202
    blocked = client.post("/api/analyses", json=FORM | {"email": "p9@exemplo.com.br", "url": "s9.com.br"})
    assert blocked.status_code == 429 and blocked.json()["error"] == "rate_limited"
    clock.now = NOW + timedelta(hours=1)  # next hourly window (daily allows 10)
    assert client.post("/api/analyses", json=FORM | {"email": "p8@exemplo.com.br", "url": "s8.com.br"}).status_code == 202
    assert len(pipeline_env.sender.sent) == 4


def test_email_limit_is_silent(client, pipeline_env, db, clock):
    for i in range(2):
        assert client.post("/api/analyses", json=FORM | {"url": f"s{i}.com.br"}).status_code == 202
    clock.now = NOW + timedelta(hours=2)  # new hourly IP window, same day
    third = client.post("/api/analyses", json=FORM | {"url": "s3.com.br", "email": "ANA@exemplo.com.br"})
    assert third.status_code == 202 and third.json() == client.post("/api/analyses", json=FORM | {"email": "x@exemplo.com.br"}).json()
    assert [e.to for e in pipeline_env.sender.sent].count("ana@exemplo.com.br") == 2
    assert db.scalars(select(Analysis).where(Analysis.domain == "s3.com.br")).all() == []


def test_domain_limit(client, pipeline_env):
    from raioxia.api.ratelimit import Limits, Window

    limits = load_limits()
    generous_ip = Limits(ip=[Window(window=3600, max=100)], email=limits.email, domain=limits.domain)
    client.app.state.deps.limiter = RateLimiter(pipeline_env.redis, generous_ip)
    for i in range(10):
        assert client.post("/api/analyses", json=FORM | {"email": f"d{i}@exemplo.com.br"}).status_code == 202, i
    response = client.post("/api/analyses", json=FORM | {"email": "d10@exemplo.com.br"})
    assert response.status_code == 429 and "amanhã" in response.json()["message"]
    assert len(pipeline_env.sender.sent) == 10


def _link(env, path_prefix: str) -> str:
    for email in reversed(env.sender.sent):
        found = re.search(rf"https://raiox\.primesolutions\.net\.br({path_prefix}\S+)", email.text)
        if found:
            return found.group(1)
    raise AssertionError(f"no {path_prefix} link sent")


def test_unsubscribe_needs_the_button_and_supports_one_click(client, pipeline_env, db):
    client.post("/api/analyses", json=FORM)
    client.get(confirm_link(pipeline_env))
    link = _link(pipeline_env, "/descadastrar/")
    page = client.get(link)
    assert page.status_code == 200 and "<form method=\"post\"" in page.text
    assert db.scalars(select(Lead)).one().newsletter  # a GET (mail scanner) changes nothing
    done = client.post(link, data={"List-Unsubscribe": "One-Click"})  # what Gmail sends
    assert done.status_code == 200
    db.expire_all()
    assert not db.scalars(select(Lead)).one().newsletter
    assert client.get("/descadastrar/1.0.forged").status_code == 404
    assert client.post("/descadastrar/1.0.forged").status_code == 404


def test_data_deletion_flow(client, pipeline_env, db):
    client.post("/api/analyses", json=FORM)
    client.get(confirm_link(pipeline_env))
    response = client.post("/api/excluir-dados", json={"email": "ANA@exemplo.com.br", "turnstile_token": "tok"})
    assert response.status_code == 202
    assert pipeline_env.sender.sent[-1].subject == "Confirme a exclusão dos seus dados do Raio-X IA"
    link = _link(pipeline_env, "/excluir-dados/")
    assert client.get(link).status_code == 200 and db.scalars(select(Lead)).all()  # page only
    assert client.post(link).status_code == 200
    db.expire_all()
    assert db.scalars(select(Lead)).all() == [] and db.scalars(select(Analysis)).all() == []
    assert db.scalars(select(Consent)).all() == [] and db.scalars(select(EmailOutbox)).all() == []
    assert client.post(link).status_code == 200  # idempotent


def test_deletion_request_does_not_reveal_unknown_addresses(client, pipeline_env, clock):
    unknown = client.post("/api/excluir-dados", json={"email": "ninguem@exemplo.com.br", "turnstile_token": "tok"})
    assert unknown.status_code == 202 and pipeline_env.sender.sent == []
    client.post("/api/analyses", json=FORM)
    known = client.post("/api/excluir-dados", json={"email": "ana@exemplo.com.br", "turnstile_token": "tok"})
    assert known.json() == unknown.json()
    assert client.post("/api/excluir-dados", json={"email": "nada", "turnstile_token": "tok"}).status_code == 422


def test_expired_deletion_link(client, pipeline_env, clock):
    client.post("/api/analyses", json=FORM)
    client.post("/api/excluir-dados", json={"email": "ana@exemplo.com.br", "turnstile_token": "tok"})
    link = _link(pipeline_env, "/excluir-dados/")
    clock.now = NOW + timedelta(hours=25)
    assert client.get(link).status_code == 404 and client.post(link).status_code == 404
