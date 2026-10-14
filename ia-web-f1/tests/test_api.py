import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from raioxia.api.app import create_app
from raioxia.api.deps import AppDeps
from raioxia.db import Analysis, Consent, EmailOutbox, Lead

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
FORM = {"url": "exemplo.com.br", "email": "Ana@Exemplo.com.br", "accepted_terms": True, "newsletter": True}


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(pipeline_env, db_sessionmaker, clock):
    app = create_app(AppDeps(
        settings=pipeline_env.settings, sessions=db_sessionmaker, redis=pipeline_env.redis,
        queues=pipeline_env.queues, clock=clock,
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
