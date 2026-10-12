"""Check 7: is there a reachable sitemap (declared in robots.txt or at a usual address)?"""

import asyncio
import re
import zlib
from dataclasses import asdict, dataclass
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker
from raioxia.checkers.robots_file import RobotsState, load_robots
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.parsing.robots import looks_like_html

FALLBACK_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]
MAX_DECLARED = 3
MAX_UNCOMPRESSED = 5 * 1024 * 1024
_ROOT = re.compile(rb"<(?:[\w.-]+:)?(urlset|sitemapindex)[\s>/]", re.IGNORECASE)

_PROBLEMS = {
    "unreachable": "não respondeu",
    "invalid_xml": "está com o conteúdo corrompido (XML inválido)",
    "html": "devolve uma página do site em vez do sitemap",
    "not_sitemap": "não tem o formato de um sitemap",
}


@dataclass
class SitemapProbe:
    url: str
    ok: bool
    problem: str | None = None
    status_code: int | None = None
    kind: str | None = None  # urlset | sitemapindex | text
    entries: int | None = None

    def describe_problem(self) -> str:
        if self.problem and self.problem.startswith("http_"):
            return f"não abre (erro {self.status_code})"
        return _PROBLEMS.get(self.problem or "", "não pôde ser lido")


def _gunzip(data: bytes) -> bytes | None:
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, MAX_UNCOMPRESSED)
    except zlib.error:
        return None


def inspect_sitemap(url: str, status_code: int, content: bytes, truncated: bool) -> SitemapProbe:
    if content[:2] == b"\x1f\x8b":  # .xml.gz served without Content-Encoding
        content = _gunzip(content) or b""
    root = _ROOT.search(content[:4096])
    if root:
        kind = root.group(1).decode().lower()
        if truncated:  # too big to read whole: the start looks right, trust it
            return SitemapProbe(url, True, status_code=status_code, kind=kind)
        try:
            tree = fromstring(content)
        except (ParseError, DefusedXmlException, ValueError):
            return SitemapProbe(url, False, "invalid_xml", status_code, kind)
        entries = sum(1 for el in tree.iter() if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == "loc")
        return SitemapProbe(url, True, status_code=status_code, kind=kind, entries=entries)

    text = content.decode("utf-8", errors="replace")
    if looks_like_html(text):
        return SitemapProbe(url, False, "html", status_code)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(line.startswith(("http://", "https://")) for line in lines):
        return SitemapProbe(url, True, status_code=status_code, kind="text", entries=len(lines))
    return SitemapProbe(url, False, "not_sitemap", status_code)


async def probe(ctx: AnalysisContext, url: str) -> SitemapProbe:
    try:
        response = await ctx.fetch(url)
    except FetchError:
        return SitemapProbe(url, False, "unreachable")
    if response.status_code >= 400:
        return SitemapProbe(url, False, f"http_{response.status_code}", response.status_code)
    return inspect_sitemap(url, response.status_code, response.content, response.truncated)


def _summary(found: SitemapProbe) -> str:
    if found.kind == "sitemapindex" and found.entries:
        return f", que reúne {found.entries} sitemaps"
    if found.entries:
        return f", com {found.entries} endereços"
    return ""


@register
class SitemapChecker(Checker):
    id = "sitemap"
    titulo = "Sitemap (índice das páginas)"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        robots_file = await load_robots(ctx)
        declared = []
        if robots_file.state == RobotsState.PARSED:
            for raw in robots_file.robots.sitemaps:
                try:
                    url = str(httpx.URL(robots_file.url).join(raw))
                except httpx.InvalidURL:
                    continue
                if url not in declared:
                    declared.append(url)
        declared = declared[:MAX_DECLARED]

        declared_probes = list(await asyncio.gather(*(probe(ctx, u) for u in declared)))
        good = next((p for p in declared_probes if p.ok), None)

        fallback_probes: list[SitemapProbe] = []
        if good is None:
            for path in FALLBACK_PATHS:
                result = await probe(ctx, await ctx.site_url(path))
                fallback_probes.append(result)
                if result.ok:
                    break
        fallback = next((p for p in fallback_probes if p.ok), None)

        evidence = {
            "declared": declared,
            "probes": [asdict(p) for p in declared_probes + fallback_probes],
            "found": (good or fallback).url if (good or fallback) else None,
        }

        if good:
            return self.result(
                Status.OK,
                f"Encontramos o seu sitemap ({good.url}){_summary(good)}, indicado no robots.txt. Ele funciona "
                "como um índice que ajuda os robôs a achar todas as suas páginas.",
                evidencia=evidence,
            )

        if declared_probes:
            broken = declared_probes[0]
            fix = (
                "Corrija o endereço do sitemap no robots.txt ou gere o sitemap de novo (no WordPress, plugins como "
                "Yoast ou Rank Math fazem isso)."
            )
            if fallback:
                return self.result(
                    Status.WARNING,
                    f"Seu robots.txt indica um sitemap em {broken.url}, mas ele {broken.describe_problem()}. "
                    f"Encontramos outro sitemap que funciona em {fallback.url}, só que os robôs seguem o "
                    "endereço do robots.txt.",
                    f"Atualize a linha do robots.txt para: Sitemap: {fallback.url}",
                    evidence,
                )
            return self.result(
                Status.ERROR,
                f"Seu robots.txt indica um sitemap em {broken.url}, mas ele {broken.describe_problem()}. Os robôs "
                "seguem esse endereço e não encontram o índice das suas páginas.",
                fix,
                evidence,
            )

        if fallback:
            return self.result(
                Status.OK,
                f"Encontramos o seu sitemap ({fallback.url}){_summary(fallback)}. Ele funciona como um índice que "
                "ajuda os robôs a achar todas as suas páginas.",
                f"Dica: para facilitar ainda mais, acrescente no robots.txt a linha: Sitemap: {fallback.url}",
                evidence,
            )

        example = await ctx.site_url("/sitemap.xml")
        return self.result(
            Status.WARNING,
            "Não encontramos um sitemap no seu site. O sitemap é um índice das suas páginas: sem ele, os robôs "
            "de IA e de busca podem demorar ou nem chegar a algumas delas.",
            "Gere um sitemap (no WordPress, plugins como Yoast ou Rank Math fazem isso automaticamente; Wix e "
            f"Shopify já criam um) e declare no robots.txt com a linha: Sitemap: {example}",
            evidence,
        )
