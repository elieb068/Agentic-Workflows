from pathlib import Path

import pytest

from raioxia.checkers.js_dependency import JsDependencyChecker, compare_text
from raioxia.models import Status
from raioxia.parsing.html import HtmlDoc

SPA = Path(__file__).parent / "fixtures" / "spa"
PARAGRAPH = " ".join(f"Pão artesanal número {i} com fermentação natural e farinha orgânica." for i in range(10))


def test_compare_text():
    result = compare_text("alpha beta gamma", "alpha beta gamma delta epsilon")
    assert result["coverage"] == 0.6
    assert result["missing_sample"] == ["delta", "epsilon"]
    assert compare_text("", "")["coverage"] == 1.0


def test_visible_text_skips_scripts_and_noscript():
    doc = HtmlDoc((SPA / "index.html").read_text())
    assert doc.visible_text() == ""
    assert HtmlDoc("<html><head><title>T</title></head><body><p>Olá <b>mundo</b></p></body></html>").visible_text() == "Olá mundo"


async def test_render_disabled_is_indeterminado(web, analyze_site):
    web.add("https://site.example/", f"<html><body><p>{PARAGRAPH}</p></body></html>")
    report = await analyze_site("https://site.example/", checkers=[JsDependencyChecker()], render=False)
    assert report.checks[0].status == Status.UNKNOWN
    assert report.checks[0].evidencia["render"] == "disabled"


async def test_render_failure_is_indeterminado(web, analyze_site, monkeypatch):
    web.add("https://site.example/", f"<html><body><p>{PARAGRAPH}</p></body></html>")
    monkeypatch.setenv("CHROMIUM_EXECUTABLE", "/nonexistent/chrome")
    report = await analyze_site("https://site.example/", checkers=[JsDependencyChecker()], render=True)
    assert report.checks[0].status == Status.UNKNOWN
    assert "render_error" in report.checks[0].evidencia


@pytest.mark.browser
@pytest.mark.usefixtures("require_browser")
class TestWithBrowser:
    async def run(self, web, analyze_site, html: str, js: str | None = None):
        web.add("https://site.example/", html)
        if js is not None:
            web.add("https://site.example/static/js/main.js", js, content_type="application/javascript")
        report = await analyze_site("https://site.example/", checkers=[JsDependencyChecker()], render=True)
        return report.checks[0]

    async def test_server_rendered_page_is_ok(self, web, analyze_site):
        result = await self.run(web, analyze_site, f"<html><body><h1>Padaria</h1><p>{PARAGRAPH}</p></body></html>")
        assert result.status == Status.OK
        assert result.evidencia["coverage"] == 1.0

    async def test_spa_is_error(self, web, analyze_site):
        result = await self.run(web, analyze_site, (SPA / "index.html").read_text(), (SPA / "main.js").read_text())
        assert result.status == Status.ERROR
        assert result.evidencia["raw_words"] == 0 and result.evidencia["rendered_words"] > 100
        assert "praticamente vazia" in result.explicacao
        assert "SSR" in result.como_corrigir

    async def test_partially_injected_content_is_warning(self, web, analyze_site):
        extra = " ".join(f"Depoimento {i}: cliente satisfeita recomenda bolos encomendas festas." for i in range(8))
        html = (
            f'<html><body><p>{PARAGRAPH}</p><div id="reviews"></div>'
            f'<script>document.getElementById("reviews").textContent = "{extra}"</script></body></html>'
        )
        result = await self.run(web, analyze_site, html)
        assert result.status == Status.WARNING
        assert 0.3 <= result.evidencia["coverage"] < 0.7
        assert "% do texto" in result.explicacao

    async def test_tiny_page_is_not_judged(self, web, analyze_site):
        result = await self.run(web, analyze_site, '<html><body><div id="r"></div><script>r.textContent="Em breve"</script></body></html>')
        assert result.status == Status.OK
