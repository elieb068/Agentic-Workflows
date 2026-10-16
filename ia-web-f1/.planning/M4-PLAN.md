# M4 — Deploy (DRAFT, awaiting approval)

Goal: Raio-X IA live at `https://raiox.primesolutions.net.br` on the Oracle VPS, next to the production WordPress stack, with workers, database, backups and the daily retention job running, and a real end-to-end request (form -> confirmation e-mail -> report e-mail) passing.

## Server facts (checked 2026-10-15)

- Oracle VPS 163.176.179.23, **aarch64**, 4 cores, 23 GB RAM (1.3 GB used), 182 GB free disk; Docker 29 + Compose v5; time zone UTC.
- WordPress stack (`/home/ubuntu/wordpress`): `wordpress_caddy` owns 80/443 and sits on the Docker network `wordpress_default` (172.18.0.0/16, Caddy at 172.18.0.3). The `raiox.primesolutions.net.br` placeholder block and its certificate are already live there.
- **cron is not installed**: scheduled jobs use **systemd timers** (built in, logs in journald).
- `ubuntu` is uid/gid 1001, the same uid as `pwuser` in the Playwright image, so containers can read the 600 Gmail key without loosening it.
- Gmail key: `/home/ubuntu/raioxia/secrets/gmail-service-account.json` (600, dir 700); domain-wide delegation verified (test e-mail delivered From `Raio-X IA <raiox@primesolutions.net.br>`).
- `/home/ubuntu/backups/` exists (WordPress dumps).

## Layout on the server

```
/home/ubuntu/raioxia/            (700)
  app/                           git checkout of the branch (only ia-web-f1/ is used)
  secrets/gmail-service-account.json
  .env                           (600) production settings, never in git
  backups/                       daily pg_dump, 14 kept
```

## Compose project `raioxia` (in the repo: `deploy/compose.yaml`)

| Service | Image | Notes |
|---|---|---|
| `api` | app image | `uvicorn raioxia.api.app:create_app --factory`, 2 workers; joins `wordpress_default` with alias `raioxia-api` so Caddy reaches it; no host ports; healthcheck `/healthz` |
| `worker-analysis` | app image | `python -m raioxia worker analysis` (forks one process per job); `shm_size: 1gb`, `init: true`, memory limit 2 GB |
| `worker-email` | app image | `python -m raioxia worker email`; the only service that mounts the Gmail key (read-only) |
| `postgres` | `postgres:17-alpine` | named volume, internal network only, healthcheck |
| `redis` | `redis:7-alpine` | AOF persistence (queued jobs survive restarts), internal network only |
| `migrate` / `cli` | app image | one-shot: `migrate` before `api` starts; `purge`, `stats`, `email-test` via `docker compose run --rm cli ...` |

- **App image** (`deploy/Dockerfile`): `mcr.microsoft.com/playwright/python:v1.63.0-noble` (arm64, matching the pinned Playwright), runs as `pwuser` (1001), installs the package, no build step for CSS (committed). Built on the server (native ARM).
- **Networks**: `internal` (postgres, redis, api, workers) + external `wordpress_default` for `api` only. Postgres and Redis are never published.
- **Logs**: JSON lines to Docker's json-file driver with rotation (10 MB x 5 per service).
- Everything `restart: unless-stopped`.

## Settings (`/home/ubuntu/raioxia/.env`)

`APP_BASE_URL=https://raiox.primesolutions.net.br`, a generated `SECRET_KEY` and Postgres password, `DATABASE_URL`/`REDIS_URL` to the internal services, `TRUSTED_PROXIES=["172.18.0.0/16"]` (Caddy's network), `EMAIL_SENDER=gmail`, `GMAIL_SERVICE_ACCOUNT_FILE=/run/secrets/gmail-service-account.json`, real Turnstile keys, `SERVER_IP=163.176.179.23`, `FETCHER_USER_AGENT` with the production `/bot` URL, `LOG_JSON=true`.

## Caddy

Replace the placeholder block in `/home/ubuntu/wordpress/Caddyfile` (backup first, edit in place: single-file bind mount) with:

```
raiox.primesolutions.net.br {
    encode zstd gzip
    reverse_proxy raioxia-api:8000
}
```

then `caddy validate` + `caddy reload` (no WordPress downtime). Caddy already sends `X-Forwarded-For`, which the app trusts only from 172.18.0.0/16.

## Scheduled jobs (systemd timers, `deploy/systemd/`)

- `raioxia-purge.timer`: daily 03:30 America/Sao_Paulo -> `docker compose run --rm cli purge` (expiry, LGPD retention, stuck e-mails).
- `raioxia-backup.timer`: daily 03:00 -> `pg_dump` (custom format) to `/home/ubuntu/raioxia/backups/`, keep 14; restore steps in `DEPLOY.md`.

## Deploy and update

`deploy/deploy.sh` on the server: `git pull` -> `docker compose build` -> `run --rm migrate` -> `up -d` -> wait for `/healthz`. `DEPLOY.md` documents first install, updates, rollback (previous commit + image tag), restore from backup, logs, and the stats/email-test commands.

## Go-live checks

1. `email-test` from the `cli` container (key mounted, From alias).
2. Turnstile renders on the live form; a wrong token is refused.
3. Real request with eric.lieb@: form -> confirmation e-mail -> click -> report page -> report e-mail; then unsubscribe and data deletion.
4. Our own site scores 100 when analyzed by the live service.
5. Headers: CSP, HSTS (from Caddy), no-referrer; `/robots.txt`, `/sitemap.xml`, `/llms.txt`.
6. WordPress still answers 200 throughout.

## Decisions needed

1. **Turnstile keys**: create a widget in the Cloudflare dashboard (Turnstile -> Add widget, hostname `raiox.primesolutions.net.br`, mode Managed) and send me the site key and secret; the secret goes only into the server's `.env`.
2. **Cloudflare proxy (orange cloud)**: proposal: stay **DNS-only (grey)** for launch (real client IPs, no extra config). If switched to orange later: SSL Full (strict), Caddy `trusted_proxies` for Cloudflare's ranges + `client_ip_headers CF-Connecting-IP` (a global Caddy option that also affects WordPress logs), and **Email Address Obfuscation off** for this host (its injected script is blocked by our CSP).
3. **Off-site backup** (proposal): local daily dumps now; copying them to TrueNAS can come later.
4. **Uptime monitor** (proposal): an UptimeRobot HTTP monitor on `https://raiox.primesolutions.net.br/healthz`.

## Commits

1. [ ] `deploy/Dockerfile`, `deploy/compose.yaml`, `.dockerignore`; image builds and runs the test suite on arm64
2. [ ] `deploy/deploy.sh`, systemd units (purge, backup), `DEPLOY.md`, `.env` template for production
3. [ ] Server: checkout, `.env`, build, migrate, start; Caddy switched from placeholder to the app (backup + reload)
4. [ ] Timers enabled; go-live checks; README/ROADMAP; Phase 1 done criteria
