"""End to end: CLI -> runner -> all 8 checks -> JSON, against complete simulated sites."""

import json

import pytest

from raioxia.checkers import REGISTRY
from raioxia.cli import main
from raioxia.net.fetcher import SafeFetcher
from tests.sites import closed_site, healthy_site

SPEC_FIELDS = {"id", "status", "titulo", "explicacao", "como_corrigir", "evidencia", "peso"}


def analyze(web, capsys, url: str, *flags: str) -> dict:
    code = main(
        ["analyze", url, *flags],
        fetcher_factory=lambda: SafeFetcher(resolver=web.resolver, transport=web.transport()),
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


def statuses(report: dict) -> dict[str, str]:
    return {c["id"]: c["status"] for c in report["checks"]}


def assert_contract(report: dict) -> None:
    assert [c["id"] for c in report["checks"]] == list(REGISTRY)
    assert len(report["checks"]) == 8
    for check in report["checks"]:
        assert set(check) == SPEC_FIELDS
        assert check["status"] in {"ok", "aviso", "erro", "indeterminado"}
        assert check["titulo"] and check["explicacao"]
        if check["status"] in {"aviso", "erro"}:
            assert check["como_corrigir"], check["id"]
    assert sum(c["peso"] for c in report["checks"]) == 100


def test_healthy_site_without_render(web, capsys):
    report = analyze(web, capsys, healthy_site(web), "--no-render")
    assert_contract(report)
    s = statuses(report)
    assert s.pop("js_dependency") == "indeterminado"
    assert set(s.values()) == {"ok"}
    assert report["score"] == 100
    assert report["band"] == {"id": "open", "label": "Porta aberta"}


def test_closed_site_without_render(web, capsys):
    report = analyze(web, capsys, closed_site(web), "--no-render")
    assert_contract(report)
    assert statuses(report) == {
        "robots_ai": "erro",
        "edge_block": "erro",
        "llms_txt": "erro",
        "meta_robots": "ok",
        "js_dependency": "indeterminado",
        "structured_data": "aviso",
        "sitemap": "aviso",
        "basics": "aviso",
    }
    # (15 + 8*0.5 + 6*0.5 + 6*0.5) / (100 - 20) = 25 / 80
    assert report["score"] == 31
    assert report["band"]["label"] == "Fechada para a IA"
    edge = next(c for c in report["checks"] if c["id"] == "edge_block")
    assert "indicativo" in edge["explicacao"] and "dash.cloudflare.com" in edge["como_corrigir"]


def test_no_request_leaves_the_simulated_sites(web, capsys):
    healthy_site(web)
    analyze(web, capsys, "padaria.example", "--no-render")
    assert {r.host for r in web.requests} == {"padaria.example"}
    paths = {r.path for r in web.requests}
    assert {"/", "/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/cardapio"} <= paths


@pytest.mark.browser
@pytest.mark.usefixtures("require_browser")
class TestWithBrowser:
    def test_healthy_site_scores_100(self, web, capsys):
        report = analyze(web, capsys, healthy_site(web))
        assert_contract(report)
        assert set(statuses(report).values()) == {"ok"}
        assert report["score"] == 100

    def test_closed_site_full_report(self, web, capsys):
        report = analyze(web, capsys, closed_site(web))
        assert_contract(report)
        assert statuses(report)["js_dependency"] == "erro"
        # 25 points out of 100: only meta_robots (15) plus half of three warnings (4 + 3 + 3)
        assert report["score"] == 25
        assert report["band"]["id"] == "closed"
