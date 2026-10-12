# ia-web-f1 — Raio-X IA (Phase 1)

## What We're Building

A PT-BR web tool: the user submits their site URL and receives by e-mail a report on the site's "AI visibility". Phase 1 ("Sua porta está aberta para a IA?") checks AI crawler access, llms.txt and technical readability, with no LLM calls (near-zero cost per report).

Revenue: ads on the site, newsletter (sponsorship/affiliates) and, later, a paid monitoring/consulting plan.

Full definitions: [`../CLAUDE.md`](../CLAUDE.md).

## Current State (as of 2026-10-12)

Project scaffold created (package skeleton, pyproject, .env.example, smoke test). No checkers implemented yet.

## Stack

Python 3.12 + FastAPI · Redis + RQ · PostgreSQL · httpx + Playwright Chromium · Jinja2 + Tailwind · Resend/SES · Docker Compose + Caddy on own VPS.

## Key Constraints

- SSRF protection is critical (DNS resolution + private IP block, revalidate per redirect, max 5 redirects, http/https on 80/443 only, 5 MB cap, timeouts).
- Rate limit per IP, e-mail and domain; reuse a domain's result within 24h.
- LGPD: separate, recorded consents; unsubscribe in every e-mail; data deletion endpoint.
- Tests use simulated-site fixtures only.
- User-facing text in PT-BR; code and commits in English.
