"""End to end through the whole M2 pipeline with the REAL M1 runner and checks.

Submit -> confirmation e-mail -> click -> analysis job (real checkers, simulated site, no
network) -> report stored -> report e-mail -> JSON report -> unsubscribe from the e-mail link.
Only the network (FakeWeb), Redis (fakeredis, synchronous queues) and the mail provider
(ConsoleSender) are doubles.
"""

import re

import pytest
from sqlalchemy import select

import raioxia.jobs
from raioxia.db import Analysis, EmailOutbox, Lead
from raioxia.runner import analyze as real_analyze
from tests.sites import closed_site, healthy_site

pytestmark = pytest.mark.db

BASE = "https://raiox.primesolutions.net.br"


@pytest.fixture
def real_runner(pipeline_env, make_fetcher, monkeypatch):
    async def run(url, **kwargs):
        pipeline_env.analyze_calls.append(url)
        return await real_analyze(url, fetcher=make_fetcher(), render=False)

    monkeypatch.setattr(raioxia.jobs, "analyze", run)


def link(env, path: str) -> str:
    for email in reversed(env.sender.sent):
        found = re.search(rf"{re.escape(BASE)}({path}\S+)", email.text)
        if found:
            return found.group(1)
    raise AssertionError(f"no {path} link in the e-mails sent")


def submit_and_confirm(client, env, url: str, email: str) -> dict:
    form = {"url": url, "email": email, "accepted_terms": True, "newsletter": True, "turnstile_token": "tok"}
    assert client.post("/api/analyses", json=form).status_code == 202
    response = client.get(link(env, "/confirmar/"))
    assert response.status_code == 303
    token = response.headers["location"].removeprefix("/r/")
    return client.get(f"/api/r/{token}").json()


def test_healthy_site_end_to_end(client, pipeline_env, real_runner, web, db):
    url = healthy_site(web)
    report = submit_and_confirm(client, pipeline_env, url, "dona@padaria.example")

    assert report["status"] == "done" and report["score"] == 100 and report["band"]["label"] == "Porta aberta"
    assert len(report["checks"]) == 8
    assert {c["status"] for c in report["checks"]} <= {"ok", "indeterminado"}  # js_dependency: no render here
    subjects = [e.subject for e in pipeline_env.sender.sent]
    assert subjects == ["Confirme seu e-mail para receber o Raio-X IA de padaria.example", "Raio-X IA de padaria.example: nota 100 de 100"]
    assert "Não encontramos nenhum problema importante" in pipeline_env.sender.sent[-1].text
    assert all(r.status == "sent" for r in db.scalars(select(EmailOutbox)))
    assert not any(r.host != "padaria.example" for r in web.requests)

    # The unsubscribe link in the report e-mail works end to end.
    assert client.post(link(pipeline_env, "/descadastrar/")).status_code == 200
    db.expire_all()
    assert not db.scalars(select(Lead)).one().newsletter


def test_closed_site_end_to_end(client, pipeline_env, real_runner, web, db):
    url = closed_site(web)
    report = submit_and_confirm(client, pipeline_env, url, "dono@fechada.example")

    statuses = {c["id"]: c["status"] for c in report["checks"]}
    assert report["status"] == "done" and report["score"] < 50 and report["band"]["label"] == "Fechada para a IA"
    assert statuses["robots_ai"] == "erro" and statuses["edge_block"] == "erro" and statuses["llms_txt"] == "erro"
    email = pipeline_env.sender.sent[-1]
    assert email.subject.startswith("Raio-X IA de fechada.example: nota ")
    problems = re.findall(r"^\d\. (.+) \((problema|atenção)\)$", email.text, re.M)
    assert len(problems) == 3 and all(kind == "problema" for _, kind in problems)
    analysis = db.scalars(select(Analysis)).one()
    assert analysis.results[0].evidencia  # raw evidence persisted as JSONB
