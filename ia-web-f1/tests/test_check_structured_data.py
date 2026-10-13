import json

from raioxia.checkers.structured_data import StructuredDataChecker, iter_types, parse_blocks
from raioxia.models import Status


def ld(data) -> str:
    text = data if isinstance(data, str) else json.dumps(data)
    return f'<script type="application/ld+json">{text}</script>'


async def run(web, analyze_site, head: str):
    web.add("https://site.example/", f"<html><head><title>x</title>{head}</head><body>oi</body></html>")
    report = await analyze_site("https://site.example/", checkers=[StructuredDataChecker()])
    return report.checks[0]


def test_iter_types_handles_graph_lists_prefixes_and_nesting():
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": ["Bakery", "schema:Store"], "address": {"@type": "PostalAddress"}},
            {"@type": "https://schema.org/WebSite"},
        ],
    }
    assert sorted(set(iter_types(data))) == ["Bakery", "PostalAddress", "Store", "WebSite"]


def test_parse_blocks_reports_position_and_unwraps_comments():
    blocks = parse_blocks(['<!-- {"@type": "Organization"} -->', '{"@type": "Organization",}'])
    assert blocks[0] == {"valid": True, "types": ["Organization"]}
    assert blocks[1]["valid"] is False and "linha 1" in blocks[1]["error"]


async def test_organization_is_ok(web, analyze_site):
    result = await run(web, analyze_site, ld({"@context": "https://schema.org", "@type": "Organization", "name": "A"}))
    assert result.status == Status.OK
    assert "Organization" in result.explicacao


async def test_local_business_subtype_in_graph_is_ok(web, analyze_site):
    result = await run(web, analyze_site, ld({"@graph": [{"@type": "WebSite"}, {"@type": "Dentist"}]}))
    assert result.status == Status.OK
    assert result.evidencia["relevant_types"] == ["Dentist"]


async def test_product_only_is_ok_with_tip(web, analyze_site):
    result = await run(web, analyze_site, ld({"@type": "Product", "name": "Bolo"}))
    assert result.status == Status.OK
    assert "Organization" in result.como_corrigir


async def test_only_irrelevant_types_is_warning(web, analyze_site):
    result = await run(web, analyze_site, ld({"@type": "WebSite"}) + ld({"@type": "BreadcrumbList"}))
    assert result.status == Status.WARNING
    assert "BreadcrumbList, WebSite" in result.explicacao


async def test_no_jsonld_is_warning(web, analyze_site):
    result = await run(web, analyze_site, "")
    assert result.status == Status.WARNING
    assert "não tem dados estruturados" in result.explicacao


async def test_microdata_is_mentioned(web, analyze_site):
    web.add(
        "https://site.example/",
        '<html><body><div itemscope itemtype="https://schema.org/Bakery">x</div></body></html>',
    )
    report = await analyze_site("https://site.example/", checkers=[StructuredDataChecker()])
    result = report.checks[0]
    assert result.status == Status.WARNING
    assert "microdata (Bakery)" in result.explicacao


async def test_invalid_json_is_error_even_with_valid_blocks(web, analyze_site):
    result = await run(web, analyze_site, ld({"@type": "Organization"}) + ld('{"@type": "FAQPage", }'))
    assert result.status == Status.ERROR
    assert "1 bloco(s)" in result.explicacao
    assert "Organization" in result.explicacao
    assert result.evidencia["blocks"][1]["valid"] is False


async def test_type_attribute_with_params_and_case(web, analyze_site):
    head = '<script type="Application/LD+JSON; charset=utf-8">{"@type": "Organization"}</script>'
    result = await run(web, analyze_site, head)
    assert result.status == Status.OK
