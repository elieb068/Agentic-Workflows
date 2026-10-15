# M3 — Front (approved 2026-10-15)

Goal: the public site in PT-BR. A landing page with the form, the "check your e-mail" page, the real report at `/r/{token}`, privacy and terms, the `/bot` page and the data-deletion page. Server-side HTML (Jinja2) + Tailwind, no SPA, fast and good for SEO. The pages replace M2's provisional ones; the JSON API stays.

**Design first** (user decision 2026-10-15): the screens are designed in Claude Design with the Prime Solutions brand and approved before any page is built. Templates still sit on a small set of design tokens, so later visual changes stay in one place.

## Brand (from www.primesolutions.net.br, 2026-10-15)

- **Logo**: `https://www.primesolutions.net.br/logo.png` (teal sphere + "prime solutions"); on dark backgrounds the site shows it white via CSS (`brightness-0 invert`). Raio-X IA is presented as a Prime Solutions product ("Raio-X IA · por Prime Solutions").
- **Colors**: brand-900 `#1a2e38`, brand-800 `#223e4d` (dark navy-teal backgrounds), accent `#22a39f`, accent-dark `#1b8581` (buttons, highlights), Tailwind slate grays for text; headline gradient teal -> blue. Status colors for checks must keep AA contrast on the dark background.
- **Type**: Outfit (display/headings), Inter (text), JetBrains Mono (evidence/code). Self-hosted woff2 (SIL OFL), no Google Fonts request.
- **Look**: dark hero, teal pill badges and rounded teal buttons, translucent cards with thin borders, generous spacing. The report body may use a lighter surface for long reading; the design step decides.

## Pages and routes

| Route | Page | Notes |
|---|---|---|
| `GET /` | Landing | Hook (since 15/09/2026 Cloudflare blocks AI crawlers by default on new and free-plan domains), the form, how it works (3 steps), the 8 checks in plain words, FAQ, newsletter CTA |
| `POST /pedido` | Form handler | Plain HTML form post (works without our JS; only Turnstile needs JS). Success -> "Verifique seu e-mail" page; errors -> landing re-rendered with the values kept and a PT-BR message per field |
| `GET /r/{token}` | Report | Score + band, the checks ordered by severity (errors, warnings, ok, undetermined), each with explanation, "como corrigir" and the raw evidence in a collapsible block; check 2 always shows the "indicativo" caveat and, for Cloudflare, the dashboard steps. Processing/failed/expired states. Ad placeholders, newsletter CTA, consulting CTA |
| `POST /r/{token}/newsletter` | Newsletter from the report | One click: the token proves the e-mail was confirmed, so no second opt-in e-mail; consent logged with IP/UA |
| `GET /privacidade`, `GET /termos` | Legal | Raio-X's own LGPD privacy policy and terms in PT-BR (decision 1), drafted by me; lawyer review before launch |
| `GET /bot` | About our crawler | What `RaioXIA-Bot` fetches (only the home page and root files, on a user's request), why it also sends the AI bots' user agents (check 2), how to ask not to be analyzed |
| `GET /excluir-dados` | Data deletion | Form for the existing `POST /api/excluir-dados` flow |
| 404 / 500 | Error pages | PT-BR, same layout |
| `GET /robots.txt`, `/sitemap.xml`, `/llms.txt` | Our own files | We pass our own checks: AI crawlers allowed, sitemap, valid llms.txt, JSON-LD (`Organization`, `WebApplication`, `FAQPage`) |

## Design decisions

- **Templates**: `raioxia/web/templates/` with `base.html` (layout, header, footer), `partials/` (form, check card, score, CTAs, ad slot) and one file per page. Copy lives in the templates; check texts keep coming from the M1 checkers. M2's provisional pages are removed.
- **Tailwind v4 via the standalone CLI** (single binary, no Node): `scripts/build-css` compiles `raioxia/web/styles/app.css` into `raioxia/web/static/app.css`, which is committed, so runtime and Docker need no build step; a test fails if the committed CSS is stale. Design tokens (colors, radius, fonts) as CSS variables, so Claude Design can replace them in one place.
- **Static files** served by FastAPI (`/static/`, long cache with a content hash in the file name): CSS, self-hosted fonts, logo. No third-party requests besides Turnstile.
- **JavaScript**: only the Turnstile widget plus a few lines of progressive enhancement (disable the button while submitting; on the processing report, poll `/api/r/{token}` instead of the meta refresh). Everything works without our JS.
- **Security**: strict Content-Security-Policy (self + `challenges.cloudflare.com` for Turnstile), no inline scripts, `form-action 'self'`. CSRF: forms are same-origin POSTs with the Turnstile token (submit/deletion) or a secret path token (newsletter/unsubscribe/deletion), so no extra CSRF token is needed.
- **No cookies, no analytics** in M3: nothing to consent to beyond the form, no cookie banner. (Metrics come from `stats`.)
- **SEO**: title/description/OpenGraph per page, canonical URLs on `https://raiox.primesolutions.net.br`, `noindex` stays on token pages, landing and legal pages indexable.
- **Accessibility and mobile**: semantic HTML, labels tied to inputs, visible focus, contrast AA, works at 360 px wide; status is never color-only (icon + text).
- **Ads**: empty placeholders only (`<aside data-ad-slot="...">` with reserved size, so a future ad network doesn't shift the layout). No ad network script in M3.
- **Report states**: `processing` (auto-updates), `pending_confirmation`, `expired`, `failed` (PT-BR message from the job) and `done`. A reused result says when it was analyzed.
- **Tests**: page routes and their states, form re-render with errors, CSP/headers, the committed CSS is fresh, and **our own landing scores 100 on our own 8 checks** (the analyzer runs against the app through httpx's ASGI transport, no network).

## Approved choices (2026-10-15)

1. **Privacy and terms**: Raio-X IA's own pages (the company site only has WordPress's placeholder policy and no terms). Controller: **Prime Solutions Tecnologia e Serviços de Apoio Administrativo, CNPJ 43.757.807/0001-86**; privacy contact `raiox@primesolutions.net.br`. They describe what Raio-X really does: consents, double opt-in, retention (7 days unconfirmed, 180 days reports), deletion, Gmail/Google and Cloudflare (Turnstile) as processors, no cookies. Footer also links to www.primesolutions.net.br.
2. **Consulting CTA**: `mailto:raiox@primesolutions.net.br` with a prefilled PT-BR subject and body naming the analyzed site and report link.
3. **Visual**: design first in Claude Design with the Prime Solutions logo, palette and fonts (above).
4. **Newsletter** (not answered; using the proposal until told otherwise): "Raio-X IA Semanal: o que mudou nas IAs e como isso afeta o seu site", weekly; sending stays out of scope.

## Commits

0. [x] **Design** in Claude Design (approved 2026-10-15: https://claude.ai/artifact/AKKQDLPzFvWn295cxUE7uq): landing (desktop + 360 px), "verifique seu e-mail", report (done / processing / failed) and a legal page, with the Prime Solutions brand; user approval before commit 1
1. [x] Web layer skeleton: `raioxia/web/` (templates, static, Tailwind build script + committed CSS, tokens), base layout, 404/500, CSP and static headers; M2 provisional pages removed
2. [ ] Landing + `POST /pedido` (form, Turnstile widget, field errors, "verifique seu e-mail" page)
3. [ ] Report page: score, ordered check cards, evidence, edge caveat + Cloudflare steps, states, polling
4. [ ] Report CTAs: newsletter one-click, consulting CTA, ad placeholders
5. [ ] `/privacidade`, `/termos` (controller and CNPJ above), `/bot`, `/excluir-dados`; footer links; `CONSENT_TEXT_VERSION` bumped to the published texts' date
6. [ ] Our own `robots.txt`, `sitemap.xml`, `llms.txt`, JSON-LD, OpenGraph; "our landing scores 100" test
7. [ ] Accessibility/mobile pass, README/ROADMAP update

## Done criteria

- A visitor can go from the landing to a report entirely in the browser (Turnstile test keys locally), including the error paths.
- Report page shows every state correctly and works at 360 px.
- Our own site scores 100 with our own analyzer.
- No third-party requests except Turnstile; no cookies; CSP enforced; `pytest` green.
