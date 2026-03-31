# elogieaki API — Project Roadmap

**Project**: elogieaki.com.br — Brazilian company review platform
**Stack**: Node.js v18 ESM + Express + MongoDB + JWT + pm2
**Services**: ea-auth (port 3740), ea-api (port 3750) on VPS 200.234.207.39
**Granularity**: Coarse (3 phases)

---

## Phases

- [x] **Phase 0: Current State** — Both services running; core endpoints operational (completed this session)
- [ ] **Phase 1: Security & Data Integrity** — Admin controls, rate limiting, schema hardening
- [ ] **Phase 2: Developer Experience** — Swagger/OpenAPI documentation
- [ ] **Phase 3: Operations** — Monitoring, alerting, log management

---

## Phase Details

### Phase 1: Security & Data Integrity

**Goal**: The API enforces access control, prevents abuse, and rejects malformed data before it reaches the database.

**Depends on**: Phase 0 (services running)

**Requirements**: R1.1, R1.2, R1.3, R2.1, R2.2, R2.3, R5.1, R5.2

**Plans**:
- 1.1 Admin middleware (adminOK) — Define admin by `nivel_acesso` flag on the user document; export reusable `adminOK` middleware in ea-api
- 1.2 Company PATCH endpoint — `PATCH /api/v1/companies/:id` protected by `authOK + adminOK`; accepts partial body [PARALLEL with 1.3]
- 1.3 Company DELETE endpoint — `DELETE /api/v1/companies/:id` protected by `authOK + adminOK`; hard delete or soft-delete flag [PARALLEL with 1.2]
- 1.4 MongoDB unique indexes — Add unique index on `empresas.cnpj` and `usuarios.email`; write migration script to drop duplicates first [PARALLEL with 1.2, 1.3]
- 1.5 Joi schema coverage — Audit all POST/PATCH routes; add or complete Joi schemas for companies, users, comments
- 1.6 Rate limiting — Apply `express-rate-limit`: global 100/min, auth routes 10/min, comment submission 5/min

**Success Criteria** (what must be TRUE):
1. A non-admin authenticated user receives 403 when calling PATCH or DELETE on a company
2. An admin user can successfully update a company's fields via PATCH and delete a company via DELETE
3. Sending 11 login requests within 60 seconds from the same IP returns HTTP 429 on the 11th
4. Posting a comment 6 times within 60 seconds from the same IP returns HTTP 429 on the 6th
5. Submitting a POST /companies body missing a required field returns a structured 400 validation error
6. Attempting to insert a duplicate CNPJ returns a MongoDB duplicate key error (not a 500)

**Plans**: 1.1 → (1.2 ‖ 1.3 ‖ 1.4) → 1.5 → 1.6

---

### Phase 2: Developer Experience

**Goal**: Every API endpoint is discoverable, self-documented, and testable from a browser without any external tool.

**Depends on**: Phase 1 (all endpoints finalized, schemas stable)

**Requirements**: R3.1, R3.2

**Plans**:
- 2.1 Install swagger-ui-express + swagger-jsdoc — Add dependencies to ea-api; configure `/api-docs` route
- 2.2 Document ea-api endpoints — Write OpenAPI 3.0 JSDoc annotations for companies, users, comments, homepage routes
- 2.3 Document ea-auth endpoints — Write OpenAPI 3.0 JSDoc annotations for signup, login, updpass, unkpass, status; either expose from ea-auth or proxy into ea-api docs [PARALLEL with 2.2]
- 2.4 Schema definitions — Define reusable `$ref` components for Company, User, Comment, Error response objects

**Success Criteria** (what must be TRUE):
1. `GET /api-docs` returns an interactive Swagger UI page in the browser
2. Every endpoint listed in phases 0–1 appears in the docs with its method, path, and expected request body
3. A developer can execute a live `POST /auth/login` call from the Swagger UI and receive a JWT in the response
4. All documented request bodies reference named schema components (no inline-only schemas)

**Plans**: 2.1 → 2.4 → (2.2 ‖ 2.3)

---

### Phase 3: Operations

**Goal**: Service failures are detected automatically, the team is notified, and logs never fill the disk.

**Depends on**: Phase 1 (stable, rate-limited services worth monitoring)

**Requirements**: R4.1, R4.2, R4.3

**Plans**:
- 3.1 pm2-logrotate — Install and configure `pm2-logrotate` module; set max size 10M, retain 7 files, daily rotation [PARALLEL with 3.2]
- 3.2 pm2 crash alerting — Configure pm2 `post_update` / `error` hooks or use `pm2-slack` / `pm2-webhook` module to fire on crash events [PARALLEL with 3.1]
- 3.3 External health check — Register ea-api and ea-auth health endpoints with an external uptime monitor (UptimeRobot free tier or equivalent); set alert recipient

**Success Criteria** (what must be TRUE):
1. After `pm2 kill && pm2 start ecosystem.config.js`, logs for both services rotate and old files are compressed under `/opt/servers/*/logs/`
2. Manually stopping ea-api via `pm2 stop ea-api` triggers a crash notification to the configured channel/email within 2 minutes
3. The external monitor shows ea-api and ea-auth as "Up" and sends an alert email when either service is manually taken offline for 3+ minutes
4. Log directory size remains bounded after 30 days of normal operation (no unbounded growth)

**Plans**: (3.1 ‖ 3.2) → 3.3

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0 - Current State | — | Done | 2026-03-30 |
| 1 - Security & Data Integrity | 0/6 | Not started | - |
| 2 - Developer Experience | 0/4 | Not started | - |
| 3 - Operations | 0/3 | Not started | - |

---

## Requirement Coverage

| Requirement | Phase | Status |
|-------------|-------|--------|
| R1.1 PATCH /companies/:id (admin) | Phase 1 | Pending |
| R1.2 DELETE /companies/:id (admin) | Phase 1 | Pending |
| R1.3 Admin middleware (adminOK) | Phase 1 | Pending |
| R2.1 Global rate limit 100/min | Phase 1 | Pending |
| R2.2 Auth rate limit 10/min | Phase 1 | Pending |
| R2.3 Comment rate limit 5/min | Phase 1 | Pending |
| R3.1 Swagger UI at /api-docs | Phase 2 | Pending |
| R3.2 Cover all endpoints in docs | Phase 2 | Pending |
| R4.1 External health check monitoring | Phase 3 | Pending |
| R4.2 pm2 crash alerting | Phase 3 | Pending |
| R4.3 Log rotation via pm2-logrotate | Phase 3 | Pending |
| R5.1 Joi schema coverage POST/PATCH | Phase 1 | Pending |
| R5.2 MongoDB unique indexes CNPJ + email | Phase 1 | Pending |

**Coverage: 13/13 requirements mapped.**
