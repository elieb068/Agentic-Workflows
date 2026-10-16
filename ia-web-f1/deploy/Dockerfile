# One image for the API, both workers and the CLI jobs.
# The base matches the pinned playwright package (pyproject.toml): bump both together.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RAIOXIA_CONFIG_DIR=/app/config

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY config ./config
COPY raioxia ./raioxia
COPY tests ./tests
# Editable install: the package runs from /app (templates, static files, migrations and config sit
# next to the code). Dev extras are tiny and let `pytest` run inside the image as a smoke check.
# Readable by pwuser whatever the modes in the checkout (the server clones with umask 077).
RUN chmod -R a+rX /app \
    && pip install -e ".[dev]" \
    && python -W error::SyntaxWarning -c "import raioxia.api.app, raioxia.web.routes, raioxia.jobs, raioxia.email.gmail"

# pwuser (uid 1001) comes with the Playwright image; on the server it is also the uid that owns
# the Gmail key, so the key stays mode 600.
USER pwuser
EXPOSE 8000
CMD ["uvicorn", "raioxia.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-proxy-headers", "--no-server-header"]
