"""Structured (JSON lines) logging for the API and workers. Stdlib only."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else came in through `extra=` and is logged as a field.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}

# URL paths whose last segment is a secret: report links, e-mail links (confirm, unsubscribe, deletion) and the
# Brevo webhook. Uvicorn's access log (and our own error logs) would otherwise keep them in plain text.
_SECRET_PATH = re.compile(r"(/(?:api/)?r/|/confirmar/|/descadastrar/|/excluir-dados/|/webhooks/brevo/)[^/?#\s\"']+")
REDACTED = "<redacted>"


def redact(text: str) -> str:
    return _SECRET_PATH.sub(rf"\1{REDACTED}", text)


class RedactSecretsFilter(logging.Filter):
    """Masks tokens in URL paths before any line is written (message, its args and `extra=` fields)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        for key, value in list(vars(record).items()):
            if key not in _RESERVED and isinstance(value, str):
                setattr(record, key, redact(value))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", *, json_lines: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_lines else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecretsFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Uvicorn installs its own handlers; route them through ours so every line has the same shape.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
