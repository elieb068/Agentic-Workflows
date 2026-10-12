"""Fetches and interprets /robots.txt once per analysis (shared by checks 1 and 7).

How crawlers treat the fetch outcome (RFC 9309, section 2.3.1):
- 2xx: parse the rules;
- 4xx (except 429): no robots.txt, everything is allowed;
- 5xx, 429 or unreachable: crawlers must assume everything is disallowed.
"""

from dataclasses import dataclass
from enum import StrEnum

from raioxia.checkers.base import AnalysisContext
from raioxia.net.fetcher import FetchError
from raioxia.parsing.robots import Robots, looks_like_html


class RobotsState(StrEnum):
    PARSED = "parsed"
    MISSING = "missing"  # 4xx: allow all
    UNREACHABLE = "unreachable"  # 5xx/429/network error: disallow all


@dataclass
class RobotsFile:
    url: str
    state: RobotsState
    robots: Robots  # empty rules unless PARSED
    status_code: int | None = None
    fetch_error: str | None = None
    is_html: bool = False  # a 2xx answer that is really an HTML page (rules are ignored)


async def load_robots(ctx: AnalysisContext) -> RobotsFile:
    return await ctx.memo(("robots_file",), lambda: _load(ctx))


async def _load(ctx: AnalysisContext) -> RobotsFile:
    url = await ctx.site_url("/robots.txt")
    empty = Robots(groups=[], sitemaps=[])
    try:
        response = await ctx.fetch(url)
    except FetchError as exc:
        return RobotsFile(url=url, state=RobotsState.UNREACHABLE, robots=empty, fetch_error=exc.kind)

    code = response.status_code
    if code == 429 or code >= 500:
        return RobotsFile(url=url, state=RobotsState.UNREACHABLE, robots=empty, status_code=code)
    if code >= 400:
        return RobotsFile(url=url, state=RobotsState.MISSING, robots=empty, status_code=code)

    text = response.text
    if looks_like_html(text):
        return RobotsFile(url=url, state=RobotsState.PARSED, robots=empty, status_code=code, is_html=True)
    return RobotsFile(url=url, state=RobotsState.PARSED, robots=Robots.parse(text), status_code=code)
