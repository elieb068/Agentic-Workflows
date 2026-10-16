"""Guides (/guias): one per check, plus the AI crawler list. The text lives in templates/guides/<slug>.html.

Each guide answers a real search ("como liberar o GPTBot", "o que é llms.txt") and is linked from the matching
item of the report and of the landing page. Bump `updated` when a guide's content changes: it feeds the page,
the Article JSON-LD and the sitemap's <lastmod>.
"""

from dataclasses import dataclass
from datetime import date

from raioxia.config import AgentCategory, get_config

PUBLISHED = date(2026, 10, 16)


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str  # the page's H1
    seo_title: str  # <title>, before " | Raio-X IA"
    description: str
    summary: str  # one line for the guide list
    updated: date
    minutes: int  # reading time
    check_id: str | None = None  # the check this guide explains (raioxia/checkers)
    published: date = PUBLISHED

    @property
    def path(self) -> str:
        return f"/guias/{self.slug}"


GUIDES = [
    Guide(
        "robots-txt-para-ia", "robots.txt para IA: como liberar o ChatGPT, o Claude e o Gemini",
        "robots.txt para IA: como liberar GPTBot e ClaudeBot",
        "Como escrever o robots.txt para os robôs de IA: quais liberar, quais você pode bloquear sem sumir das respostas e exemplos prontos para copiar.",
        "Quais robôs liberar, quais você pode bloquear e exemplos prontos.", PUBLISHED, 6, "robots_ai",
    ),
    Guide(
        "cloudflare-bloqueando-ia", "Cloudflare bloqueando a IA: como conferir e liberar os robôs",
        "Cloudflare bloqueando IA: como conferir e liberar",
        "Desde 15/09/2026, domínios novos na Cloudflare bloqueiam por padrão robôs de treino e agentes de IA nas páginas com anúncios. Veja como conferir o painel e liberar a IA.",
        "O que mudou em 15/09/2026 e onde mexer no painel.", PUBLISHED, 5, "edge_block",
    ),
    Guide(
        "o-que-e-llms-txt", "O que é llms.txt e como criar o seu",
        "O que é llms.txt e como criar o seu (com modelo)",
        "O llms.txt apresenta o seu site aos assistentes de IA em um arquivo de texto simples. Veja o formato, um modelo para copiar e os erros mais comuns.",
        "O arquivo que apresenta o seu site à IA, com modelo para copiar.", PUBLISHED, 5, "llms_txt",
    ),
    Guide(
        "noindex-e-noai", "noindex, nofollow e noai: as instruções que escondem o seu site",
        "noindex, nofollow e noai: o que fazem e como remover",
        "Uma linha esquecida no código pode tirar o seu site do Google e das respostas de IA. Veja como achar noindex, nofollow, noai e X-Robots-Tag e quando removê-los.",
        "Uma linha esquecida que tira o site do Google e da IA.", PUBLISHED, 4, "meta_robots",
    ),
    Guide(
        "site-em-javascript-e-ia", "Site em JavaScript: por que a IA não lê e como resolver",
        "Site em JavaScript: por que o ChatGPT não lê",
        "A maioria dos robôs de IA não executa JavaScript. Se o texto do seu site só aparece depois do carregamento, a IA vê uma página vazia. Veja como testar e corrigir.",
        "Por que a IA vê uma página vazia e como corrigir.", PUBLISHED, 5, "js_dependency",
    ),
    Guide(
        "dados-estruturados-para-ia", "Dados estruturados: como a IA descobre quem é a sua empresa",
        "Dados estruturados (JSON-LD) para IA: guia com exemplo",
        "Dados estruturados (JSON-LD) dizem à IA, em código, o nome, o endereço e o contato da sua empresa. Veja um exemplo pronto e como validar.",
        "JSON-LD com exemplo pronto para a sua empresa.", PUBLISHED, 5, "structured_data",
    ),
    Guide(
        "sitemap", "Sitemap: o índice que ajuda os robôs a achar as suas páginas",
        "Sitemap: o que é, como criar e declarar no robots.txt",
        "O sitemap é a lista das páginas do seu site para os robôs de busca e de IA. Veja como criar, onde publicar e como declarar no robots.txt.",
        "Como criar o sitemap e declarar no robots.txt.", PUBLISHED, 4, "sitemap",
    ),
    Guide(
        "basicos-do-site-para-ia", "HTTPS, velocidade, título e idioma: o básico que a IA confere",
        "HTTPS, velocidade, título e idioma: o básico para a IA",
        "Cadeado HTTPS, resposta rápida, título, descrição e idioma declarado: os básicos que ajudam robôs de busca e de IA a entender o seu site.",
        "Cadeado, velocidade, título, descrição e idioma.", PUBLISHED, 4, "basics",
    ),
    Guide(
        "robos-de-ia", "Robôs de IA: quem são e o que cada um faz",
        "Robôs de IA: GPTBot, ClaudeBot, PerplexityBot e outros",
        "Os principais robôs de IA (GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot, Google-Extended e outros): de quem são, o que fazem e como liberar cada um.",
        "A lista dos robôs, de quem são e o que fazem.", PUBLISHED, 6,
    ),
]

_BY_SLUG = {g.slug: g for g in GUIDES}
_BY_CHECK = {g.check_id: g for g in GUIDES if g.check_id}

# Official crawler documentation, by company (linked from the crawler list).
CRAWLER_DOCS = {
    "OpenAI": "https://developers.openai.com/api/docs/bots",
    "Anthropic": "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler",
    "Perplexity": "https://docs.perplexity.ai/guides/bots",
    "Google": "https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers",
    "Microsoft": "https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0",
    "Apple": "https://support.apple.com/en-us/119829",
    "Common Crawl": "https://commoncrawl.org/ccbot",
    "Meta": "https://developers.facebook.com/docs/sharing/webmasters/web-crawlers",
}

CATEGORY_LABELS = {
    AgentCategory.SEARCH: "Busca e respostas",
    AgentCategory.USER: "A pedido de uma pessoa",
    AgentCategory.TRAINING: "Treino de modelos",
}


def by_slug(slug: str) -> Guide | None:
    return _BY_SLUG.get(slug)


def for_check(check_id: str) -> Guide | None:
    return _BY_CHECK.get(check_id)


def crawler_rows() -> list[dict]:
    """The crawlers we check (config/ai_agents.yaml), search first: the ones that put a site in AI answers."""
    order = list(CATEGORY_LABELS)
    agents = sorted(get_config().agents.agents, key=lambda a: order.index(a.category))
    return [
        {"token": a.token, "company": a.company, "category": a.category.value, "label": CATEGORY_LABELS[a.category],
         "robots_only": a.robots_only, "docs": CRAWLER_DOCS.get(a.company)}
        for a in agents
    ]


def crawlers_reviewed() -> date:
    return get_config().agents.last_reviewed


def retired_tokens() -> list[dict]:
    return [{"token": t.token, "company": t.company, "replaced_by": t.replaced_by} for t in get_config().agents.retired_tokens]


def score_weights() -> list[dict]:
    """Weight of each check in the score, in points out of 100 (config/scoring.yaml)."""
    weights = get_config().scoring.weights
    total = sum(weights.values())
    return [{"guide": _BY_CHECK[check], "points": round(100 * w / total)} for check, w in weights.items() if check in _BY_CHECK]
