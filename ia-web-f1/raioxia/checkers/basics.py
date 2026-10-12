"""Check 8: HTTPS, home response time, <title>, meta description and declared language."""

from dataclasses import dataclass

import httpx

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker, worst
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.parsing.html import HtmlDoc

SLOW_MS = 2000

_FETCH_FAILURES = {
    "timeout": "Sua página inicial demorou demais para responder e desistimos de esperar.",
    "tls": "Não conseguimos abrir sua página inicial porque o certificado HTTPS (o cadeado) parece inválido ou vencido.",
    "connect": "Não conseguimos nos conectar ao seu site. Ele pode estar fora do ar.",
    "too_many_redirects": "Sua página inicial fica redirecionando de um endereço para outro sem parar.",
    "protocol": "Sua página inicial respondeu de um jeito que não conseguimos ler.",
}


@dataclass
class Item:
    key: str
    status: Status
    problem: str = ""
    fix: str = ""


@register
class BasicsChecker(Checker):
    id = "basics"
    titulo = "Básicos do site"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        try:
            home = await ctx.home()
        except FetchError as exc:
            return self.result(
                Status.ERROR,
                _FETCH_FAILURES.get(exc.kind, _FETCH_FAILURES["connect"]),
                "Confira se o site abre no navegador. Se abrir normalmente, fale com quem cuida da "
                "hospedagem: algo está impedindo o acesso de visitantes automáticos.",
                {"fetch_error": exc.kind, "detail": exc.detail},
            )

        evidence: dict = {
            "final_url": home.final_url,
            "status_code": home.status_code,
            "response_time_ms": home.elapsed_ms,
        }
        items = [await self._https(ctx, home.final_url, evidence), self._speed(home.elapsed_ms)]

        if home.status_code >= 400:
            items.append(
                Item(
                    "home_status",
                    Status.ERROR,
                    f"Sua página inicial respondeu com erro (código {home.status_code}) quando tentamos abri-la.",
                    "Verifique se o site está no ar. Se ele abre normalmente para você, pode ser um bloqueio "
                    "de segurança contra robôs: veja o item \"Bloqueio na borda\".",
                )
            )
        else:
            doc = HtmlDoc(home.text)
            evidence |= {"title": doc.title, "description": doc.meta("description"), "lang": doc.lang}
            items += self._content_items(doc)

        evidence["items"] = {i.key: i.status.value for i in items}
        status = worst(i.status for i in items)
        problems = [i for i in items if i.status != Status.OK]
        if not problems:
            return self.result(
                Status.OK,
                f"Seu site usa HTTPS, abriu em {home.elapsed_ms / 1000:.1f} segundos e tem título, descrição "
                "e idioma declarados. É o básico para a IA entender e confiar na sua página.",
                evidencia=evidence,
            )
        return self.result(
            status,
            "\n".join(f"• {i.problem}" for i in problems),
            "\n".join(f"• {i.fix}" for i in problems),
            evidence,
        )

    async def _https(self, ctx: AnalysisContext, final_url: str, evidence: dict) -> Item:
        url = httpx.URL(final_url)
        if url.scheme == "https":
            return Item("https", Status.OK)
        try:
            probe = await ctx.fetch(str(url.copy_with(scheme="https", port=None)))
            https_works = probe.status_code < 400
        except FetchError:
            https_works = False
        evidence["https_available"] = https_works
        if https_works:
            return Item(
                "https",
                Status.WARNING,
                "Seu site tem HTTPS, mas quem digita o endereço sem \"https\" continua na versão sem cadeado.",
                "Configure um redirecionamento permanente (301) de http:// para https://. Muitas hospedagens "
                "têm uma opção \"Forçar HTTPS\" no painel.",
            )
        return Item(
            "https",
            Status.ERROR,
            "Seu site não tem HTTPS (o cadeado no navegador). Sites sem HTTPS passam menos confiança para "
            "buscadores e assistentes de IA, e o navegador mostra o aviso \"Não seguro\".",
            "Ative um certificado HTTPS. A maioria das hospedagens oferece de graça (Let's Encrypt) no painel; "
            "se você usa Cloudflare, ative o SSL/TLS por lá.",
        )

    def _speed(self, elapsed_ms: int) -> Item:
        if elapsed_ms <= SLOW_MS:
            return Item("response_time", Status.OK)
        return Item(
            "response_time",
            Status.WARNING,
            f"Sua página inicial levou {elapsed_ms / 1000:.1f} segundos para responder. Robôs de IA têm pouca "
            "paciência: páginas lentas podem ser abandonadas antes de serem lidas.",
            "Deixe a página mais leve (imagens comprimidas, menos plugins) e considere uma hospedagem ou CDN "
            "mais rápida.",
        )

    def _content_items(self, doc: HtmlDoc) -> list[Item]:
        items = []
        items.append(
            Item("title", Status.OK)
            if doc.title
            else Item(
                "title",
                Status.WARNING,
                "A página não tem título. É o nome que buscadores e IAs usam para apresentar o seu site.",
                "Adicione um título curto com o nome da empresa e o que ela faz, por exemplo: "
                "\"Padaria Pão Quente | Pães artesanais em Campinas\".",
            )
        )
        items.append(
            Item("description", Status.OK)
            if doc.meta("description")
            else Item(
                "description",
                Status.WARNING,
                "A página não tem descrição (meta description), o pequeno resumo que aparece embaixo do título "
                "nos resultados de busca.",
                "Escreva uma ou duas frases sobre o que sua empresa faz e onde atende. No WordPress, plugins como "
                "Yoast ou Rank Math têm um campo para isso.",
            )
        )
        items.append(
            Item("lang", Status.OK)
            if doc.lang
            else Item(
                "lang",
                Status.WARNING,
                "A página não informa em que idioma está escrita.",
                "Adicione lang=\"pt-BR\" na tag <html> do site. No WordPress, isso vem do idioma escolhido em "
                "Configurações > Geral.",
            )
        )
        return items
