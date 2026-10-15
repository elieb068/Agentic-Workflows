"""Our own robots.txt, sitemap.xml, llms.txt and JSON-LD: the site must pass its own 8 checks."""

import json

from raioxia.web.content import FAQ
from raioxia.web.templating import COMPANY

PUBLIC_PAGES = ["/", "/privacidade", "/termos", "/bot", "/excluir-dados"]
PRIVATE_PREFIXES = ["/r/", "/confirmar/", "/descadastrar/", "/excluir-dados/", "/api/"]


def robots_txt(base_url: str) -> str:
    lines = ["# Raio-X IA: AI crawlers and search engines are welcome on the public pages.", "User-agent: *", "Allow: /"]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]  # personal reports and e-mail links
    lines += ["", f"Sitemap: {base_url}/sitemap.xml", ""]
    return "\n".join(lines)


def sitemap_xml(base_url: str) -> str:
    urls = "".join(f"<url><loc>{base_url}{path}</loc></url>" for path in PUBLIC_PAGES)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'


def llms_txt(base_url: str) -> str:
    return f"""# Raio-X IA

> Ferramenta gratuita, em português, que verifica se um site está aberto para os robôs de inteligência artificial (ChatGPT, Gemini, Claude, Perplexity): robots.txt, bloqueio no firewall ou CDN, llms.txt, instruções de indexação, dependência de JavaScript, dados estruturados, sitemap e básicos. Dá uma nota de 0 a 100 e explica como corrigir cada item. Produto da {COMPANY["brand"]}.

## Páginas

- [Início]({base_url}/): o que o Raio-X IA verifica e o formulário para pedir o relatório
- [Sobre nosso robô]({base_url}/bot): o que o RaioXIA-Bot acessa e como pedir para um site não ser analisado
- [Política de privacidade]({base_url}/privacidade): dados coletados, bases legais (LGPD), retenção e direitos
- [Termos de uso]({base_url}/termos): regras de uso e natureza do relatório

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
