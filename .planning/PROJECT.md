# elogieaki API

## What We're Building

A production-grade REST API backend for **elogieaki.com.br** — a Brazilian company review and rating platform where users post reviews of companies, nominate standout employees ("medalhistas"), and like/engage with others' reviews.

The backend consists of two Node.js (ESM) microservices running behind nginx on a single VPS:
- **ea-auth** (port 3740) — authentication, JWT issuance, password management
- **ea-api** (port 3750) — companies, users, comments, likes, homepage data

Both services use MongoDB as the database and are managed by pm2.

## Current State (as of 2026-03-31)

Both services were **down** (not running, no pm2). Discovered and fixed this session:
- Installed pm2, started both services, configured systemd startup for reboot persistence
- Added `POST /api/v1/companies` endpoint (company creation) with full schema, Joi validation, CNPJ duplicate check, auto-slug generation, and auto-set metadata fields

### Working Endpoints
**ea-auth** (port 3740):
- POST /api/v1/signup
- POST /api/v1/login → returns JWT + userID
- PATCH /api/v1/updpass (authenticated)
- PATCH /api/v1/unkpass (forgot password)
- GET /api/v1/status

**ea-api** (port 3750):
- GET /api/v1/latest — homepage latest comments
- GET /api/v1/ranking — homepage company ranking
- GET /api/v1/status
- GET /api/v1/companies/lookup
- POST /api/v1/companies ← added this session
- GET /api/v1/companies/:id
- GET /api/v1/companies/:id/colabs
- GET /api/v1/companies/:id/comments (paginated)
- POST /api/v1/companies/:id/comments (spam-protected)
- GET /api/v1/users/:id
- GET /api/v1/users/:id/stats
- GET /api/v1/users/:id/comments (paginated)
- PATCH /api/v1/users/:id
- POST /api/v1/comments
- PATCH /api/v1/comments/:id/toggle (like/unlike)

## Tech Stack

| Layer | Tech |
|-------|------|
| Runtime | Node.js v18 (ESM modules) |
| Framework | Express |
| Database | MongoDB (mongoose) |
| Auth | JWT (jsonwebtoken), bcrypt |
| Validation | Joi (body) + express-validator (params) |
| Process manager | pm2 |
| Web server | nginx (reverse proxy) |
| Server OS | Debian, hosted VPS at 200.234.207.39 |

## Server Layout

```
/opt/servers/
  ea-auth/          # Auth microservice
    routes/auth.js
    models/User.js
    middleware/authOK.js, reqOK.js
    validation/schemas.js
  ea-api/           # Main API microservice
    routes/companies.js, users.js, comments.js, root.js
    models/companies.js, companies-v.js, users.js, comments-c.js, comments-v.js, ...
    middleware/authOK.js, reqOK.js
    validation/schemas.js, valparams.js
/www/wwwroot/elogieaki.com.br/   # Laravel frontend
```

## Project Goals

**Feature expansion** — build on the working foundation to complete CRUD operations, add operational stability, and make the API developer-friendly.

## Key Decisions

- Auth: Keep current JWT as-is (no refresh tokens for now)
- Company update/delete: Admin only (not self-service)
- API docs: Swagger/OpenAPI interactive docs
- Rate limiting: Protect public + auth endpoints from abuse
- Monitoring: pm2 crash alerts + health check monitoring
