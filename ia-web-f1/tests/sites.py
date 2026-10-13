"""Complete simulated sites for end-to-end tests (every file the 8 checks read)."""

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
TEXT = " ".join(f"Pão artesanal número {i} com fermentação natural lenta e farinha orgânica." for i in range(40))
LOCAL_BUSINESS = {
    "@context": "https://schema.org",
    "@type": "Bakery",
    "name": "Padaria Pão Quente",
    "address": {"@type": "PostalAddress", "addressLocality": "Campinas", "addressRegion": "SP"},
    "telephone": "+55 19 3333-4444",
}
CF_HEADERS = {"server": "cloudflare", "cf-ray": "8f1e2d3c4b5a6978-GRU", "content-type": "text/html; charset=utf-8"}


def healthy_site(web, host: str = "padaria.example") -> str:
    base = f"https://{host}"
    web.add(
        f"{base}/",
        '<!DOCTYPE html><html lang="pt-BR"><head><title>Padaria Pão Quente | Campinas</title>'
        '<meta name="description" content="Pães artesanais em Campinas">'
        f'<script type="application/ld+json">{json.dumps(LOCAL_BUSINESS)}</script></head>'
        f"<body><h1>Padaria Pão Quente</h1><p>{TEXT}</p></body></html>",
    )
    web.add(
        f"{base}/robots.txt",
        f"User-agent: *\nDisallow: /admin/\n\nSitemap: {base}/sitemap.xml\n",
        content_type="text/plain",
    )
    web.add(
        f"{base}/sitemap.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{base}/</loc></url><url><loc>{base}/cardapio</loc></url></urlset>",
        content_type="application/xml",
    )
    web.add(
        f"{base}/llms.txt",
        "# Padaria Pão Quente\n\n> Padaria artesanal em Campinas (SP).\n\n## Páginas\n\n"
        f"- [Cardápio]({base}/cardapio): pães e preços\n",
        content_type="text/markdown",
    )
    web.add(f"{base}/cardapio", "<html><body>Cardápio</body></html>")
    return f"{base}/"


def closed_site(web, host: str = "fechada.example") -> str:
    """Cloudflare blocking AI search bots, JS-only SPA, fake llms.txt, no sitemap, no structured data."""
    base = f"https://{host}"
    spa = (FIXTURES / "spa" / "index.html").read_text()
    bot_tokens = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
                  "PerplexityBot", "Perplexity-User", "CCBot", "meta-externalagent", "Bytespider")

    def home(request: httpx.Request) -> httpx.Response:
        if any(token in request.headers["user-agent"] for token in bot_tokens):
            return httpx.Response(403, content=b"<html><body>Forbidden</body></html>", headers=CF_HEADERS)
        return httpx.Response(200, content=spa.encode(), headers=CF_HEADERS)

    web.route(f"{base}/", home)
    web.add(f"{base}/static/js/main.js", (FIXTURES / "spa" / "main.js").read_text(), content_type="application/javascript")
    web.add(f"{base}/robots.txt", (FIXTURES / "robots" / "block_search.txt").read_text(), content_type="text/plain")
    web.add(f"{base}/llms.txt", (FIXTURES / "llms" / "soft404.html").read_text())
    return f"{base}/"
