# ia-web-f1 — Raio-X IA (Fase 1)

Web tool (PT-BR) that checks whether a site's "door is open" to AI crawlers. The user submits a URL, confirms by e-mail (double opt-in) and receives a report at `/r/{token}`.

Project definitions, scope and conventions live in [`CLAUDE.md`](CLAUDE.md). Planning lives in [`.planning/`](.planning/).

## Status

- **M1 — analysis core: done.** 8 checks, score, CLI, network-free tests.
- Next: **M2 — pipeline** (API, queue, worker, database, double opt-in, e-mails).

## Usage (CLI)

```bash
python -m raioxia analyze exemplo.com.br --pretty
python -m raioxia analyze https://exemplo.com.br --no-render        # skip Playwright (check 5 = indeterminado)
python -m raioxia analyze exemplo.com.br --only robots_ai,llms_txt  # run a subset
```

Prints an `AnalysisReport` as JSON: `url`, `final_url`, `score` (0-100), `band` (`Porta aberta` / `Entreaberta` / `Fechada para a IA`), `checks`, `analyzed_at`, `duration_ms`, `version`. Each check has the spec fields: `id`, `status` (`ok` | `aviso` | `erro` | `indeterminado`), `titulo`, `explicacao`, `como_corrigir`, `evidencia`, `peso`.

Invalid, internal or unknown addresses exit with code 2 and a PT-BR message as JSON on stderr.

## The 8 checks

| # | id | What it checks |
|---|---|---|
| 1 | `robots_ai` | robots.txt (RFC 9309) per AI crawler, search/user vs training, retired tokens |
| 2 | `edge_block` | CDN/WAF treating crawler UAs differently from a browser (indicative) |
| 3 | `llms_txt` | `/llms.txt` and `/llms-full.txt`: soft 404s, redirects to home, content type, structure, sample links |
| 4 | `meta_robots` | `noindex`, `nofollow`, `noai`, `noimageai`, `nosnippet` in meta tags and `X-Robots-Tag` |
| 5 | `js_dependency` | raw HTML text vs Playwright-rendered text |
| 6 | `structured_data` | JSON-LD validity and relevant types (Organization, LocalBusiness, Product, FAQPage) |
| 7 | `sitemap` | sitemap declared in robots.txt or at usual addresses, XML/gzip/text |
| 8 | `basics` | HTTPS, response time, `<title>`, meta description, `lang` |

Score = Σ(weight × status value) / Σ(weight) × 100, with `ok`=1, `aviso`=0.5, `erro`=0 and `indeterminado` left out. Weights and bands live in [`config/scoring.yaml`](config/scoring.yaml); the AI crawler list in [`config/ai_agents.yaml`](config/ai_agents.yaml). Blocking only training crawlers is reported but never penalized.

## Security

All network access goes through `raioxia/net/fetcher.py` (`SafeFetcher`):

- http/https on ports 80/443 only, no credentials in URLs;
- DNS resolved per hop; any private, loopback, link-local, metadata, CGNAT, reserved or multicast answer (including IPv6 forms that embed IPv4) blocks the request;
- the connection is pinned to the validated IP (Host and TLS SNI keep the hostname), which closes DNS rebinding;
- max 5 redirects, each revalidated; 5 MB body cap; connect/read/total timeouts; no cookies; environment proxies ignored.

Playwright (`raioxia/net/render.py`) never touches the network itself: every request is routed through the `SafeFetcher`, Chromium's DNS is disabled, its proxy points to a dead port and WebSockets are refused.

## Layout

```
config/             ai_agents.yaml, scoring.yaml (versioned)
raioxia/
  cli.py, runner.py, scoring.py, models.py, config.py
  net/              ssrf.py, fetcher.py, render.py
  parsing/          robots.py, llms_txt.py, html.py
  checkers/         base.py + one module per check (registered in __init__.py)
tests/              pytest; FakeWeb/FakeResolver doubles, simulated sites, fixtures/
.planning/          project, roadmap and milestone plans
```

To add a check (phases 2-4): subclass `Checker`, decorate with `@register`, import it in `raioxia/checkers/__init__.py` and give it a weight in `config/scoring.yaml`.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest                      # browser tests are skipped if Chromium is unavailable
```

Check 5 and the browser tests need Chromium for Playwright: run `playwright install chromium`, or point `CHROMIUM_EXECUTABLE` at an existing binary. Tests never touch the network.
