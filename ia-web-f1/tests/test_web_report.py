import re
from datetime import timedelta
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from raioxia import pipeline
from raioxia.db import Analysis, Consent, Lead
from raioxia.models import AnalysisReport, Band, CheckResult, Status
from raioxia.runner import AnalysisError
from tests.conftest import sample_report

pytestmark = pytest.mark.db

FORM = {"url": "exemplo.com.br", "email": "ana@exemplo.com.br", "accepted_terms": "1", "cf-turnstile-response": "tok"}


def confirm_link(env) -> str:
    email = next(e for e in reversed(env.sender.sent) if e.subject.startswith("Confirme"))
    return re.search(r"https://raiox\.primesolutions\.net\.br(/confirmar/\S+)", email.text).group(1)


def run(client, env, form=FORM) -> str:
    client.post("/pedido", data=form)
    return client.get(confirm_link(env)).headers["location"]


def test_done_report_layout(client, pipeline_env):
    page = client.get(run(client, pipeline_env))
    html = page.text
    assert page.status_code == 200 and '<meta name="robots" content="noindex, nofollow">' in html
    assert 'aria-label="Nota 71 de 100"' in html and "Entreaberta" in html and "exemplo.com.br" in html
    assert "1 problema · 1 ponto de atenção · 1 ok" in html
    # problems first, then warnings, then ok
    order = [html.index(t) for t in ("Conteúdo só aparece com JavaScript", "llms.txt incompleto", "Robôs de IA liberados")]
    assert order == sorted(order)
    assert html.count('data-ad-slot="') == 2
    assert 'data-poll-report' not in html


def test_consulting_mailto_is_prefilled(client, pipeline_env):
    location = run(client, pipeline_env)
    html = client.get(location).text
    mailto = unquote(re.search(r'href="(mailto:[^"]+)"', html).group(1).replace("&amp;", "&"))
    assert mailto.startswith("mailto:raiox@primesolutions.net.br?subject=Quero ajuda com o Raio-X IA de exemplo.com.br")
    assert "(nota 71 de 100)" in mailto and f"https://raiox.primesolutions.net.br{location}" in mailto


def test_newsletter_one_click_from_the_report(client, pipeline_env, db):
    location = run(client, pipeline_env)  # FORM has no newsletter opt-in
    token = location.removeprefix("/r/")
    assert 'action="/r/' + token + '/newsletter"' in client.get(location).text
    response = client.post(f"/r/{token}/newsletter", headers={"User-Agent": "Safari"})
    assert response.status_code == 303 and response.headers["location"] == f"{location}?newsletter=ok"
    db.expire_all()
    assert db.scalars(select(Lead)).one().newsletter
    consent = db.scalars(select(Consent).where(Consent.kind == "newsletter")).one()
    assert consent.granted and consent.user_agent == "Safari" and str(consent.ip) == "203.0.113.9"
    after = client.get(f"{location}?newsletter=ok").text
    assert "Pronto! Você vai receber" in after and "Você já recebe a newsletter" in after
    client.post(f"/r/{token}/newsletter")  # idempotent: no second consent row
    assert len(db.scalars(select(Consent).where(Consent.kind == "newsletter")).all()) == 1
    assert client.post("/r/nope/newsletter").status_code == 404


def test_pending_and_processing_states_poll(client, pipeline_env, db):
    client.post("/pedido", data=FORM)
    analysis = db.scalars(select(Analysis)).one()
    pending = client.get(f"/r/{analysis.report_token}").text
    assert "Falta confirmar o e-mail" in pending and 'data-status="pending_confirmation"' in pending
    assert f'data-poll-report="/api/r/{analysis.report_token}"' in pending and '<meta http-equiv="refresh" content="10">' in pending
    analysis.status = "running"
    db.commit()
    processing = client.get(f"/r/{analysis.report_token}").text
    assert "Analisando" in processing and "Verificando 8 pontos do site" in processing
    assert processing.count('<span aria-hidden="true" class="h-1.5 w-1.5') == 8


def test_failed_and_expired_states(client, pipeline_env, db, clock):
    pipeline_env.analyze_result = AnalysisError("dns", "Não encontramos esse domínio. Confira se o endereço está certo.")
    failed = client.get(run(client, pipeline_env)).text
    assert "Não conseguimos analisar" in failed and "Não encontramos esse domínio" in failed and "Fazer um novo pedido" in failed

    client.post("/pedido", data=FORM | {"url": "outro.com.br"})
    pending = db.scalars(select(Analysis).where(Analysis.domain == "outro.com.br")).one()
    pipeline.expire_unconfirmed(db, now=clock.now + timedelta(days=2))
    db.commit()
    assert "Este pedido expirou" in client.get(f"/r/{pending.report_token}").text


def test_reused_result_shows_the_original_analysis_time(client, pipeline_env, clock):
    run(client, pipeline_env)
    clock.now += timedelta(hours=3)
    location = run(client, pipeline_env, FORM | {"email": "bia@exemplo.com.br"})
    assert "Resultado da análise feita em" in client.get(location).text


def test_undetermined_checks_and_evidence(client, pipeline_env):
    pipeline_env.analyze_result = AnalysisReport(
        url="https://exemplo.com.br/", final_url="https://exemplo.com.br/", score=100, band=Band(id="open", label="Porta aberta"),
        checks=[
            CheckResult(id="js_dependency", status=Status.UNKNOWN, titulo="Conteúdo sem depender de JavaScript", explicacao="x", peso=20),
            CheckResult(id="robots_ai", status=Status.OK, titulo="Robôs", explicacao="ok", peso=25, evidencia={"agents": {"GPTBot": "permitido"}}),
        ],
        analyzed_at=sample_report().analyzed_at, duration_ms=10, version="0.0.1",
    )
    html = client.get(run(client, pipeline_env)).text
    assert "Não verificado" in html and "1 não verificado" in html and "Porta aberta" in html
    assert "Ver dados técnicos" in html and "&#34;GPTBot&#34;: &#34;permitido&#34;" in html
    assert html.index("Robôs") < html.index("Não verificado")  # undetermined last
