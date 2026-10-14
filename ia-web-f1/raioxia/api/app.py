"""FastAPI application factory.

Production: `uvicorn raioxia.api.app:create_app --factory --host 0.0.0.0 --port 8000`
(no --proxy-headers: client IPs are resolved by raioxia.api.client_ip with TRUSTED_PROXIES).
"""

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from raioxia import __version__
from raioxia.api.deps import AppDeps
from raioxia.api.routes import router

# Paths that carry tokens or are private: keep them out of search engines.
_NOINDEX_PREFIXES = ("/r/", "/confirmar/", "/api/", "/descadastrar/", "/excluir-dados/")


def _production_deps() -> AppDeps:
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
    )


def create_app(app_deps: AppDeps | None = None) -> FastAPI:
    app = FastAPI(title="Raio-X IA", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = app_deps or _production_deps()
    app.include_router(router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")  # report/confirm URLs carry tokens
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith(_NOINDEX_PREFIXES):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request", "message": "Confira os campos e tente de novo."}, status_code=422)

    return app
