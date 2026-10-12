import httpx

from raioxia.checkers.basics import BasicsChecker
from raioxia.models import Status

FULL = (
    '<html lang="pt-BR"><head><title>Padaria Pão Quente</title>'
    '<meta name="description" content="Pães artesanais em Campinas"></head><body>oi</body></html>'
)


async def basics(analyze_site, url="site.example"):
    report = await analyze_site(url, checkers=[BasicsChecker()])
    return report.checks[0]


async def test_all_good(web, analyze_site):
    web.add("https://site.example/", FULL)
    result = await basics(analyze_site)
    assert result.status == Status.OK
    assert result.evidencia["title"] == "Padaria Pão Quente"
    assert result.evidencia["lang"] == "pt-BR"
    assert result.como_corrigir == ""


async def test_missing_title_description_lang_are_warnings(web, analyze_site):
    web.add("https://site.example/", "<html><head></head><body>oi</body></html>")
    result = await basics(analyze_site)
    assert result.status == Status.WARNING
    assert result.evidencia["items"] == {
        "https": "ok", "response_time": "ok", "title": "aviso", "description": "aviso", "lang": "aviso",
    }
    assert result.explicacao.count("•") == 3
    assert result.como_corrigir.count("•") == 3


async def test_http_only_site_is_error(web, analyze_site):
    web.add("http://site.example/", FULL)
    result = await basics(analyze_site, "http://site.example/")
    assert result.status == Status.ERROR
    assert result.evidencia["items"]["https"] == "erro"
    assert result.evidencia["https_available"] is False
    assert "HTTPS" in result.explicacao


async def test_https_available_but_not_forced_is_warning(web, analyze_site):
    web.add("http://site.example/", FULL)
    web.add("https://site.example/", FULL)
    result = await basics(analyze_site, "http://site.example/")
    assert result.status == Status.WARNING
    assert result.evidencia["items"]["https"] == "aviso"


async def test_http_redirecting_to_https_is_ok(web, analyze_site):
    web.redirect("http://site.example/", "https://site.example/")
    web.add("https://site.example/", FULL)
    result = await basics(analyze_site, "http://site.example/")
    assert result.status == Status.OK


async def test_slow_home_is_warning(web, analyze_site, monkeypatch):
    import raioxia.checkers.basics as mod

    monkeypatch.setattr(mod, "SLOW_MS", -1)
    web.add("https://site.example/", FULL)
    result = await basics(analyze_site)
    assert result.status == Status.WARNING
    assert result.evidencia["items"]["response_time"] == "aviso"
    assert "segundos" in result.explicacao


async def test_error_status_on_home(web, analyze_site):
    web.add("https://site.example/", "<title>Erro</title>", status=503)
    result = await basics(analyze_site)
    assert result.status == Status.ERROR
    assert "503" in result.explicacao
    assert "title" not in result.evidencia["items"]


async def test_unreachable_home(web, analyze_site):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    web.route("https://site.example/", refuse)
    result = await basics(analyze_site, "https://site.example/")
    assert result.status == Status.ERROR
    assert result.evidencia["fetch_error"] == "connect"
