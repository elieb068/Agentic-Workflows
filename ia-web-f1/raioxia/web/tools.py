"""Free instant tools (no e-mail): the robots.txt AI tester (/ferramentas/robots-txt-ia).

It runs the report's own robots check (checkers.robots_ai), so the verdicts match the full Raio-X. It fetches only
the home page (to follow redirects to the site's real address) and /robots.txt, and stores nothing: the address
typed is not saved, the IP only feeds the anti-abuse counters (hashed, as in api.ratelimit).
"""

from raioxia.checkers.robots_ai import RobotsAIChecker
from raioxia.config import AgentCategory
from raioxia.models import Status
from raioxia.net.fetcher import SafeFetcher
from raioxia.pipeline import is_opted_out
from raioxia.runner import AnalysisError, analyze, normalize_url

CHECK_TIMEOUT = 20.0

VERDICTS = {
    "permitido": ("Liberado", "ok"),
    "parcial": ("Só a página inicial", "aviso"),
    "bloqueado": ("Bloqueado", "erro"),
}
GROUPS = [
    (AgentCategory.SEARCH.value, "Busca e respostas", "Levam o seu site para as respostas da IA. O ideal é liberar todos."),
    (AgentCategory.USER.value, "A pedido de uma pessoa", "Abrem uma página quando alguém pede ao assistente."),
    (AgentCategory.TRAINING.value, "Treino de modelos", "Bloquear é uma escolha legítima e não tira o site das respostas."),
]
STATES = {
    "parsed": "encontrado",
    "missing": "não existe (tudo liberado)",
    "unreachable": "deu erro (os robôs entendem que tudo está bloqueado)",
}


class ToolError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def robots_ai_report(raw_url: str, *, fetcher: SafeFetcher | None = None) -> dict:
    """The robots verdict for one site, ready for the template. Raises ToolError with a PT-BR message."""
    try:
        normalized = normalize_url(raw_url)
    except AnalysisError as exc:
        raise ToolError(exc.message) from exc
    host = normalized.url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
    if is_opted_out(host):
        raise ToolError("O responsável por este site pediu para ele não ser analisado pelo Raio-X IA.")
    try:
        report = await analyze(normalized.url, fetcher=fetcher, checkers=[RobotsAIChecker()], render=False, check_timeout=CHECK_TIMEOUT)
    except AnalysisError as exc:
        raise ToolError(exc.message) from exc

    check = report.checks[0]
    if check.status == Status.UNKNOWN:
        raise ToolError("Não conseguimos ler o robots.txt desse site agora. Tente de novo em alguns minutos.")
    evidence = check.evidencia
    agents = evidence.get("agents", {})
    groups = []
    for category, title, note in GROUPS:
        rows = [
            {"token": token, "company": a["company"], "label": VERDICTS[a["verdict"]][0], "tone": VERDICTS[a["verdict"]][1], "rule": a.get("rule")}
            for token, a in agents.items() if a["category"] == category
        ]
        if rows:
            groups.append({"title": title, "note": note, "rows": rows})
    search = [a for a in agents.values() if a["category"] != AgentCategory.TRAINING.value]
    return {
        "host": host,
        "robots_url": evidence.get("robots_url"),
        "state": STATES.get(evidence.get("state"), evidence.get("state")),
        "is_html": evidence.get("is_html", False),
        "status": check.status.value,
        "explicacao": check.explicacao,
        "como_corrigir": check.como_corrigir,
        "groups": groups,
        "search_open": sum(1 for a in search if a["verdict"] == "permitido"),
        "search_total": len(search),
    }
