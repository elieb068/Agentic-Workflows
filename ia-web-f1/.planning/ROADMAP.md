# ia-web-f1 — Roadmap (Phase 1)

**Granularity**: Coarse (4 milestones, one at a time, small commits)

## Milestones

- [x] **M0: Scaffold** — Project directory, CLAUDE.md, package skeleton
- [x] **M1: Analysis core** (done 2026-10-13, plan: `M1-PLAN.md`) — `raioxia/checkers/` with the 8 checks, `config/ai_agents.yaml`, weights config, score 0–100 with bands, SSRF-safe fetcher, CLI `python -m raioxia analyze <url>` printing JSON, tests with simulated sites
- [x] **M2: Pipeline** (done 2026-10-14, plan: `M2-PLAN.md`) — FastAPI, RQ analysis + e-mail workers (60s timeout, limited retry), PostgreSQL (`leads`, `consents`, `analyses`, `check_results`, `email_outbox`), Turnstile, rate limits, double opt-in, e-mail as a separate function via Gmail API, LGPD unsubscribe/deletion/retention, stats
- [x] **M3: Front** (done 2026-10-15, plan: `M3-PLAN.md`) — Landing, confirmation page, web report `/r/{token}`, privacy/terms, `/bot` page, ad placeholders, newsletter and consulting CTAs
- [ ] **M4: Deploy** — Docker Compose (api, worker, redis, postgres) on the Oracle VPS (ARM64), routed by the shared `wordpress_caddy` at `raiox.primesolutions.net.br` (no Caddy of our own), `DEPLOY.md`

## Done Criteria (Phase 1)

A real user submits a URL, confirms the e-mail and receives the report in under 3 minutes, with SSRF protection and rate limiting active and tested.
