"""FastAPI application factory.

Production: `uvicorn raioxia.api.app:create_app --factory --host 0.0.0.0 --port 8000`
(no --proxy-headers: client IPs are resolved by raioxia.api.client_ip with TRUSTED_PROXIES).
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from raioxia import __version__
from raioxia.api.deps import AppDeps
from raioxia.api.routes import router
from raioxia.web.assets import STATIC_DIR
from raioxia.web.pages import message
from raioxia.web.routes import router as web_router

log = logging.getLogger(__name__)

# Paths that carry tokens or are private: keep them out of search engines.
_NOINDEX_PREFIXES = ("/r/", "/confirmar/", "/api/", "/descadastrar/", "/excluir-dados/", "/webhooks/")
# Only our own files plus Cloudflare Turnstile (script + its iframe). No inline scripts or styles.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://challenges.cloudflare.com",
    "frame-src https://challenges.cloudflare.com",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


def _production_deps() -> AppDeps:
    from raioxia.api.ratelimit import RateLimiter, load_limits
    from raioxia.api.turnstile import TurnstileVerifier
    from raioxia.db.engine import get_sessionmaker
    from raioxia.logs import configure_logging
    from raioxia.queues import ANALYSIS_QUEUE, EMAIL_QUEUE, get_queue, get_redis
    from raioxia.settings import get_settings

    settings = get_settings()
    configure_logging(settings.log_level, json_lines=settings.log_json)
    redis = get_redis()
    return AppDeps(
        settings=settings,
        sessions=get_sessionmaker(),
        redis=redis,
        queues={name: get_queue(name, redis) for name in (ANALYSIS_QUEUE, EMAIL_QUEUE)},
        clock=lambda: datetime.now(UTC),
        turnstile=TurnstileVerifier.from_settings(settings),
        limiter=RateLimiter(redis, load_limits()),
    )


def create_app(app_deps: AppDeps | None = None) -> FastAPI:
    app = FastAPI(title="Raio-X IA", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = app_deps or _production_deps()
    app.include_router(router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")  # report/confirm URLs carry tokens
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if path.startswith(_NOINDEX_PREFIXES):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if path.startswith("/static/") and response.status_code == 200:
            # ?v=<content hash> (static_url) never changes for a given file: cache it for a year. Fonts are
            # referenced without it (preload links, @font-face) and never change: a new font gets a new file name.
            immutable = "v" in request.query_params or path.startswith("/static/fonts/")
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "public, max-age=86400"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request", "message": "Confira os campos e tente de novo."}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or exc.status_code != 404:
            return JSONResponse({"error": "http_error", "message": str(exc.detail)}, status_code=exc.status_code)
        return message(request, 404, "Página não encontrada", "O endereço que você abriu não existe aqui.", detail="Confira se digitou certo ou volte para o início.")

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "internal", "message": "Tivemos um problema do nosso lado. Tente de novo em alguns minutos."}, status_code=500)
        return message(request, 500, "Algo deu errado", "Tivemos um problema do nosso lado. Tente de novo em alguns minutos.")

    return app
