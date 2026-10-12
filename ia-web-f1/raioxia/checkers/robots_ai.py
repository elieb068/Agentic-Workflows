"""Check 1: what robots.txt says to each AI crawler (config/ai_agents.yaml)."""

from dataclasses import dataclass
from enum import StrEnum

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker, worst
from raioxia.checkers.robots_file import RobotsFile, RobotsState, load_robots
from raioxia.config import Agent, AgentCategory
from raioxia.models import CheckResult, Status

# Stands in for "any inner page": tells "only the home is open" apart from "the site is open".
PROBE_PATH = "/pagina-exemplo"


class Verdict(StrEnum):
    ALLOWED = "permitido"
    PARTIAL = "parcial"  # home allowed, inner pages blocked
    BLOCKED = "bloqueado"


@dataclass
class AgentResult:
    agent: Agent
    verdict: Verdict
    evidence: dict

    @property
    def label(self) -> str:
        return f"{self.agent.token} ({self.agent.company})"

    @property
    def is_search(self) -> bool:
        return self.agent.category in (AgentCategory.SEARCH, AgentCategory.USER)


def _names(results: list[AgentResult]) -> str:
    return ", ".join(r.label for r in results)


def evaluate_agent(robots_file: RobotsFile, agent: Agent) -> AgentResult:
    if robots_file.state == RobotsState.UNREACHABLE:
        return AgentResult(agent, Verdict.BLOCKED, {"reason": "robots_unreachable"})
    robots = robots_file.robots
    home = robots.decide(agent.token, "/")
    page = robots.decide(agent.token, PROBE_PATH)
    verdict = Verdict.BLOCKED if not home.allowed else Verdict.PARTIAL if not page.allowed else Verdict.ALLOWED
    deciding = home.rule if not home.allowed else page.rule
    return AgentResult(
        agent,
        verdict,
        {
            "matched_by": home.matched_by,
            "group_lines": [g.line for g in home.groups],
            "rule": deciding.text if deciding else None,
            "rule_line": deciding.line if deciding else None,
        },
    )


@register
class RobotsAIChecker(Checker):
    id = "robots_ai"
    titulo = "Robôs de IA no robots.txt"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        robots_file = await load_robots(ctx)
        agents = [evaluate_agent(robots_file, a) for a in ctx.config.agents.agents]
        evidence = {
            "robots_url": robots_file.url,
            "state": robots_file.state.value,
            "status_code": robots_file.status_code,
            "fetch_error": robots_file.fetch_error,
            "is_html": robots_file.is_html,
            "agents": {
                r.agent.token: {
                    "company": r.agent.company,
                    "category": r.agent.category.value,
                    "verdict": r.verdict.value,
                    **r.evidence,
                }
                for r in agents
            },
        }

        if robots_file.state == RobotsState.UNREACHABLE:
            what = f"deu erro (código {robots_file.status_code})" if robots_file.status_code else "não respondeu"
            return self.result(
                Status.ERROR,
                f"O arquivo robots.txt do seu site {what}. Quando isso acontece, o Google e os robôs de IA "
                "entendem que o site inteiro está bloqueado e deixam de visitá-lo.",
                "Peça a quem cuida do site para verificar por que o endereço /robots.txt dá erro. Se você não "
                "precisa de regras, basta que ele responda \"não encontrado\" (404) ou seja um arquivo vazio.",
                evidence,
            )

        problems: list[str] = []
        fixes: list[str] = []
        status = Status.OK

        if robots_file.is_html:
            status = Status.WARNING
            problems.append(
                "O endereço do seu robots.txt devolve uma página do site em vez das regras para robôs. Os robôs "
                "ignoram esse conteúdo: ninguém é bloqueado, mas qualquer regra que você pensou ter colocado não vale."
            )
            fixes.append(
                "Crie um arquivo de texto robots.txt de verdade na raiz do site (ou peça a quem cuida do site)."
            )

        search_blocked = [r for r in agents if r.is_search and r.verdict == Verdict.BLOCKED]
        search_partial = [r for r in agents if r.is_search and r.verdict == Verdict.PARTIAL]
        training_limited = [r for r in agents if not r.is_search and r.verdict != Verdict.ALLOWED]

        if search_blocked:
            status = Status.ERROR
            problems.append(
                f"Seu robots.txt bloqueia robôs que levam o seu site para as respostas de IA e buscadores: "
                f"{_names(search_blocked)}. Assim, seu site pode não aparecer quando alguém pergunta ao "
                "ChatGPT, ao Perplexity, ao Claude ou ao Google sobre o que você oferece."
            )
            example = search_blocked[0].agent.token
            fixes.append(
                f"Edite o robots.txt e remova o bloqueio de: {_names(search_blocked)}. Para liberar um robô, "
                f"use por exemplo as linhas \"User-agent: {example}\" e \"Allow: /\". Se você usa Cloudflare ou "
                "um plugin de SEO, confira se o bloqueio não vem das configurações de robôs de IA deles."
            )
        if search_partial:
            status = worst([status, Status.WARNING])
            problems.append(
                f"Estes robôs de busca só podem ler a sua página inicial; o resto do site está bloqueado para "
                f"eles: {_names(search_partial)}."
            )
            fixes.append(
                f"Revise as linhas \"Disallow\" que valem para: {_names(search_partial)}. Libere as páginas que "
                "você quer ver citadas nas respostas de IA."
            )
        if training_limited:
            status = worst([status, Status.WARNING])
            problems.append(
                f"Você bloqueia (total ou parcialmente) robôs que coletam conteúdo para treinar IA: "
                f"{_names(training_limited)}. É uma escolha legítima e não tira o seu site das respostas."
            )
            fixes.append(
                f"Se o bloqueio é intencional, está tudo certo. Se quiser que os modelos de IA conheçam sua "
                f"marca, libere: {_names(training_limited)}."
            )

        retired = []
        for token in ctx.config.agents.retired_tokens:
            groups = robots_file.robots.groups_naming(token.token)
            if groups:
                retired.append({"token": token.token, "lines": [g.line for g in groups]})
                status = worst([status, Status.WARNING])
                current = " e ".join(token.replaced_by)
                problems.append(
                    f"Seu robots.txt tem regras para \"{token.token}\", um nome antigo que nenhum robô usa mais. "
                    f"Essas regras não fazem nada; o nome atual é {current}."
                )
                lines = " e ".join(f"\"User-agent: {name}\"" for name in token.replaced_by)
                fixes.append(f"Troque \"User-agent: {token.token}\" por {lines}.")
        evidence["retired_rules"] = retired

        if not problems:
            if robots_file.state == RobotsState.MISSING:
                text = "Seu site não tem robots.txt. Na prática, isso libera todos os robôs, inclusive os de IA."
            else:
                text = (
                    f"Seu robots.txt libera todos os {len(agents)} robôs de IA que verificamos, tanto os que levam "
                    "o seu site para as respostas quanto os de treino."
                )
            return self.result(Status.OK, text, evidencia=evidence)

        if not search_blocked and not search_partial:
            problems.append("Os robôs que levam o seu site para as respostas de IA e buscadores estão liberados.")
        return self.result(
            status,
            "\n".join(f"• {p}" for p in problems),
            "\n".join(f"• {f}" for f in fixes),
            evidence,
        )
