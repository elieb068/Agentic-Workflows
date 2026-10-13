"""Check 5: is the home page content already in the raw HTML, or only after JavaScript runs?

Most AI crawlers do not execute JavaScript. We compare the words a non-JS
crawler reads in the raw HTML with the words a browser shows after rendering.
coverage = share of the rendered vocabulary that is already in the raw HTML.
"""

import re

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.net.render import Renderer, RenderError
from raioxia.parsing.html import HtmlDoc

OK_COVERAGE = 0.7
ERROR_COVERAGE = 0.3
MIN_RENDERED_WORDS = 30  # below this the page has too little text to judge
_WORD = re.compile(r"\w{3,}")

FIX_SSR = (
    "Peça a quem desenvolveu o site para entregar o texto já pronto no HTML, com renderização no servidor (SSR) "
    "ou páginas estáticas (SSG). Em Next.js, Nuxt e Astro isso é o padrão; em sites React ou Vue \"puros\", "
    "existem serviços de pré-renderização. WordPress, Wix e Shopify normalmente já entregam o texto pronto."
)


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def compare_text(raw_text: str, rendered_text: str) -> dict:
    raw, rendered = words(raw_text), words(rendered_text)
    raw_vocab, rendered_vocab = set(raw), set(rendered)
    coverage = len(rendered_vocab & raw_vocab) / len(rendered_vocab) if rendered_vocab else 1.0
    missing = [w for w in dict.fromkeys(rendered) if w not in raw_vocab][:15]
    return {
        "raw_words": len(raw),
        "rendered_words": len(rendered),
        "coverage": round(coverage, 2),
        "missing_sample": missing,
    }


@register
class JsDependencyChecker(Checker):
    id = "js_dependency"
    titulo = "Conteúdo sem depender de JavaScript"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        try:
            home = await ctx.home()
        except FetchError as exc:
            return self.result(
                Status.UNKNOWN,
                "Não conseguimos abrir sua página inicial para verificar este item.",
                evidencia={"fetch_error": exc.kind},
            )
        if home.status_code >= 400:
            return self.result(
                Status.UNKNOWN,
                "Sua página inicial respondeu com erro, então não deu para verificar este item.",
                evidencia={"status_code": home.status_code},
            )

        raw_text = HtmlDoc(home.text).visible_text()
        if not ctx.render:
            return self.result(
                Status.UNKNOWN,
                "A comparação com um navegador foi desativada nesta análise. Este item não conta na sua nota.",
                evidencia={"raw_words": len(words(raw_text)), "render": "disabled"},
            )

        try:
            async with Renderer() as renderer:
                rendered = await renderer.render(home.final_url, ctx.fetch)
        except RenderError as exc:
            return self.result(
                Status.UNKNOWN,
                "Não conseguimos carregar sua página num navegador para fazer a comparação. Este item não conta "
                "na sua nota.",
                evidencia={"raw_words": len(words(raw_text)), "render_error": str(exc)},
            )

        evidence = compare_text(raw_text, rendered.text) | {
            "render_ms": rendered.elapsed_ms,
            "render_requests": rendered.requests,
            "render_blocked": rendered.blocked,
        }
        raw_words, rendered_words, coverage = evidence["raw_words"], evidence["rendered_words"], evidence["coverage"]

        if rendered_words < MIN_RENDERED_WORDS or coverage >= OK_COVERAGE:
            return self.result(
                Status.OK,
                f"O conteúdo da sua página inicial já vem pronto no HTML ({raw_words} palavras), sem depender de "
                "JavaScript. Os robôs de IA conseguem ler tudo.",
                evidencia=evidence,
            )
        if coverage < ERROR_COVERAGE:
            return self.result(
                Status.ERROR,
                "Quase todo o conteúdo da sua página inicial só aparece depois que o navegador executa "
                "JavaScript. A maioria dos robôs de IA não executa JavaScript: para eles, sua página está "
                f"praticamente vazia ({raw_words} palavras, contra {rendered_words} que um visitante vê).",
                FIX_SSR,
                evidence,
            )
        hidden = round((1 - coverage) * 100)
        return self.result(
            Status.WARNING,
            f"Parte do conteúdo da sua página inicial (cerca de {hidden}% do texto) só aparece depois que o "
            "navegador executa JavaScript. Os robôs de IA que não executam JavaScript não veem essa parte.",
            "Veja quais blocos são carregados por JavaScript (listas de produtos, depoimentos, textos que "
            f"aparecem depois) e peça para entregá-los já no HTML. {FIX_SSR}",
            evidence,
        )
