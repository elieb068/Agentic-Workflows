"""Check 2: does the CDN/WAF treat AI crawlers differently from a browser?

The home page is fetched once with a browser User-Agent (baseline) and once
with each crawler's User-Agent; status, size, final URL and challenge signals
are compared. Our requests do not come from the crawlers' verified IPs, so the
result is only indicative, and the report always says so.
"""

import asyncio
from dataclasses import dataclass

import httpx

from raioxia.checkers import register
from raioxia.checkers.base import AnalysisContext, Checker, worst
from raioxia.config import Agent, AgentCategory
from raioxia.models import CheckResult, Status
from raioxia.net.fetcher import FetchError, FetchResult

SIZE_RATIO_LIMIT = 0.3  # bot page smaller than 30% of the browser page = "different page"
MIN_BASELINE_BYTES = 2048  # below this, size comparisons are meaningless

# (signal name, lowercase marker) searched in the first part of the body. Only markers that appear on
# block/challenge pages: e.g. "/cdn-cgi/challenge-platform/" is also injected into normal Cloudflare pages.
_BODY_MARKERS = [
    ("cloudflare_challenge", "<title>just a moment...</title>"),
    ("cloudflare_challenge", "_cf_chl_opt"),
    ("cloudflare_challenge", "cf-browser-verification"),
    ("cloudflare_block", "attention required! | cloudflare"),
    ("cloudflare_block", "cf-error-details"),
    ("akamai_block", "reference&#32;&#35;"),
    ("sucuri_block", "sucuri website firewall"),
    ("imperva_block", "incapsula incident id"),
    ("datadome_challenge", "captcha-delivery.com"),
    ("perimeterx_challenge", "px-captcha"),
]
_CDNS = [
    ("Cloudflare", lambda h: "cf-ray" in h or "cloudflare" in h.get("server", "").lower()),
    ("Akamai", lambda h: "akamaighost" in h.get("server", "").lower() or "akamai-grn" in h),
    ("Fastly", lambda h: "x-fastly-request-id" in h or "fastly" in h.get("x-served-by", "").lower()
     or h.get("x-served-by", "").lower().startswith("cache-")),
    ("Amazon CloudFront", lambda h: "x-amz-cf-id" in h or "cloudfront" in h.get("via", "").lower()),
    ("Vercel", lambda h: "x-vercel-id" in h),
    ("Netlify", lambda h: "x-nf-request-id" in h),
    ("Sucuri", lambda h: "x-sucuri-id" in h or "sucuri" in h.get("server", "").lower()),
    ("Imperva", lambda h: "x-iinfo" in h or "incapsula" in h.get("x-cdn", "").lower()),
    ("Azure Front Door", lambda h: "x-azure-ref" in h),
]

CAVEAT = (
    "Importante: este teste é indicativo. Nossas requisições não vêm dos endereços oficiais desses robôs, e "
    "alguns firewalls tratam o robô verdadeiro de forma diferente."
)
CLOUDFLARE_STEPS = (
    "Como conferir no painel da Cloudflare (os nomes dos menus podem variar conforme o plano):\n"
    "1. Entre em dash.cloudflare.com e escolha o seu domínio.\n"
    "2. Abra a área de controle de robôs de IA (\"AI Crawl Control\") e veja quais robôs aparecem como "
    "bloqueados. Libere os de busca e de respostas, como OAI-SearchBot, ChatGPT-User, Claude-SearchBot, "
    "Claude-User e PerplexityBot.\n"
    "3. Em Segurança > Bots, confira se a opção de bloquear robôs de IA ou o \"Bot Fight Mode\" estão ligados.\n"
    "4. Em Segurança > Eventos, pesquise pelo nome do robô (por exemplo, GPTBot) para ver se houve bloqueios reais."
)
GENERIC_STEPS = (
    "Peça a quem cuida do site ou da hospedagem para revisar as regras do firewall (WAF) ou do CDN que "
    "bloqueiam visitantes pelo \"User-Agent\" e liberar os robôs de busca e de respostas de IA."
)

_REASONS = {
    "status": "erro {code}",
    "challenge": "desafio anti-robô",
    "no_response": "sem resposta",
    "smaller_page": "página bem menor",
    "other_url": "levado para outro endereço",
}


def detect_challenge(response: FetchResult) -> list[str]:
    signals = []
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        signals.append("cf_mitigated")
    if response.headers.get("x-amzn-waf-action", "").lower() in ("challenge", "captcha"):
        signals.append("aws_waf_challenge")
    head = response.text[:20000].lower()
    signals += sorted({name for name, marker in _BODY_MARKERS if marker in head})
    return signals


def detect_cdn(headers: httpx.Headers) -> list[str]:
    lowered = httpx.Headers({k.lower(): v for k, v in headers.items()})
    return [name for name, matches in _CDNS if matches(lowered)]


def _same_page(a: str, b: str) -> bool:
    ua, ub = httpx.URL(a), httpx.URL(b)
    return (ua.host, ua.path) == (ub.host, ub.path)


@dataclass
class Probe:
    agent: Agent
    verdict: str  # same | blocked | different | unknown
    reason: str | None
    evidence: dict

    @property
    def label(self) -> str:
        reason = _REASONS.get(self.reason or "", "").format(code=self.evidence.get("status_code"))
        return f"{self.agent.token} ({reason})" if reason else self.agent.token

    @property
    def is_search(self) -> bool:
        return self.agent.category != AgentCategory.TRAINING


def compare(agent: Agent, baseline: FetchResult, result: FetchResult | FetchError) -> Probe:
    if isinstance(result, FetchError):
        if result.kind in ("blocked", "dns"):
            return Probe(agent, "unknown", None, {"fetch_error": result.kind})
        return Probe(agent, "blocked", "no_response", {"fetch_error": result.kind})

    signals = detect_challenge(result)
    evidence = {
        "status_code": result.status_code,
        "bytes": len(result.content),
        "final_url": result.final_url,
        "signals": signals,
    }
    if result.status_code >= 400:
        return Probe(agent, "blocked", "status", evidence)
    if signals:
        return Probe(agent, "blocked", "challenge", evidence)
    if not _same_page(result.final_url, baseline.final_url):
        return Probe(agent, "different", "other_url", evidence)
    base_size = len(baseline.content)
    if base_size >= MIN_BASELINE_BYTES and len(result.content) < base_size * SIZE_RATIO_LIMIT:
        return Probe(agent, "different", "smaller_page", evidence)
    return Probe(agent, "same", None, evidence)


@register
class EdgeBlockChecker(Checker):
    id = "edge_block"
    titulo = "Bloqueio no firewall ou CDN"

    async def run(self, ctx: AnalysisContext) -> CheckResult:
        try:
            url = (await ctx.home()).final_url
        except FetchError:
            url = ctx.url
        agents = [a for a in ctx.config.agents.agents if not a.robots_only]

        async def fetch(ua: str) -> FetchResult | FetchError:
            try:
                return await ctx.fetch(url, user_agent=ua)
            except FetchError as exc:
                return exc

        baseline, *bot_results = await asyncio.gather(
            fetch(ctx.config.agents.browser_user_agent), *(fetch(a.user_agent) for a in agents)
        )
        evidence: dict = {
            "url": url,
            "indicative": True,
            "skipped_robots_only": [a.token for a in ctx.config.agents.agents if a.robots_only],
        }

        baseline_signals = [] if isinstance(baseline, FetchError) else detect_challenge(baseline)
        if not isinstance(baseline, FetchError):
            evidence["cdn"] = cdn = detect_cdn(baseline.headers)
            evidence["baseline"] = {
                "status_code": baseline.status_code,
                "bytes": len(baseline.content),
                "final_url": baseline.final_url,
                "signals": baseline_signals,
            }
        else:
            cdn = []
            evidence["baseline"] = {"fetch_error": baseline.kind}
        steps = CLOUDFLARE_STEPS if "Cloudflare" in cdn else GENERIC_STEPS

        if isinstance(baseline, FetchError) or baseline.status_code >= 400 or baseline_signals:
            return self.result(
                Status.UNKNOWN,
                "Seu site bloqueou até o nosso acesso simulando um navegador comum, então não deu para comparar "
                "com os robôs. Isso costuma acontecer com firewalls muito rígidos, que barram qualquer acesso "
                f"vindo de servidores, e pode estar afetando os robôs de IA também.\n{CAVEAT}",
                steps,
                evidence,
            )

        probes = [compare(agent, baseline, result) for agent, result in zip(agents, bot_results, strict=True)]
        evidence["agents"] = {
            p.agent.token: {"category": p.agent.category.value, "verdict": p.verdict, "reason": p.reason, **p.evidence}
            for p in probes
        }
        cdn_text = f" ({', '.join(cdn)})" if cdn else ""

        def names(selected: list[Probe]) -> str:
            return ", ".join(p.label for p in selected)

        search_blocked = [p for p in probes if p.is_search and p.verdict == "blocked"]
        search_different = [p for p in probes if p.is_search and p.verdict == "different"]
        training_limited = [p for p in probes if not p.is_search and p.verdict in ("blocked", "different")]
        items: list[tuple[Status, str]] = []
        if search_blocked:
            items.append(
                (
                    Status.ERROR,
                    f"Quando simulamos o acesso dos robôs de busca e de respostas de IA, seu site barrou: "
                    f"{names(search_blocked)}. Para um navegador comum, o site abriu normalmente. Isso indica que "
                    f"um firewall ou CDN{cdn_text} está bloqueando esses robôs, e seu site pode estar invisível "
                    "para o ChatGPT, o Perplexity, o Claude e outros.",
                )
            )
        if search_different:
            items.append(
                (
                    Status.WARNING,
                    f"Para estes robôs de busca, o site devolveu uma página bem diferente da que um navegador "
                    f"recebe: {names(search_different)}. Pode ser uma página de bloqueio ou de verificação.",
                )
            )
        if training_limited:
            # Informational, like in robots_ai: blocking training crawlers does not lower the score.
            items.append(
                (
                    Status.OK,
                    f"Os robôs de treino de IA também foram barrados: {names(training_limited)}. Pode ser "
                    "intencional; muitos CDNs, como a Cloudflare, fazem isso por padrão.",
                )
            )

        if not items:
            return self.result(
                Status.OK,
                f"Simulamos o acesso de {len(probes)} robôs de IA e todos receberam a mesma página que um "
                f"navegador comum.\n{CAVEAT}",
                steps if "Cloudflare" in cdn else "",
                evidence,
            )
        status = worst(s for s, _ in items)
        if status == Status.OK:
            items.append((Status.OK, "Os robôs de busca e de respostas de IA receberam a mesma página que um navegador."))
        return self.result(
            status,
            "\n".join(f"• {text}" for _, text in items) + f"\n{CAVEAT}",
            steps if status != Status.OK or "Cloudflare" in cdn else "",
            evidence,
        )
