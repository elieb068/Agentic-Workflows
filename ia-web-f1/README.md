# ia-web-f1 — Raio-X IA (Fase 1)

Web tool (PT-BR) that checks whether a site's "door is open" to AI crawlers: robots.txt rules per AI user-agent, CDN/WAF blocking, llms.txt validity, meta robots, JS dependency, structured data, sitemap and basics. The user submits a URL, confirms by e-mail (double opt-in) and receives a report at `/r/{token}`.

Project definitions, scope and conventions live in [`CLAUDE.md`](CLAUDE.md). Planning lives in [`.planning/`](.planning/).

## Status

Scaffold only. Next milestone: **M1 — analysis core** (`raioxia/checkers/`, CLI `python -m raioxia analyze <url>`, tests).

## Layout

```
raioxia/            Python package (FastAPI app, worker, checkers)
  checkers/         Pluggable analysis modules (one per check)
config/             Versioned config (ai_agents.yaml, weights)
tests/              pytest, simulated-site fixtures only (no real sites)
.planning/          Project, requirements and roadmap docs
```

## Dev quickstart

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```
