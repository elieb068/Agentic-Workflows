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
