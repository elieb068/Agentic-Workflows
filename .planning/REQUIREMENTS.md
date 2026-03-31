# Requirements — elogieaki API

## Milestone 1: Feature Expansion + Operational Stability

### R1 — Company CRUD Completion

**R1.1 — Update Company (Admin only)**
- `PATCH /api/v1/companies/:id`
- Requires JWT + admin role check
- Updatable fields: `nome`, `cidade`, `estado`, `website`, `telefone`, `email`, `logotipo`, `categoria`
- Auto-update `_updated` timestamp
- Returns updated document

**R1.2 — Delete Company (Admin only)**
- `DELETE /api/v1/companies/:id`
- Requires JWT + admin role check
- Soft or hard delete (TBD during phase)
- Returns confirmation

**R1.3 — Admin role mechanism**
- Define how "admin" is identified (e.g. `nivel_acesso` field on user or separate flag)
- Reusable `adminOK` middleware in ea-api

### R2 — Rate Limiting

**R2.1 — Global rate limit**
- Apply to all ea-api and ea-auth endpoints
- Suggested: 100 req/min per IP

**R2.2 — Stricter limits on auth endpoints**
- Login/signup: 10 req/min per IP (brute-force protection)

**R2.3 — Stricter limits on comment submission**
- Already has per-user/per-day DB check; add IP-based rate limit as extra layer
- Suggested: 5 submissions/min per IP

### R3 — Swagger/OpenAPI Documentation

**R3.1 — Auto-generated interactive docs**
- Available at `GET /api-docs` on ea-api (port 3750)
- Covers all current + new endpoints
- Includes request body schemas, response examples, auth requirements

**R3.2 — ea-auth documented separately or unified**
- At minimum, document auth endpoints in the same Swagger UI

### R4 — Monitoring & Alerts

**R4.1 — Health check endpoints**
- Both services already have `GET /api/v1/status`
- Set up external monitoring (e.g. cron + curl, or UptimeRobot) to ping these

**R4.2 — pm2 crash alerting**
- Configure pm2 to send alert when a service restarts unexpectedly
- Email or webhook notification

**R4.3 — Log rotation**
- Ensure pm2 logs don't grow unbounded (pm2-logrotate module)

### R5 — Validation & Schema Hardening (ea-api)

**R5.1 — Joi schema coverage**
- Ensure all POST/PATCH endpoints have Joi schemas (currently some use only express-validator)
- Consistent error response format across both validators

**R5.2 — MongoDB index review**
- Ensure CNPJ has a unique index in `empresas` collection
- Ensure email has an index in `usuarios` collection

## Out of Scope (This Milestone)

- Token refresh / auth improvements
- Search & full-text filtering
- Self-service company management by collaborators
- Automated test suite
- Frontend changes
