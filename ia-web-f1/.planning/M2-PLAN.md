# M2 — Pipeline (approved 2026-10-14)

Goal: a submitted URL + e-mail becomes a confirmed lead, a queued job, a stored analysis and a report e-mail, with Turnstile, rate limits, double opt-in and LGPD consent records. M2 ships the JSON API and plain functional pages; the real front (landing, report page design) is M3.

## Flow

```
POST /api/analyses  -> Turnstile + rate limits -> lead + consents + analysis(pending_confirmation)
                    -> confirmation e-mail (link valid 24h)          [always the same 202 answer]
GET  /confirmar/{t} -> analysis confirmed -> reuse a <24h result for the domain, or enqueue RQ job
analysis worker     -> runner.analyze() -> check_results saved -> enqueue report e-mail -> done
e-mail worker       -> queue "email" -> Gmail API (service account) -> outbox row marked sent
GET  /r/{token}     -> report (JSON in M2 via /api/r/{token}; HTML page in M3)
```

## Folder structure (new)

```
raioxia/
  settings.py           env settings (pydantic-settings): DATABASE_URL, REDIS_URL, TURNSTILE_*, EMAIL_*, APP_BASE_URL, SECRET_KEY
  db/
    engine.py           SQLAlchemy 2 engine/session (sync, psycopg 3)
    models.py           leads, consents, analyses, check_results, email_outbox
  migrations/           Alembic
  api/
    app.py              FastAPI factory, JSON logging, /healthz
    routes.py           analyses, confirm, report, unsubscribe, data deletion
    client_ip.py        real client IP from X-Forwarded-For, trusted proxies only
    turnstile.py        siteverify (fixed Cloudflare host, not the SafeFetcher)
    ratelimit.py        Redis fixed-window counters
  jobs.py               RQ job: run analysis, save, enqueue report e-mail
  pipeline.py           domain logic shared by API and worker (create, confirm, reuse, finish)
  email/                separate sending function: nothing else talks to a mail provider
    outbox.py           queue_*_email(): store an outbox row (kind + context); dispatch() after commit; requeue_stale()
    jobs.py             send_email(outbox_id): the only code that sends; retries with backoff
    base.py             EmailSender protocol, MIME building, ConsoleSender (dev/tests)
    render.py           Jinja2 rendering; unsubscribe links signed at render time, never stored
    gmail.py            GmailApiSender: Workspace service account + domain-wide delegation, gmail.send scope
    templates/          Jinja2: confirm, report, failed (HTML + text), PT-BR
  tokens.py             token generation/hashing, signed unsubscribe links
  logs.py               JSON log formatter (not logging.py: it would read like the stdlib module)
config/
  limits.yaml           rate limit values (versioned, like scoring.yaml)
compose.dev.yaml        postgres 17 + redis 7 for local dev/tests on 127.0.0.1:15432/16379 (no app containers; those are M4)
```

## Data model

- **leads**: id, email (normalized, unique), created_at, confirmed_at, newsletter (bool, current state), unsubscribed_at.
- **consents** (append-only log): id, lead_id, kind (`report_terms` | `newsletter`), granted (bool), text_version, ip, user_agent, created_at. Withdrawals are new rows, never updates.
- **email_outbox**: id, lead_id, kind (`confirm` | `report` | `failed` | `deletion`), to_address, subject, status (`queued` | `sent` | `failed`), attempts, provider_message_id, last_error, created_at, sent_at. Bodies are rendered at send time from template + context, not stored. Context keys holding token links (`confirm_url`, `delete_url`) are removed once the message is sent, so the plaintext confirmation token lives only while the e-mail is pending.
- **analyses**: id, lead_id, url (normalized), domain, status (`pending_confirmation` | `queued` | `running` | `done` | `failed` | `expired`), confirm_token_hash, confirm_expires_at, report_token, requested_ip, newsletter_opt_in, reused_from_id, score, band, final_url, version, error_code, error_message, duration_ms, created_at, confirmed_at, finished_at.
- **check_results**: analysis_id, check_id, status, titulo, explicacao, como_corrigir, evidencia (jsonb), peso. Same field names as `CheckResult`.

## Design decisions

- **Reuse the M1 runner as is**: the job calls `runner.analyze()`; M2 only persists the `AnalysisReport`. No checker changes.
- **Sync SQLAlchemy + psycopg 3**: the RQ worker is sync anyway (the job wraps `asyncio.run(analyze(...))`), and FastAPI runs sync routes in its threadpool. Alembic for migrations.
- **Tokens**: `secrets.token_urlsafe(32)`. The confirmation token is stored only as a SHA-256 hash. The report token is stored as is, because the worker must put it in the e-mail later; it is 256-bit random, so not guessable.
- **No enumeration**: `POST /api/analyses` always answers 202 with the same body, whether the e-mail is new, known or rate-limited by e-mail. Rate limits by IP answer 429.
- **24h reuse**: on confirmation, if a `done` analysis for the same domain finished in the last 24h, copy its check results into the new analysis (own report token, `reused_from_id` set) and send the report e-mail straight away, without a job.
- **Job**: RQ `job_timeout=60`, `Retry(max=1, interval=30)` only for infrastructure errors; `AnalysisError` (invalid URL, DNS, blocked) is final -> status `failed` + a short "não conseguimos analisar" e-mail. Unconfirmed analyses expire after 24h.
- **Client IP**: Caddy is the only proxy; `X-Forwarded-For` is trusted only from the Docker network range. If the DNS record is later proxied by Cloudflare, Caddy maps `CF-Connecting-IP` (M4), so the app does not change.
- **Turnstile**: server-side siteverify with `remoteip`; Cloudflare's documented always-pass/always-fail test keys in dev and tests.
- **E-mail is a separate function** (user decision 2026-10-14): API and analysis worker only call `enqueue_email()`, which writes an `email_outbox` row and puts `send_email(outbox_id)` on its own RQ queue `email`, served by its own worker process. A mail outage never blocks or fails an analysis; each message retries on its own (3 attempts: 1 min, 5 min, 30 min) and its state is visible in the outbox.
- **Gmail API via Workspace service account** (user decision 2026-10-14): a Google Cloud service account with domain-wide delegation limited to `https://www.googleapis.com/auth/gmail.send`, impersonating `eric.lieb@primesolutions.net.br` and sending as its alias: `From: Raio-X IA <raiox@primesolutions.net.br>`, `Reply-To` the same alias. The alias must be listed in that mailbox's Gmail "Send mail as" addresses, otherwise Gmail silently rewrites `From` to the primary address; `gmail.send` cannot read that list, so setup includes a one-off check plus a test send. Domain-wide delegation lets the key impersonate any user of the domain for the delegated scope, so the scope stays `gmail.send` only and the key is treated as a production secret. Sent with `google-auth` + httpx to `users.messages.send` (raw MIME, base64url), no heavy Google client library. The JSON key is a file mounted read-only from outside the repo (`GMAIL_SERVICE_ACCOUNT_FILE`), never in `.env` or git. Workspace caps one user at about 2,000 messages/day, far above the rate limits; logged and alerted on quota errors.
- **Deliverability**: primesolutions.net.br already has Google MX and DKIM, but **no SPF and no DMARC**. Before go-live add `v=spf1 include:_spf.google.com ~all` and `v=DMARC1; p=none; rua=mailto:eric.lieb@primesolutions.net.br` in Cloudflare DNS (Gmail/Yahoo sender rules), then tighten DMARC later.
- **E-mail content**: `ConsoleSender` in dev/tests. Every e-mail carries an unsubscribe link and `List-Unsubscribe` / `List-Unsubscribe-Post` headers. Templates are plain and easy to swap (final look comes with M3).
- **Unsubscribe scope**: unsubscribing ends the newsletter; e-mails that answer the person's own new request (confirmation, report, failure, deletion) are still sent.
- **LGPD**: consent texts versioned (`text_version`), consents logged with IP and UA; `/descadastrar/{token}` withdraws the newsletter and stops e-mails; data deletion = request by e-mail -> confirmation link -> lead, consents, analyses and results deleted (cascade).
- **Observability**: JSON logs (stdlib formatter, no extra dep); `python -m raioxia stats` prints analyses per day, confirmation rate and newsletter opt-in rate from the DB.
- **Tests**: still no real sites. DB tests run against the `compose.dev.yaml` Postgres (skipped if unreachable, like the browser tests); Redis/RQ via `fakeredis` with synchronous jobs; HTTP via FastAPI `TestClient`; e-mail via `ConsoleSender` capture; Turnstile via a fake verifier.
- **Deps**: fastapi, uvicorn[standard], sqlalchemy>=2, psycopg[binary]>=3, alembic, redis, rq, jinja2, pydantic-settings, email-validator, google-auth; dev: fakeredis.

## Approved choices (2026-10-14)

1. **E-mail**: separate sending function via Gmail API (Workspace service account), mailbox `eric.lieb@primesolutions.net.br`, sent as alias `Raio-X IA <raiox@primesolutions.net.br>`.
2. **Rate limits** (`config/limits.yaml`): per IP 3/hour and 10/day; per e-mail 2/day; per analyzed domain 10/day (results are reused within 24h anyway).
3. **Retention**: unconfirmed leads and their analyses deleted after 7 days; reports kept 180 days, then anonymized (e-mail removed, results kept for stats). Runs as a daily `python -m raioxia purge` job.
4. **Failure e-mail**: yes, a short PT-BR e-mail when the site can't be analyzed.

## Commits

1. [x] Deps, `settings`, `compose.dev.yaml`, DB models + first Alembic migration, JSON logging, `migrate` command
2. [x] `pipeline` + `tokens`: create analysis, confirm, expire, 24h reuse (DB tests); `analyses.newsletter_opt_in` (double opt-in for the newsletter)
3. [x] E-mail function: outbox table, `queue_*_email` + `dispatch`, `send_email` job on queue `email`, ConsoleSender, Jinja2 templates (confirm, report, failed, deletion)
4. [x] Analysis RQ job + worker entry points (`python -m raioxia worker analysis|email`), retry/timeout, persistence of the report (forking Worker on Linux; SimpleWorker + timer timeouts on Windows for dev)
5. [x] FastAPI app: `POST /api/analyses`, `/confirmar/{t}`, `/api/r/{token}`, provisional `/r/{token}` page, `/healthz`; client IP (moved up from 6); internal IP literals refused at submit; security headers (no-referrer, noindex on token paths). Verified live on Windows: submit -> confirm -> real analysis -> report e-mail.
6. [x] Turnstile (fails closed) + rate limits (Redis fixed windows, hashed keys; ip -> email (silent) -> domain)
7. [x] LGPD: unsubscribe (GET page + POST/one-click), data deletion (request -> signed 24h link -> page -> POST), consent log, retention `purge` (also re-dispatches stuck e-mails; run daily from M4); `stats` command
8. [x] `GmailApiSender` (service account, gmail.send; token refresh over an httpx transport adapter, no `requests`), `email-test` command, setup steps in README
9. [ ] End-to-end test (submit -> confirm -> job -> report e-mail -> `/api/r/{token}`); README/ROADMAP update

## Done criteria

- With `compose.dev.yaml` up, a local run takes a request from submission to a report e-mail (console) and a JSON report at `/api/r/{token}`.
- `pytest` passes with no access to real sites; DB tests run when Postgres is up.
- Rate limits, Turnstile failure, expired/reused confirmation links and the 24h reuse path are covered by tests.
- Consents are recorded with IP, UA and text version; unsubscribe and data deletion work end to end.
