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


def test_secret_paths_are_redacted():
    from raioxia.logs import redact

    cases = {
        "/r/AbC_12-x": "/r/<redacted>",
        "/r/AbC_12-x/newsletter": "/r/<redacted>/newsletter",
        "/api/r/AbC123": "/api/r/<redacted>",
        "/confirmar/tok.en_1?x=1": "/confirmar/<redacted>?x=1",
        "/descadastrar/12.99.abc": "/descadastrar/<redacted>",
        "/excluir-dados/12.99.abc": "/excluir-dados/<redacted>",
        "/webhooks/brevo/uo84-sW67secret": "/webhooks/brevo/<redacted>",
        "/excluir-dados": "/excluir-dados",  # the public form page has no token
        "/guias/robots-txt-para-ia": "/guias/robots-txt-para-ia",
        "/newsletter/3": "/newsletter/3",
    }
    assert {path: redact(path) for path in cases} == cases


def test_uvicorn_access_line_and_extra_fields_are_redacted(capsys):
    from raioxia.logs import configure_logging

    configure_logging("INFO", json_lines=True)
    try:
        # Exactly how uvicorn logs a request: the path travels in record.args.
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "172.18.0.2:5000", "POST", "/webhooks/brevo/s3cr3t-value", "1.1", 200)
        logging.getLogger("raioxia.api.app").error("unhandled error", extra={"path": "/r/report-token-123"})
    finally:
        logging.getLogger().handlers[:] = []
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[0]["msg"] == '172.18.0.2:5000 - "POST /webhooks/brevo/<redacted> HTTP/1.1" 200'
    assert lines[1]["path"] == "/r/<redacted>"
    assert "s3cr3t" not in out and "report-token-123" not in out
