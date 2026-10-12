# M1 — Analysis core (approved 2026-10-12)

Goal: `raioxia/checkers/` with the 8 checks, CLI `python -m raioxia analyze <url>` printing JSON, and tests with simulated sites (no network).

## Folder structure

```
config/
  ai_agents.yaml        bots: token, company, category, full UA; retired tokens
  scoring.yaml          per-check weights, status values, score bands
raioxia/
  __main__.py           python -m raioxia
  cli.py                analyze <url> [--no-render] [--pretty]
  config.py             loads and validates the YAMLs
  models.py             Status, CheckResult, AnalysisReport
  runner.py             normalizes URL, builds context, runs checks, scores
  scoring.py
  net/
    ssrf.py             scheme/port/DNS validation, blocks internal IPs (v4/v6)
    fetcher.py          SafeFetcher: httpx, manual redirects, 5 MB cap, timeouts, timing
    render.py           Playwright; all browser traffic routed through SafeFetcher
  parsing/
    robots.py           own robots.txt parser (RFC 9309)
    html.py             visible text, metas, lang, JSON-LD
    llms_txt.py         markdown structure validation
  checkers/
    base.py             Checker interface + AnalysisContext
    __init__.py         check registry (pluggable for phases 2-4)
    robots_ai.py        1
    edge_block.py       2
    llms_txt.py         3
    meta_robots.py      4
    js_dependency.py    5
    structured_data.py  6
    sitemap.py          7
    basics.py           8
tests/
  conftest.py           FakeSite (URL -> response) + fake DNS resolver
  fixtures/sites/       simulated sites
  test_*.py
```

## Design decisions

- **Pluggable checkers**: each checker has an `id` and `async run(ctx) -> CheckResult`, registered in `checkers/__init__.py`. The runner runs all of them concurrently.
- **Fetch once**: `AnalysisContext` memoizes fetches (home shared by checks 4/5/6/8, robots.txt by 1/7).
- **SSRF in the fetcher**: resolve DNS on every hop and reject private, loopback, link-local, metadata, CGNAT, reserved and IPv6 equivalents (incl. IPv4-mapped); connect to the validated IP keeping Host/SNI (closes DNS rebinding); manual redirects (max 5, http/https only, ports 80/443); streamed body capped at 5 MB; connect/read/total timeouts.
- **Playwright + SSRF**: `page.route("**/*")` sends every browser request through SafeFetcher; images, fonts and media are blocked. Render failure or timeout -> check 5 `indeterminado`.
- **Own robots.txt parser** (RFC 9309: longest match, `*` and `$`, allow wins ties). Evidence records the matched group and the deciding rule.
- **Result fields** follow the spec (`id`, `status`, `titulo`, `explicacao`, `como_corrigir`, `evidencia`, `peso`); everything else in English.
- **Score**: `ok`=1, `aviso`=0.5, `erro`=0; `indeterminado` leaves the denominator. score = Σ(weight×value)/Σ(weight)×100.
- **Network-free tests**: FakeSite on `httpx.MockTransport` with an injectable resolver; Playwright tests serve fixtures through routing and are marked `@pytest.mark.browser` (skipped if Chromium is missing).
- **Deps**: httpx, pydantic, pyyaml, selectolax, defusedxml, playwright; dev: pytest, pytest-asyncio. CLI uses argparse.
- **Python**: `requires-python >=3.12`, nothing 3.13-only.

## Approved choices

1. Bands: >= 80 "Porta aberta", 50-79 "Entreaberta", < 50 "Fechada para a IA" (in config).
2. Check 2 (edge) gets a lower weight because it is only indicative; checks 1 and 5 get the highest.
3. Python 3.12.

## Status per check

| # | Check | ok | aviso | erro |
|---|---|---|---|---|
| 1 | robots x AI | search bots allowed | training blocked (legitimate, informational) or rules using retired tokens | any search/user bot blocked |
| 2 | Edge (CDN/WAF) | bots get the same as the browser | very different size | 403/503 or challenge only for bots |
| 3 | llms.txt | valid, links working | missing or incomplete structure | HTML, redirect to home, or wrong content-type |
| 4 | Meta robots | nothing restrictive | `noai`/`noimageai` | `noindex` on home |
| 5 | JS dependency | content in raw HTML | partial JS dependency | main content only after render |
| 6 | Structured data | valid JSON-LD with relevant type | missing | invalid JSON |
| 7 | Sitemap | found and reachable | not found | declared in robots but broken |
| 8 | Basics | HTTPS, fast, title/description/lang | something missing or slow | no HTTPS |

Check 2 always carries the "indicative" caveat; Cloudflare gets dashboard verification steps.

## Commits

1. [x] Deps, `models`, `config`, YAMLs (`ai_agents`, `scoring`)
2. [ ] `net/ssrf` + `net/fetcher` with SSRF tests
3. [ ] `checkers/base`, runner, scoring, CLI + check 8 (basics) end-to-end
4. [ ] robots parser + checks 1 and 7
5. [ ] checks 4 and 6 (HTML parsing)
6. [ ] check 3 (llms.txt)
7. [ ] check 2 (edge)
8. [ ] `net/render` + check 5 (Playwright)
9. [ ] CLI end-to-end test with a simulated site; README/ROADMAP update

## Done criteria

- `python -m raioxia analyze https://example.com` prints JSON with 8 results, score and band.
- `pytest` passes with no network access.
- SSRF tests cover internal IPs, redirect to internal IP, rebinding, forbidden port and the 5 MB cap.
