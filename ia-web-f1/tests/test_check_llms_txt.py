from pathlib import Path

import httpx

from raioxia.checkers.llms_txt import LlmsTxtChecker
from raioxia.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "llms"
HOME = "<!DOCTYPE html><html><head><title>Padaria</title></head><body>Home</body></html>"
MD = "text/markdown; charset=utf-8"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def add_pages(web):
    for path in ["/cardapio", "/encomendas", "/historia"]:
        web.add(f"https://site.example{path}", "<html>ok</html>")


async def run(web, analyze_site, llms: str | None = None, *, content_type=MD, pages=True, **extra):
    web.add("https://site.example/", HOME)
    if llms is not None:
        web.add("https://site.example/llms.txt", llms, content_type=content_type)
    if pages:
        add_pages(web)
    report = await analyze_site("https://site.example/", checkers=[LlmsTxtChecker()])
    return report.checks[0]


async def test_valid_llms_txt(web, analyze_site):
    result = await run(web, analyze_site, fixture("valid.txt"))
    assert result.status == Status.OK
    assert "3 links em 2 seção(ões)" in result.explicacao
    assert [link["ok"] for link in result.evidencia["links_checked"]] == [True, True, True]
    assert result.evidencia["links_checked"][1]["url"] == "https://site.example/encomendas"


async def test_valid_with_llms_full(web, analyze_site):
    web.add("https://site.example/llms-full.txt", "# Padaria\n\nTudo sobre nós.", content_type="text/plain")
    result = await run(web, analyze_site, fixture("valid.txt"))
    assert result.status == Status.OK
    assert "llms-full.txt" in result.explicacao


async def test_missing_is_warning_with_template(web, analyze_site):
    result = await run(web, analyze_site)
    assert result.status == Status.WARNING
    assert "não tem o arquivo llms.txt" in result.explicacao
    assert "# Nome da sua empresa" in result.como_corrigir
    assert "https://site.example/servicos" in result.como_corrigir


async def test_html_soft_404_is_error(web, analyze_site):
    result = await run(web, analyze_site, fixture("soft404.html"), content_type="text/html")
    assert result.status == Status.ERROR
    assert result.evidencia["llms_txt"]["problem"] == "html"
    assert "404" in result.como_corrigir


async def test_redirect_to_home_is_error(web, analyze_site):
    web.redirect("https://site.example/llms.txt", "/")
    result = await run(web, analyze_site)
    assert result.status == Status.ERROR
    assert result.evidencia["llms_txt"]["problem"] == "redirect_home"


async def test_redirect_to_www_llms_txt_is_fine(web, analyze_site):
    web.add("https://site.example/", HOME)
    web.redirect("https://site.example/llms.txt", "https://www.site.example/llms.txt")
    web.add("https://www.site.example/llms.txt", fixture("valid.txt"), content_type=MD)
    add_pages(web)
    web.add("https://www.site.example/encomendas", "<html>ok</html>")  # relative link resolves against www
    report = await analyze_site("https://site.example/", checkers=[LlmsTxtChecker()])
    assert report.checks[0].status == Status.OK


async def test_markdown_served_as_html_is_error(web, analyze_site):
    result = await run(web, analyze_site, fixture("valid.txt"), content_type="text/html; charset=utf-8")
    assert result.status == Status.ERROR
    assert result.evidencia["llms_txt"]["problem"] == "wrong_content_type"


async def test_binary_content_type_is_error(web, analyze_site):
    result = await run(web, analyze_site, fixture("valid.txt"), content_type="application/octet-stream")
    assert result.status == Status.ERROR
    assert '"application/octet-stream"' in result.explicacao


async def test_missing_content_type_is_warning_but_parsed(web, analyze_site):
    web.add("https://site.example/", HOME)
    web.route(
        "https://site.example/llms.txt",
        lambda request: httpx.Response(200, content=fixture("valid.txt").encode()),
    )
    add_pages(web)
    report = await analyze_site("https://site.example/", checkers=[LlmsTxtChecker()])
    result = report.checks[0]
    assert result.status == Status.WARNING
    assert result.evidencia["structure"]["issues"] == []


async def test_empty_file_is_warning(web, analyze_site):
    result = await run(web, analyze_site, "  \n")
    assert result.status == Status.WARNING
    assert "vazio" in result.explicacao


async def test_incomplete_structure_is_warning(web, analyze_site):
    result = await run(web, analyze_site, fixture("incomplete.txt"))
    assert result.status == Status.WARNING
    assert "falta o resumo" in result.explicacao
    assert "primeira linha" in result.explicacao


async def test_broken_links_are_warning(web, analyze_site):
    result = await run(web, analyze_site, fixture("valid.txt"), pages=False)
    assert result.status == Status.WARNING
    assert "3 não funcionam" in result.explicacao


async def test_link_to_internal_address_counts_as_broken(web, analyze_site):
    llms = "# T\n> r\n## S\n- [Admin](http://169.254.169.254/latest/meta-data/)"
    result = await run(web, analyze_site, llms)
    assert result.evidencia["links_checked"] == [
        {"url": "http://169.254.169.254/latest/meta-data/", "ok": False, "error": "blocked"}
    ]
    assert not any(r.ip == "169.254.169.254" for r in web.requests)


async def test_link_sample_is_capped(web, analyze_site):
    links = "\n".join(f"- [p{i}](/p{i})" for i in range(12))
    result = await run(web, analyze_site, f"# T\n> r\n## S\n{links}")
    assert len(result.evidencia["links_checked"]) == 5


async def test_server_error_is_warning(web, analyze_site):
    web.add("https://site.example/", HOME)
    web.add("https://site.example/llms.txt", "boom", status=503, content_type="text/plain")
    report = await analyze_site("https://site.example/", checkers=[LlmsTxtChecker()])
    assert report.checks[0].status == Status.WARNING
    assert report.checks[0].evidencia["llms_txt"]["state"] == "unreachable"


async def test_broken_llms_full_is_reported(web, analyze_site):
    web.redirect("https://site.example/llms-full.txt", "/")
    result = await run(web, analyze_site, fixture("valid.txt"))
    assert result.status == Status.WARNING
    assert "llms-full.txt" in result.explicacao


async def test_single_broken_link_uses_singular(web, analyze_site):
    web.add("https://site.example/a", "<html>ok</html>")
    result = await run(web, analyze_site, "# T\n> r\n## S\n- [a](/a)\n- [b](/b)", pages=False)
    assert "1 não funciona:" in result.explicacao
