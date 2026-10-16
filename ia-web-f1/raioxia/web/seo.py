"""Our own robots.txt, sitemap.xml, llms.txt and JSON-LD: the site must pass its own 8 checks."""

import json
from datetime import date

from raioxia.web.content import FAQ
from raioxia.web.guides import GUIDES, Guide
from raioxia.web.templating import COMPANY

# Indexable pages and the date their content last changed (sitemap <lastmod>): bump it with the page.
# /excluir-dados is a utility form: reachable from the footer, kept out of search (noindex).
PUBLIC_PAGES = {
    "/": date(2026, 10, 16),
    "/guias": date(2026, 10, 16),
    "/privacidade": date(2026, 10, 16),
    "/termos": date(2026, 10, 16),
    "/bot": date(2026, 10, 15),
}
PRIVATE_PREFIXES = ["/r/", "/confirmar/", "/descadastrar/", "/excluir-dados/", "/api/"]


def robots_txt(base_url: str) -> str:
    lines = ["# Raio-X IA: AI crawlers and search engines are welcome on the public pages.", "User-agent: *", "Allow: /"]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]  # personal reports and e-mail links
    # AdSense's crawler reads report pages to match ads (no personal data is shown on them).
    lines += ["", "User-agent: Mediapartners-Google", "Allow: /"]
    lines += ["", f"Sitemap: {base_url}/sitemap.xml", ""]
    return "\n".join(lines)


def sitemap_pages() -> dict[str, date]:
    return PUBLIC_PAGES | {g.path: g.updated for g in GUIDES}


def sitemap_xml(base_url: str) -> str:
    urls = "".join(
        f"<url><loc>{base_url}{path}</loc><lastmod>{updated.isoformat()}</lastmod></url>" for path, updated in sitemap_pages().items()
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


def llms_txt(base_url: str) -> str:
    guides = "\n".join(f"- [{g.title}]({base_url}{g.path}): {g.summary}" for g in GUIDES)
    return f"""# Raio-X IA

> Ferramenta gratuita, em português, que verifica se um site está aberto para os robôs de inteligência artificial (ChatGPT, Gemini, Claude, Perplexity): robots.txt, bloqueio no firewall ou CDN, llms.txt, instruções de indexação, dependência de JavaScript, dados estruturados, sitemap e básicos. Dá uma nota de 0 a 100 e explica como corrigir cada item. Produto da {COMPANY["brand"]}.

## Páginas

- [Início]({base_url}/): o que o Raio-X IA verifica e o formulário para pedir o relatório
- [Sobre nosso robô]({base_url}/bot): o que o RaioXIA-Bot acessa e como pedir para um site não ser analisado
- [Política de privacidade]({base_url}/privacidade): dados coletados, bases legais (LGPD), retenção e direitos
- [Termos de uso]({base_url}/termos): regras de uso e natureza do relatório

## Guias

- [Todos os guias]({base_url}/guias): como deixar um site visível para a IA, e como a nota é calculada
{guides}

## Contato

- [E-mail](mailto:{COMPANY["contact_email"]}): {COMPANY["contact_email"]}
- [{COMPANY["brand"]}]({COMPANY["website"]}): consultoria em IA e automação
"""


def _script_safe(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def landing_jsonld(base_url: str, logo_url: str) -> list[str]:
    organization = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": COMPANY["brand"],
        "legalName": COMPANY["legal_name"],
        "taxID": COMPANY["cnpj"],
        "url": COMPANY["website"],
        "logo": f"{base_url}{logo_url}",
        "email": COMPANY["contact_email"],
    }
    application = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": COMPANY["product"],
        "url": f"{base_url}/",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "inLanguage": "pt-BR",
        "description": "Verifica se o seu site está aberto para os robôs de inteligência artificial e mostra como corrigir.",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "BRL"},
        "publisher": {"@type": "Organization", "name": COMPANY["brand"], "url": COMPANY["website"]},
    }
    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q["q"], "acceptedAnswer": {"@type": "Answer", "text": q["a"]}} for q in FAQ
        ],
    }
    return [_script_safe(block) for block in (organization, application, faq)]


def _publisher(base_url: str, logo_url: str) -> dict:
    return {"@type": "Organization", "name": COMPANY["brand"], "url": COMPANY["website"],
            "logo": {"@type": "ImageObject", "url": f"{base_url}{logo_url}"}}


def _breadcrumbs(base_url: str, trail: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": f"{base_url}{path}"} for i, (name, path) in enumerate(trail, 1)
        ],
    }


def guides_index_jsonld(base_url: str) -> list[str]:
    collection = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "Guias do Raio-X IA",
        "url": f"{base_url}/guias",
        "inLanguage": "pt-BR",
        "hasPart": [{"@type": "Article", "headline": g.title, "url": f"{base_url}{g.path}"} for g in GUIDES],
    }
    return [_script_safe(b) for b in (collection, _breadcrumbs(base_url, [("Início", "/"), ("Guias", "/guias")]))]


def guide_jsonld(base_url: str, guide: Guide, logo_url: str, image_url: str) -> list[str]:
    article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": guide.title,
        "description": guide.description,
        "url": f"{base_url}{guide.path}",
        "mainEntityOfPage": f"{base_url}{guide.path}",
        "inLanguage": "pt-BR",
        "datePublished": guide.published.isoformat(),
        "dateModified": guide.updated.isoformat(),
        "image": f"{base_url}{image_url}",
        "author": _publisher(base_url, logo_url),
        "publisher": _publisher(base_url, logo_url),
    }
    trail = [("Início", "/"), ("Guias", "/guias"), (guide.title.split(":")[0], guide.path)]
    return [_script_safe(b) for b in (article, _breadcrumbs(base_url, trail))]
