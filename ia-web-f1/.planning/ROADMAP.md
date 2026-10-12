# ia-web-f1 — Roadmap (Phase 1)

**Granularity**: Coarse (4 milestones, one at a time, small commits)

## Milestones

- [x] **M0: Scaffold** — Project directory, CLAUDE.md, package skeleton
- [ ] **M1: Analysis core** — `raioxia/checkers/` with the 8 checks, `config/ai_agents.yaml`, weights config, score 0–100 with bands, SSRF-safe fetcher, CLI `python -m raioxia analyze <url>` printing JSON, tests with simulated sites
- [ ] **M2: Pipeline** — FastAPI, RQ queue + worker (60s timeout, limited retry), PostgreSQL (`leads`, `consents`, `analyses`, `check_results`), Turnstile, rate limits, double opt-in, e-mail provider abstraction
- [ ] **M3: Front** — Landing, confirmation page, web report `/r/{token}`, privacy/terms, `/bot` page, ad placeholders, newsletter and consulting CTAs
- [ ] **M4: Deploy** — Docker Compose (api, worker, redis, postgres, caddy), Caddy TLS, `DEPLOY.md`

## Done Criteria (Phase 1)

A real user submits a URL, confirms the e-mail and receives the report in under 2 minutes, with SSRF protection and rate limiting active and tested.
