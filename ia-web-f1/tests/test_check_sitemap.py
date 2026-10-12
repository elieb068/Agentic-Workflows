import gzip

from raioxia.checkers.robots_ai import RobotsAIChecker
from raioxia.checkers.sitemap import SitemapChecker
from raioxia.models import Status

HOME = "<html><title>x</title></html>"
URLSET = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://site.example/</loc></url>"
    "<url><loc>https://site.example/a</loc></url>"
    "<url><loc>https://site.example/b</loc></url>"
    "</urlset>"
)
INDEX = (
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://site.example/s1.xml</loc></sitemap>"
    "<sitemap><loc>https://site.example/s2.xml</loc></sitemap>"
    "</sitemapindex>"
)
XML = "application/xml"


async def run(web, analyze_site, robots: str | None = None, checkers=None):
    web.add("https://site.example/", HOME)
    if robots is not None:
        web.add("https://site.example/robots.txt", robots, content_type="text/plain")
    report = await analyze_site("https://site.example/", checkers=checkers or [SitemapChecker()])
    return report.checks[-1]


async def test_declared_sitemap_ok(web, analyze_site):
    web.add("https://site.example/sitemap.xml", URLSET, content_type=XML)
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.xml")
    assert result.status == Status.OK
    assert "com 3 endereços" in result.explicacao
    assert result.evidencia["found"] == "https://site.example/sitemap.xml"


async def test_declared_relative_sitemap_index(web, analyze_site):
    web.add("https://site.example/sitemap_index.xml", INDEX, content_type=XML)
    result = await run(web, analyze_site, "Sitemap: /sitemap_index.xml")
    assert result.status == Status.OK
    assert "reúne 2 sitemaps" in result.explicacao


async def test_undeclared_sitemap_found_with_tip(web, analyze_site):
    web.add("https://site.example/sitemap.xml", URLSET, content_type=XML)
    result = await run(web, analyze_site)
    assert result.status == Status.OK
    assert "Sitemap: https://site.example/sitemap.xml" in result.como_corrigir


async def test_wordpress_core_sitemap_found(web, analyze_site):
    web.add("https://site.example/wp-sitemap.xml", INDEX, content_type=XML)
    result = await run(web, analyze_site)
    assert result.status == Status.OK
    assert len(result.evidencia["probes"]) == 3


async def test_no_sitemap_is_warning(web, analyze_site):
    result = await run(web, analyze_site)
    assert result.status == Status.WARNING
    assert "Não encontramos um sitemap" in result.explicacao


async def test_declared_404_is_error(web, analyze_site):
    result = await run(web, analyze_site, "Sitemap: https://site.example/missing.xml")
    assert result.status == Status.ERROR
    assert "não abre (erro 404)" in result.explicacao


async def test_declared_broken_but_fallback_works(web, analyze_site):
    web.add("https://site.example/sitemap.xml", URLSET, content_type=XML)
    result = await run(web, analyze_site, "Sitemap: https://site.example/old.xml")
    assert result.status == Status.WARNING
    assert "Sitemap: https://site.example/sitemap.xml" in result.como_corrigir


async def test_invalid_xml_is_error(web, analyze_site):
    web.add("https://site.example/sitemap.xml", "<urlset><url><loc>x</url>", content_type=XML)
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.xml")
    assert result.status == Status.ERROR
    assert "corrompido" in result.explicacao


async def test_html_soft_404_is_not_a_sitemap(web, analyze_site):
    web.add("https://site.example/sitemap.xml", "<!doctype html><html><body>Home</body></html>")
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.xml")
    assert result.status == Status.ERROR
    assert "página do site" in result.explicacao


async def test_xml_entity_expansion_is_refused(web, analyze_site):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
        "<urlset><url><loc>&lol2;</loc></url></urlset>"
    )
    web.add("https://site.example/sitemap.xml", bomb, content_type=XML)
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.xml")
    assert result.status == Status.ERROR
    assert result.evidencia["probes"][0]["problem"] == "invalid_xml"


async def test_gzipped_sitemap(web, analyze_site):
    web.add("https://site.example/sitemap.xml.gz", gzip.compress(URLSET.encode()), content_type="application/gzip")
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.xml.gz")
    assert result.status == Status.OK
    assert result.evidencia["probes"][0]["entries"] == 3


async def test_text_sitemap(web, analyze_site):
    web.add("https://site.example/sitemap.txt", "https://site.example/\nhttps://site.example/a\n", content_type="text/plain")
    result = await run(web, analyze_site, "Sitemap: https://site.example/sitemap.txt")
    assert result.status == Status.OK
    assert result.evidencia["probes"][0]["kind"] == "text"


async def test_robots_fetched_once_for_both_checks(web, analyze_site):
    web.add("https://site.example/sitemap.xml", URLSET, content_type=XML)
    await run(web, analyze_site, "Sitemap: /sitemap.xml", checkers=[RobotsAIChecker(), SitemapChecker()])
    assert [r.path for r in web.requests].count("/robots.txt") == 1
