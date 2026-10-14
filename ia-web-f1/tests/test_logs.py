import json
import logging

from raioxia.logs import JsonFormatter


def record(msg: str, *args, exc_info=None, **extra) -> logging.LogRecord:
    rec = logging.getLogger("raioxia.test").makeRecord("raioxia.test", logging.INFO, __file__, 1, msg, args, exc_info, extra=extra)
    return rec


def test_json_line_with_extra_fields():
    line = JsonFormatter().format(record("analysis %s done", 42, analysis_id=42, domain="exemplo.com.br"))
    entry = json.loads(line)
    assert entry["level"] == "info" and entry["logger"] == "raioxia.test"
    assert entry["msg"] == "analysis 42 done"
    assert entry["analysis_id"] == 42 and entry["domain"] == "exemplo.com.br"
    assert entry["ts"].endswith("+00:00")
    assert "args" not in entry and "levelno" not in entry


def test_exception_and_non_json_values():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        line = JsonFormatter().format(record("failed", exc_info=sys.exc_info(), when=object(), texto="ação"))
    entry = json.loads(line)
    assert "ValueError: boom" in entry["exc"]
    assert entry["when"].startswith("<object")
    assert "ação" in line  # PT-BR kept readable, not \u-escaped
