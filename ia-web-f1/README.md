# ia-web-f1 — Raio-X IA (Fase 1)

Web tool (PT-BR) that checks whether a site's "door is open" to AI crawlers. The user submits a URL, confirms by e-mail (double opt-in) and receives a report at `/r/{token}`.

Project definitions, scope and conventions live in [`CLAUDE.md`](CLAUDE.md). Planning lives in [`.planning/`](.planning/).

## Status

- **M1 — analysis core: done.** 8 checks, score, CLI, network-free tests.
- **M2 — pipeline: done.** FastAPI, PostgreSQL, RQ workers (analysis, e-mail), double opt-in, Turnstile, rate limits, Gmail API e-mail, LGPD (unsubscribe, deletion, retention), stats. Plan and decisions: [`.planning/M2-PLAN.md`](.planning/M2-PLAN.md).
- **M3 — front: done.** Landing with the form, report page (all states), privacy/terms, `/bot`, data deletion, own robots/sitemap/llms.txt/JSON-LD; Prime Solutions brand; WCAG 2.1 AA (axe) clean at 1280/390/360 px. Plan: [`.planning/M3-PLAN.md`](.planning/M3-PLAN.md).
- Next: **M4 — deploy** on the Oracle VPS.

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
cp .env.example .env        # then set SECRET_KEY (command in the file)
docker compose -f compose.dev.yaml up -d   # PostgreSQL :15432 + Redis :16379 (also creates raioxia_test)
python -m raioxia migrate
pytest                      # browser tests skip without Chromium, db tests skip without PostgreSQL
```

On Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env               # then set SECRET_KEY (command in the file)
docker compose -f compose.dev.yaml up -d
python -m raioxia migrate
pytest
python -m raioxia analyze exemplo.com.br --pretty
```

VS Code picks up `.venv` and the pytest settings from `.vscode/settings.json` (Testing panel).

Database tests (marked `db`) use the separate `raioxia_test` database of the dev PostgreSQL and rebuild its schema once per run; override with `TEST_DATABASE_URL`. New migrations: change `raioxia/db/models.py`, then `alembic revision --autogenerate -m "..."` with the dev database up, and review the generated file.

Check 5 and the browser tests need Chromium for Playwright: run `playwright install chromium`, or point `CHROMIUM_EXECUTABLE` at an existing binary. Tests never touch the network.

Playwright is pinned (`playwright==1.63.0`) to match the `mcr.microsoft.com/playwright/python:v1.63.0-noble` image used in production. Bump both together: with a mismatch the image's Chromium doesn't load, browser tests are skipped silently and check 5 returns `indeterminado`.

## Production

Runs on the Oracle VPS (ARM64) at `https://raiox.primesolutions.net.br`, next to an existing WordPress stack whose Caddy already owns ports 80/443. Raio-X IA is a separate Compose project without its own Caddy: the shared Caddy reaches it over a Docker network. Details land in `DEPLOY.md` (M4).

## E-mail (Gmail API)

E-mail is a separate function: the API and the analysis worker only write `email_outbox` rows; the `email` worker (`python -m raioxia worker email`) is the only process that sends. In development `EMAIL_SENDER=console` prints messages to the log.

Production sends through the Gmail API with a Google Workspace **service account** and **domain-wide delegation**, impersonating `eric.lieb@primesolutions.net.br` and sending as the alias `Raio-X IA <raiox@primesolutions.net.br>`. One-time setup:

1. **Google Cloud** ([console.cloud.google.com](https://console.cloud.google.com)), signed in with the Workspace admin: create (or pick) a project, then *APIs & Services → Library → Gmail API → Enable*.
2. *IAM & Admin → Service Accounts → Create service account* (e.g. `raiox-mailer`). It needs no IAM roles.
3. Open it → *Keys → Add key → Create new key → JSON*. Keep the file out of the repo and out of chats. If key creation is blocked, the organization policy `iam.disableServiceAccountKeyCreation` is on (default for newer Workspace orgs): override it for this project under *IAM & Admin → Organization Policies*.
4. Copy the service account's **Unique ID** (numeric client ID) from its details page.
5. **Workspace Admin** ([admin.google.com](https://admin.google.com)): *Security → Access and data control → API controls → Manage Domain Wide Delegation → Add new*, with that client ID and exactly one scope: `https://www.googleapis.com/auth/gmail.send`. Propagation usually takes minutes (up to 24h).
6. In Gmail for `eric.lieb@`, *Settings → Accounts → Send mail as* must list `raiox@primesolutions.net.br` (done), otherwise Gmail rewrites `From` to the primary address.
7. Server: store the key readable only by the app (e.g. `chmod 600`), then set `EMAIL_SENDER=gmail` and `GMAIL_SERVICE_ACCOUNT_FILE=/path/to/key.json`.
8. Check: `python -m raioxia email-test eric.lieb@primesolutions.net.br` sends one message directly (bypassing the queue) and prints the Gmail message id; confirm it arrives `From: Raio-X IA <raiox@primesolutions.net.br>`. `unauthorized_client` means step 5 is missing or not yet propagated.

The key can impersonate any user of the domain for the delegated scope (`gmail.send` only: it can send, not read). Treat it as a production secret; if it leaks, delete it under *Service Accounts → Keys* and access stops immediately.

The domain has SPF (`include:_spf.google.com`), DKIM (`google._domainkey`) and DMARC (`p=none`, reports to `eric.lieb@`) in Cloudflare DNS.

## Pipeline commands

```bash
python -m raioxia migrate                 # apply database migrations
uvicorn raioxia.api.app:create_app --factory --host 0.0.0.0 --port 8000
python -m raioxia worker analysis         # Playwright analyses (one process per job on Linux)
python -m raioxia worker email            # the only process that sends e-mail
python -m raioxia purge                   # daily: expiry, LGPD retention, stuck e-mails
python -m raioxia stats --days 30         # requests, confirmation and newsletter opt-in rates
python -m raioxia email-test <address>    # one direct test e-mail through the configured sender
```

## Front (M3)

Server-side Jinja2 pages in `raioxia/web/` (templates, `static/`, `styles/app.css`), styled with Tailwind v4. The brand tokens (colors, fonts) live in one `@theme` block in `raioxia/web/styles/app.css`; fonts and the logo are self-hosted, and the only third party is Cloudflare Turnstile (strict CSP).

```bash
python scripts/build_css.py           # rebuild raioxia/web/static/app.css (committed); --watch while editing
python scripts/a11y_audit.py [token]  # axe WCAG 2.1 AA + overflow audit at 1280/390/360 px (app running on :8000)
```

The Tailwind standalone CLI (pinned 4.3.3, no Node) and axe-core are downloaded into `.tools/` on first use. A test fails if `app.css` is stale. Templates may not use inline styles or scripts (a test scans them: the CSP would block them).

Owners who ask not to be analyzed go in `config/blocked_domains.yaml` (subdomains included). The `/bot` page shows `SERVER_IP` when set.

