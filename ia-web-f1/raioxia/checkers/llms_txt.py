"""Check 3: /llms.txt (and the optional /llms-full.txt).

A valid file answers 200 without redirecting to the home page, is served as
text, is not HTML and follows the llms.txt markdown layout. A sample of its
links is fetched to catch broken ones.
"""

import asyncio
from dataclasses import dataclass, field

import httpx

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker, worst
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.parsing.llms_txt import LlmsTxt, parse_llms_txt
from raioxia.parsing.robots import looks_like_html

TEXT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
MAX_LINKS_CHECKED = 5

_STRUCTURE_ISSUES = {
    "missing_title": "falta o título principal (uma linha começando com \"# \", com o nome da empresa)",
    "title_not_first": "o título principal (\"# \") não está na primeira linha",
    "multiple_titles": "há mais de um título principal (\"# \"); deve haver só um",
    "missing_summary": "falta o resumo logo abaixo do título (uma linha começando com \"> \")",
    "no_link_sections": "não há seções com links (\"## \" seguido de uma lista de links para as suas páginas)",
}


@dataclass
class FileProbe:
    url: str
    state: str  # ok | missing | unreachable | invalid
    problem: str | None = None  # html | redirect_home | wrong_content_type | no_content_type | empty | http_error
    status_code: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    text: str = field(default="", repr=False)

    def evidence(self) -> dict:
        return {
            "url": self.url,
            "state": self.state,
            "problem": self.problem,
            "status_code": self.status_code,
            "final_url": self.final_url,
            "content_type": self.content_type,
        }


def _is_home(url: str, home_url: str | None) -> bool:
    parsed = httpx.URL(url)
    if parsed.path in ("", "/"):
        return True
    return home_url is not None and parsed.copy_with(query=None, fragment=None) == httpx.URL(home_url).copy_with(
        query=None, fragment=None
    )


async def probe_file(ctx: AnalysisContext, path: str) -> FileProbe:
    url = await ctx.site_url(path)
    try:
        response = await ctx.fetch(url)
    except FetchError as exc:
        return FileProbe(url, "unreachable", exc.kind)
    probe = FileProbe(
        url,
        "ok",
        status_code=response.status_code,
        final_url=response.final_url,
        content_type=response.content_type or None,
    )
    if response.status_code in (404, 410):
        probe.state = "missing"
        return probe
    if response.status_code >= 400:
        probe.state, probe.problem = "unreachable", "http_error"
        return probe

    try:
        home_url = (await ctx.home()).final_url
    except FetchError:
        home_url = None
    text = response.text
    probe.text = text
    if response.redirected and _is_home(response.final_url, home_url):
        probe.state, probe.problem = "invalid", "redirect_home"
    elif looks_like_html(text) or response.content_type in ("text/html", "application/xhtml+xml"):
        # Text served as text/html is mangled by HTML parsers, so it counts as HTML too.
        probe.state, probe.problem = "invalid", "html" if looks_like_html(text) else "wrong_content_type"
    elif response.content_type and response.content_type not in TEXT_TYPES:
        probe.state, probe.problem = "invalid", "wrong_content_type"
    elif not text.strip():
        probe.state, probe.problem = "invalid", "empty"
    elif not response.content_type:
        probe.problem = "no_content_type"  # still usable
    return probe


async def check_links(ctx: AnalysisContext, base: str, urls: list[str]) -> list[dict]:
    resolved: list[str] = []
    for raw in urls:
        try:
            url = str(httpx.URL(base).join(raw))
        except httpx.InvalidURL:
            continue
        if url.startswith(("http://", "https://")) and url not in resolved:
            resolved.append(url)
    resolved = resolved[:MAX_LINKS_CHECKED]

    async def one(url: str) -> dict:
        try:
            response = await ctx.fetch(url)
        except FetchError as exc:
            return {"url": url, "ok": False, "error": exc.kind}
        return {"url": url, "ok": response.status_code < 400, "status_code": response.status_code}

    return list(await asyncio.gather(*(one(u) for u in resolved)))


def _file_problem_text(name: str, probe: FileProbe) -> str:
    return {
        "html": f"O endereço {name} devolve uma página HTML em vez de um arquivo de texto. Isso costuma acontecer "
        "quando o site mostra a página inicial ou uma página de erro para qualquer endereço. Para a IA, é como se "
        "o arquivo existisse, mas com o conteúdo errado.",
        "redirect_home": f"O endereço {name} redireciona para a página inicial. Para a IA, é como se o arquivo "
        "existisse, mas com o conteúdo errado.",
        "wrong_content_type": f"O arquivo {name} é enviado como \"{probe.content_type}\", e não como texto. "
        "Robôs podem ignorá-lo ou interpretá-lo errado.",
        "empty": f"O arquivo {name} existe, mas está vazio.",
        "no_content_type": f"O servidor não informa o tipo do arquivo {name}. Alguns robôs podem não reconhecê-lo "
        "como texto.",
    }.get(probe.problem or "", f"O arquivo {name} não respondeu quando tentamos abri-lo.")


_FILE_FIXES = {
    "html": "Crie o arquivo de verdade, em texto simples. Se você não quer ter esse arquivo, faça o endereço "
    "responder \"não encontrado\" (erro 404) em vez de mostrar outra página.",
    "redirect_home": "Remova o redirecionamento: o endereço deve mostrar o próprio arquivo de texto ou responder "
    "\"não encontrado\" (erro 404).",
    "wrong_content_type": "Peça a quem cuida do servidor para enviar o arquivo como text/plain ou text/markdown.",
    "empty": "Preencha o arquivo (veja o modelo abaixo) ou remova-o.",
    "no_content_type": "Peça a quem cuida do servidor para enviar o arquivo como text/plain ou text/markdown.",
}
_FILE_FIX_UNREACHABLE = "Confira se o arquivo abre no navegador e se o servidor não está bloqueando o acesso."
_FILE_STATUS = {
    "html": Status.ERROR,
    "redirect_home": Status.ERROR,
    "wrong_content_type": Status.ERROR,
    "empty": Status.WARNING,
    "no_content_type": Status.WARNING,
}


def _template(origin: str) -> str:
    return (
        "Modelo de llms.txt (texto simples, na raiz do site):\n"
        "# Nome da sua empresa\n"
        "> Uma frase sobre o que vocês fazem e onde atendem.\n"
        "## Páginas principais\n"
        f"- [Serviços]({origin}servicos): o que oferecemos\n"
        f"- [Contato]({origin}contato): telefone, endereço e horário"
    )


@register
class LlmsTxtChecker(Checker):
    id = "llms_txt"
    titulo = "Arquivo llms.txt"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        main, full = await asyncio.gather(probe_file(ctx, "/llms.txt"), probe_file(ctx, "/llms-full.txt"))
        origin = await ctx.site_url("/")
        evidence: dict = {"llms_txt": main.evidence(), "llms_full_txt": full.evidence()}
        items: list[tuple[Status, str, str]] = []

        if main.state == "missing":
            items.append(
                (
                    Status.WARNING,
                    "Seu site não tem o arquivo llms.txt. É um padrão novo: um arquivo de texto que apresenta o "
                    "seu site para assistentes de IA, com um resumo e os links das páginas mais importantes. Nem "
                    "todos os assistentes usam ainda, mas é simples de fazer e ajuda os que usam.",
                    f"Crie o arquivo em {main.url}.\n{_template(origin)}",
                )
            )
        elif main.state == "unreachable":
            items.append((Status.WARNING, _file_problem_text("/llms.txt", main), _FILE_FIX_UNREACHABLE))
        elif main.problem:
            items.append(
                (
                    _FILE_STATUS[main.problem],
                    _file_problem_text("/llms.txt", main),
                    _FILE_FIXES[main.problem],
                )
            )

        doc: LlmsTxt | None = None
        if main.state == "ok":
            doc = parse_llms_txt(main.text)
            evidence["structure"] = {
                "title": doc.title,
                "summary": doc.summary,
                "sections": doc.sections,
                "links": len(doc.links),
                "issues": doc.issues,
            }
            if doc.issues:
                missing = "; ".join(_STRUCTURE_ISSUES[i] for i in doc.issues)
                items.append(
                    (
                        Status.WARNING,
                        f"Seu llms.txt existe, mas está incompleto: {missing}.",
                        f"Ajuste o arquivo para seguir o formato padrão.\n{_template(origin)}",
                    )
                )
            links = await check_links(ctx, main.final_url or main.url, [link.url for link in doc.links] + doc.other_links)
            evidence["links_checked"] = links
            broken = [link["url"] for link in links if not link["ok"]]
            if broken:
                items.append(
                    (
                        Status.WARNING,
                        f"Dos {len(links)} links que testamos no seu llms.txt, {len(broken)} "
                        f"{'não funciona' if len(broken) == 1 else 'não funcionam'}: {', '.join(broken)}.",
                        "Corrija ou remova os links quebrados do llms.txt.",
                    )
                )

        # llms-full.txt is optional: only a broken one is reported.
        if full.state == "invalid" or (full.state == "unreachable" and full.problem == "http_error"):
            items.append(
                (
                    Status.WARNING,
                    "Sobre o llms-full.txt (a versão completa, opcional): " + _file_problem_text("/llms-full.txt", full),
                    _FILE_FIXES.get(full.problem or "", _FILE_FIX_UNREACHABLE),
                )
            )

        if not items:
            assert doc is not None
            extra = " Você também tem o llms-full.txt, a versão completa." if full.state == "ok" else ""
            return self.result(
                Status.OK,
                f"Seu llms.txt está no formato certo: título, resumo e {len(doc.links)} links em "
                f"{sum(1 for n in doc.sections.values() if n)} seção(ões), e os links que testamos funcionam.{extra}",
                evidencia=evidence,
            )
        return self.result(
            worst(status for status, _, _ in items),
            "\n".join(f"• {text}" for _, text, _ in items),
            "\n".join(f"• {fix}" for _, _, fix in items),
            evidence,
        )
