import shutil

import pytest
from sqlalchemy import select

from raioxia import pipeline
from raioxia.config import DEFAULT_CONFIG_DIR
from raioxia.db import Consent

pytestmark = pytest.mark.db

FORM = {"url": "exemplo.com.br", "email": "ana@exemplo.com.br", "accepted_terms": "1", "cf-turnstile-response": "tok"}


@pytest.mark.parametrize(
    ("path", "texts"),
    [
        ("/privacidade", ["Política de privacidade", "art. 7º, V", "Oracle Cloud", "até 7 dias", "180 dias", "ANPD", "Versão de 16 de outubro de 2026", "não são personalizados"]),
        ("/termos", ["Termos de uso", "Código de Defesa do Consumidor", "não é consultoria", "Uso permitido"]),
    ],
)
def test_legal_pages(client, path, texts):
    response = client.get(path)
    html = response.text
    assert response.status_code == 200
    assert "Prime Solutions Tecnologia e Serviços de Apoio Administrativo, CNPJ 43.757.807/0001-86" in html
    assert "mailto:raiox@primesolutions.net.br" in html and "Versão de 1" in html
    assert f'<link rel="canonical" href="https://raiox.primesolutions.net.br{path}">' in html
    for text in texts:
        assert text in html


def test_consents_record_the_published_text_version(client, db):
    client.post("/pedido", data=FORM)
    assert {c.text_version for c in db.scalars(select(Consent))} == {"2026-10-16"} == {pipeline.CONSENT_TEXT_VERSION}


def test_bot_page(client, pipeline_env):
    html = client.get("/bot").text
    assert "RaioXIA-Bot/0.1 (+https://raiox.primesolutions.net.br/bot)" in html
    assert "GPTBot" in html and "lista de exclusão" in html and "endereço IP" not in html
    pipeline_env.settings.server_ip = "163.176.179.23"
    assert "163.176.179.23" in client.get("/bot").text


def test_deletion_form_flow(client, pipeline_env, turnstile):
    page = client.get("/excluir-dados")
    assert page.status_code == 200 and 'action="/excluir-dados"' in page.text and "cf-turnstile" in page.text
    assert '<meta name="robots" content="noindex, nofollow">' in page.text  # a utility form: kept out of search

    unknown = client.post("/excluir-dados", data={"email": "ninguem@exemplo.com.br", "cf-turnstile-response": "tok"})
    assert unknown.status_code == 200 and "Confira o seu e-mail" in unknown.text and pipeline_env.sender.sent == []
    client.post("/pedido", data=FORM)
    known = client.post("/excluir-dados", data={"email": "ana@exemplo.com.br", "cf-turnstile-response": "tok"})
    assert known.text.count("Se esse e-mail estiver no Raio-X IA") == unknown.text.count("Se esse e-mail estiver no Raio-X IA") == 1
    assert pipeline_env.sender.sent[-1].subject == "Confirme a exclusão dos seus dados do Raio-X IA"

    bad = client.post("/excluir-dados", data={"email": "nada", "cf-turnstile-response": "tok"})
    assert bad.status_code == 422 and 'aria-invalid="true"' in bad.text and 'value="nada"' in bad.text
    turnstile.ok = False
    robot = client.post("/excluir-dados", data={"email": "ana@exemplo.com.br"})
    assert robot.status_code == 403 and "não é um robô" in robot.text


def test_opted_out_domains_are_refused(client, pipeline_env, tmp_path, monkeypatch):
    for name in ("ai_agents.yaml", "scoring.yaml", "limits.yaml"):
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    (tmp_path / "blocked_domains.yaml").write_text("domains:\n  - domain: exemplo.com.br\n    requested: teste\n", encoding="utf-8")
    monkeypatch.setenv("RAIOXIA_CONFIG_DIR", str(tmp_path))
    pipeline._opted_out_domains.cache_clear()
    try:
        assert pipeline.is_opted_out("www.exemplo.com.br") and not pipeline.is_opted_out("outroexemplo.com.br")
        response = client.post("/pedido", data=FORM | {"url": "https://www.exemplo.com.br"})
        assert response.status_code == 422 and "pediu para ele não ser analisado" in response.text
        assert pipeline_env.sender.sent == []
    finally:
        monkeypatch.delenv("RAIOXIA_CONFIG_DIR")
        pipeline._opted_out_domains.cache_clear()


def test_committed_opt_out_list_is_valid():
    pipeline._opted_out_domains.cache_clear()
    assert isinstance(pipeline._opted_out_domains(), frozenset)
