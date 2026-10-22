# Deploy — Raio-X IA on the Oracle VPS

Production: `https://raiox.primesolutions.net.br` on the Oracle VPS (163.176.179.23, ARM64, SSH user `ubuntu`), next to the production WordPress stack in `/home/ubuntu/wordpress`. That stack's Caddy (`wordpress_caddy`) owns ports 80/443 and the TLS certificates; it reaches our API as `raioxia-api:8000` over the Docker network `wordpress_default`. Raio-X IA publishes no ports of its own.

## Layout

```
/home/ubuntu/raioxia/                    700
  app/                                   git clone of the branch; the app is app/ia-web-f1
  .env                                   600  production settings (template: deploy/env.production.example)
  secrets/gmail-service-account.json     600  Gmail API key (domain-wide delegation, gmail.send only)
  backups/raioxia_*.dump                 600  daily pg_dump, newest 14
```

Services (`deploy/compose.yaml`, project `raioxia`): `api`, `worker-analysis`, `worker-email` (the only one with the Gmail key, plus `cli`), `postgres`, `redis`, the one-shot `migrate`, and `cli` for commands. One image (`deploy/Dockerfile`, Playwright 1.63 base, user `pwuser` = uid 1001 = `ubuntu`).

Everything goes through `deploy/deploy.sh` (alias it: `alias rx=~/raioxia/app/ia-web-f1/deploy/deploy.sh`).

## Update

```bash
rx deploy          # git pull, build, migrate, restart, wait for /healthz
rx ps
rx logs api        # or worker-analysis, worker-email
```

Rollback: `rx rollback <commit>` (builds and starts that commit; migrations only move forward, so roll back code, not the database, unless a migration is the problem).

## One-off commands

```bash
rx run stats --days 30
rx run purge                                   # also runs daily by timer
rx run email-test eric.lieb@primesolutions.net.br
```

## Scheduled jobs (systemd timers; cron is not installed on this server)

| Timer | When (Brasília) | Does |
|---|---|---|
| `raioxia-backup.timer` | 03:00 | `rx backup` |
| `raioxia-purge.timer` | 03:30 | `rx run purge` |

```bash
systemctl list-timers 'raioxia-*'
journalctl -u raioxia-purge.service -n 50
```

Units live in `deploy/systemd/`; install or update them with:

```bash
sudo cp ~/raioxia/app/ia-web-f1/deploy/systemd/raioxia-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now raioxia-backup.timer raioxia-purge.timer
```

## Restore a backup

```bash
rx ps                                           # stack running
docker compose --env-file ~/raioxia/.env -f ~/raioxia/app/ia-web-f1/deploy/compose.yaml stop api worker-analysis worker-email
docker exec -i raioxia-postgres-1 pg_restore -U raioxia -d raioxia --clean --if-exists < ~/raioxia/backups/raioxia_<stamp>.dump
rx deploy
```

## Caddy

The site block lives in `/home/ubuntu/wordpress/Caddyfile` (the WordPress project). Current block:

```
raiox.primesolutions.net.br {
    # Largest legit body is the newsletter archive PUT (~50 KB); anything bigger is refused with 413.
    request_body {
        max_size 1MB
    }
    encode zstd gzip
    header Strict-Transport-Security "max-age=31536000"
    reverse_proxy raioxia-api:8000
}
```

It is a single-file bind mount: edit it in place (never replace the file), keep a dated backup next to it, then:

```bash
docker exec wordpress_caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec wordpress_caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

## First install (done 2026-10-15; kept for a rebuild)

1. `mkdir -p ~/raioxia/secrets ~/raioxia/backups && chmod 700 ~/raioxia ~/raioxia/secrets`
2. `git clone --branch claude/ia-web-f1-setup-bg77ej https://github.com/elieb068/Agentic-Workflows.git ~/raioxia/app`
3. Put the Gmail key at `~/raioxia/secrets/gmail-service-account.json` (`chmod 600`).
4. Create `~/raioxia/.env` from `deploy/env.production.example` (`chmod 600`), generating `SECRET_KEY` and `POSTGRES_PASSWORD`.
5. `rx deploy`, then `rx run email-test ...`.
6. Point the Caddy block at `reverse_proxy raioxia-api:8000` (section above).
7. Install the timers (section above).

## Cloudflare

DNS `raiox` A -> 163.176.179.23, **DNS only (grey)**. If it is ever proxied (orange): SSL/TLS "Full (strict)"; tell Caddy to trust Cloudflare's ranges and read `CF-Connecting-IP` (global option, also affects WordPress); and turn **Email Address Obfuscation** off for this host, because its injected script is blocked by our CSP. Turnstile widget: hostname `raiox.primesolutions.net.br`, keys in `.env`.
