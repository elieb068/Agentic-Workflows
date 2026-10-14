import json

from raioxia.cli import main
from raioxia.net.fetcher import SafeFetcher

FULL = '<html lang="pt-BR"><head><title>T</title><meta name="description" content="d"></head></html>'


def test_analyze_prints_report_json(web, capsys):
    web.add("https://site.example/", FULL)
    code = main(
        ["analyze", "site.example", "--no-render", "--only", "basics"],
        fetcher_factory=lambda: SafeFetcher(resolver=web.resolver, transport=web.transport()),
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["url"] == "https://site.example/"
    assert report["score"] == 100
    assert report["band"] == {"id": "open", "label": "Porta aberta"}
    assert [c["id"] for c in report["checks"]] == ["basics"]
    assert set(report["checks"][0]) == {"id", "status", "titulo", "explicacao", "como_corrigir", "evidencia", "peso"}


def test_invalid_url_exits_2_with_ptbr_message(capsys):
    assert main(["analyze", "ftp://site.example"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"] == "invalid_url"
    assert "endereço" in err["message"]


def test_unknown_check_id(capsys):
    assert main(["analyze", "site.example", "--only", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_migrate_runs_alembic_upgrade(monkeypatch):
    calls = []
    monkeypatch.setattr("raioxia.db.migrate.upgrade", lambda: calls.append("head"))
    assert main(["migrate"]) == 0
    assert calls == ["head"]


def test_email_test_sends_one_message(monkeypatch, capsys):
    from raioxia.email.base import ConsoleSender, EmailError
    from raioxia.settings import Settings

    sender = ConsoleSender()
    monkeypatch.setattr("raioxia.email.jobs.get_sender", lambda: sender)
    monkeypatch.setattr("raioxia.settings.get_settings", lambda: Settings(_env_file=None, secret_key="k" * 40))
    assert main(["email-test", "eric.lieb@primesolutions.net.br"]) == 0
    [email] = sender.sent
    assert email.to == "eric.lieb@primesolutions.net.br" and "raiox@primesolutions.net.br" in email.text
    assert "&lt;raiox@primesolutions.net.br&gt;" in email.html
    assert capsys.readouterr().out.startswith("sent: <")

    class Broken:
        def send(self, email):
            raise EmailError("gmail auth refused: unauthorized_client", transient=True)

    monkeypatch.setattr("raioxia.email.jobs.get_sender", lambda: Broken())
    assert main(["email-test", "x@exemplo.com.br"]) == 1
    assert "unauthorized_client" in capsys.readouterr().err
