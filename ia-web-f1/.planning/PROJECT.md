# ia-web-f1 — Raio-X IA (Phase 1)

## What We're Building

A PT-BR web tool: the user submits their site URL and receives by e-mail a report on the site's "AI visibility". Phase 1 ("Sua porta está aberta para a IA?") checks AI crawler access, llms.txt and technical readability, with no LLM calls (near-zero cost per report).

Revenue: ads on the site, newsletter (sponsorship/affiliates) and, later, a paid monitoring/consulting plan.

Full definitions: [`../CLAUDE.md`](../CLAUDE.md).

## Current State (as of 2026-10-15)

M3 (front) is done: landing with the HTML form, the report page in every state with the newsletter one-click and "quero ajuda" e-mail, privacy policy and terms (controller Prime Solutions Tecnologia e Serviços de Apoio Administrativo, CNPJ 43.757.807/0001-86), `/bot`, data deletion, and our own robots/sitemap/llms.txt/JSON-LD (our landing scores 100 on our own checks). Prime Solutions brand from an approved Claude Design canvas. Next: M4 (deploy).

M2 (pipeline) is done: submit -> double opt-in e-mail -> queued analysis -> stored report -> report e-mail, with Turnstile, rate limits, LGPD consent log, unsubscribe, data deletion and retention purge. E-mail is a separate function (outbox + own worker) sending through the Gmail API (Workspace service account; Google-side setup in the README still to do). Verified live on Windows with the dev PostgreSQL/Redis.

M1 (analysis core) is done: the 8 checks, weighted score with bands, SSRF-safe fetcher and Playwright renderer, and the CLI `python -m raioxia analyze <url>`. The test suite runs without network (browser tests skip when Chromium is missing).

Decisions taken during M1:
- Blocking only AI training crawlers (robots.txt, meta tags or CDN) is reported but not penalized (option B, 2026-10-13).
- `nosnippet` / `max-snippet:0` are flagged in check 4: Google uses them to keep pages out of AI Overviews.
- Composite checks (e.g. basics) take the worst status of their parts.

Decisions taken during M2: see `M2-PLAN.md` (approved choices, deviations and outcome).

Next: M3 (front), then M4 (deploy on the Oracle VPS).

## Stack

Python 3.12 + FastAPI · Redis + RQ · PostgreSQL · httpx + Playwright Chromium (pinned 1.63.0, matches the Docker image) · Jinja2 + Tailwind · Gmail API (Workspace service account, separate e-mail queue/worker) · Docker Compose.

**Hosting (decided 2026-10-14):** Oracle VPS (ARM64: 4 cores, 23 GB RAM), domain `raiox.primesolutions.net.br` (Cloudflare DNS). The VPS already runs a production WordPress stack whose Caddy owns 80/443; Raio-X IA is a separate Compose project reached by that shared Caddy over a Docker network, with no host ports of its own. A placeholder site block and its Let's Encrypt certificate are already live. Verified on the VPS: test suite passes and a real analysis runs on ARM64.

## Key Constraints

- SSRF protection is critical (DNS resolution + private IP block, revalidate per redirect, max 5 redirects, http/https on 80/443 only, 5 MB cap, timeouts).
- Rate limit per IP, e-mail and domain; reuse a domain's result within 24h.
- LGPD: separate, recorded consents; unsubscribe in every e-mail; data deletion endpoint.
- Tests use simulated-site fixtures only.
- User-facing text in PT-BR; code and commits in English.
