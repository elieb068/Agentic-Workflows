"""Check 4: indexing directives on the home page (<meta name="robots"> and X-Robots-Tag).

Directives can target everyone ("robots" meta / plain header) or one crawler
("googlebot" meta / "googlebot: noindex" header).
"""

from collections import defaultdict

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker, worst
from raioxia.config import AgentCategory
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError
from raioxia.parsing.html import HtmlDoc

ALL = "*"
META = "a tag meta robots da página"
HEADER = "o cabeçalho HTTP X-Robots-Tag"
# Directives whose value contains ":" (not a user-agent prefix).
_VALUED = {"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"}

# target -> directive -> sources
Directives = dict[str, dict[str, set[str]]]


def parse_directive_list(value: str) -> set[str]:
    return {part.strip().lower().replace(" ", "") for part in value.split(",") if part.strip()}


def collect(meta: dict[str, list[str]], headers: list[str], agent_tokens: set[str]) -> Directives:
    found: Directives = defaultdict(lambda: defaultdict(set))
    for name, contents in meta.items():
        target = ALL if name == "robots" else name if name in agent_tokens else None
        if target is None:
            continue
        for content in contents:
            for directive in parse_directive_list(content):
                found[target][directive].add(META)
    for header in headers:
        target, value = ALL, header
        prefix, sep, rest = header.partition(":")
        prefix = prefix.strip().lower()
        if sep and prefix and "," not in prefix and prefix not in _VALUED:
            target, value = prefix, rest
        for directive in parse_directive_list(value):
            found[target][directive].add(HEADER)
    return found


def _where(sources: set[str]) -> str:
    return " e ".join(sorted(sources))


@register
class MetaRobotsChecker(Checker):
    id = "meta_robots"
    titulo = "Instruções de indexação da página"

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

        agents = {a.token.lower(): a for a in ctx.config.agents.agents}
        meta = {k: v for k, v in HtmlDoc(home.text).metas_by_name().items() if k == "robots" or k in agents}
        headers = home.headers.get_list("x-robots-tag")
        found = collect(meta, headers, set(agents))
        evidence = {
            "meta": meta,
            "x_robots_tag": headers,
            "directives": {t: sorted(d) for t, d in found.items()},
        }

        def is_search(target: str) -> bool:
            agent = agents.get(target)
            return target == ALL or (agent is not None and agent.category != AgentCategory.TRAINING)

        def targets_with(*names: str, search_only: bool = True) -> dict[str, set[str]]:
            hits: dict[str, set[str]] = {}
            for target, directives in found.items():
                if search_only and not is_search(target):
                    continue
                sources = set().union(*(directives.get(n, set()) for n in names))
                if sources:
                    hits[target] = sources
            return hits

        def label(target: str) -> str:
            return "todos os robôs" if target == ALL else agents[target].token

        items: list[tuple[Status, str, str]] = []

        noindex = targets_with("noindex", "none")
        if noindex:
            who = ", ".join(label(t) for t in noindex)
            where = _where(set().union(*noindex.values()))
            items.append(
                (
                    Status.ERROR,
                    f"Sua página inicial pede para não ser incluída nos resultados (\"noindex\", valendo para "
                    f"{who}), por meio de {where}. Com isso, seu site some do Google e das respostas de IA.",
                    "Remova o \"noindex\" da página inicial. No WordPress, desmarque \"Evitar que mecanismos de "
                    "busca indexem este site\" em Configurações > Leitura. Se a instrução vem do cabeçalho "
                    "X-Robots-Tag, peça a quem cuida do servidor para retirá-la.",
                )
            )

        nofollow = {t: s for t, s in targets_with("nofollow", "none").items() if t not in noindex}
        if nofollow:
            items.append(
                (
                    Status.WARNING,
                    f"Sua página inicial pede para os robôs não seguirem os links dela (\"nofollow\", via "
                    f"{_where(set().union(*nofollow.values()))}). Assim eles têm mais dificuldade para chegar "
                    "às suas outras páginas.",
                    "Remova o \"nofollow\" da página inicial, a não ser que haja um motivo específico para ele.",
                )
            )

        for directive, what in (("noai", "o conteúdo"), ("noimageai", "as imagens")):
            hits = targets_with(directive, search_only=False)
            if hits:
                items.append(
                    (
                        Status.WARNING,
                        f"Sua página tem a instrução \"{directive}\", que pede para {what} não ser usado por IA. "
                        "Os principais assistentes não seguem essa instrução oficialmente, mas ferramentas que a "
                        "respeitam vão deixar seu site de fora das respostas.",
                        f"Se você quer aparecer em respostas de IA, remova \"{directive}\". Se a intenção é só "
                        "impedir o treino de IA, bloqueie os robôs de treino (como GPTBot e ClaudeBot) no robots.txt.",
                    )
                )

        nosnippet = targets_with("nosnippet", "max-snippet:0")
        if nosnippet:
            items.append(
                (
                    Status.WARNING,
                    "Sua página proíbe que trechos dela apareçam nos resultados (\"nosnippet\"). Com isso, o "
                    "Google também deixa de usá-la nas respostas com IA (AI Overviews).",
                    "Remova \"nosnippet\" (ou \"max-snippet:0\") se você quer que trechos do seu site apareçam "
                    "nas respostas.",
                )
            )

        if not items:
            return self.result(
                Status.OK,
                "Sua página inicial não tem nenhuma instrução que impeça buscadores ou robôs de IA de usá-la.",
                evidencia=evidence,
            )
        return self.result(
            worst(status for status, _, _ in items),
            "\n".join(f"• {text}" for _, text, _ in items),
            "\n".join(f"• {fix}" for _, _, fix in items),
            evidence,
        )
