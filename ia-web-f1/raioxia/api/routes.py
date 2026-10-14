"""M2 endpoints: submit, confirm, report (JSON + provisional page), health. The real pages come in M3."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from raioxia import pipeline
from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia.db import Analysis
from raioxia.email.outbox import dispatch, queue_confirm_email, queue_report_email
from raioxia.jobs import enqueue_analysis
from raioxia.pipeline import Confirmation, SubmissionError
from raioxia.queues import ANALYSIS_QUEUE, EMAIL_QUEUE

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

ACCEPTED_MESSAGE = "Pronto! Enviamos um link de confirmação para o seu e-mail. A análise começa assim que você clicar nele."
ACCEPTED = {"status": "confirm_email", "message": ACCEPTED_MESSAGE}
TURNSTILE_MESSAGE = "Não conseguimos confirmar que você não é um robô. Recarregue a página e tente de novo."
IP_LIMIT_MESSAGE = "Você fez muitos pedidos em pouco tempo. Tente de novo mais tarde."
DOMAIN_LIMIT_MESSAGE = "Esse site já foi pedido muitas vezes hoje. Tente de novo amanhã."


class SubmitIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    email: str = Field(min_length=3, max_length=320)
    accepted_terms: bool = False
    newsletter: bool = False
    turnstile_token: str = Field(default="", max_length=4096)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


@router.post("/api/analyses", status_code=202)
def submit(body: SubmitIn, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    ip, now = request_ip(request), app.clock()
    if not app.turnstile(body.turnstile_token, ip):
        return _error(403, "turnstile_failed", TURNSTILE_MESSAGE)
    if not app.limiter.hit("ip", ip or "unknown", now):
        log.info("submission rate limited", extra={"scope": "ip"})
        return _error(429, "rate_limited", IP_LIMIT_MESSAGE)
    try:
        valid = pipeline.validate_submission(url=body.url, email=body.email, accepted_terms=body.accepted_terms)
    except SubmissionError as exc:
        return _error(422, exc.code, exc.message)
    if not app.limiter.hit("email", valid.email, now):
        log.info("submission rate limited", extra={"scope": "email"})
        return ACCEPTED  # same answer as a real submission: don't reveal anything about the address
    if not app.limiter.hit("domain", valid.domain, now):
        log.info("submission rate limited", extra={"scope": "domain", "domain": valid.domain})
        return _error(429, "rate_limited", DOMAIN_LIMIT_MESSAGE)

    submission = pipeline.submit(
        session,
        url=valid.url,
        email=valid.email,
        accepted_terms=body.accepted_terms,
        newsletter=body.newsletter,
        ip=ip,
        user_agent=request.headers.get("user-agent", "")[:512] or None,
        now=now,
    )
    row = queue_confirm_email(session, submission.analysis, submission.confirm_token, base_url=app.settings.base_url)
    session.commit()
    dispatch(app.queues[EMAIL_QUEUE], [row.id])
    return ACCEPTED


def _message(request: Request, app: AppDeps, status: int, title: str, message: str) -> HTMLResponse:
    context = {"title": title, "message": message, "base_url": app.settings.base_url}
    return templates.TemplateResponse(request, "message.html", context, status_code=status)


@router.get("/confirmar/{token}")
def confirm(token: str, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    result, analysis = pipeline.confirm(session, token, now=app.clock())
    email_ids: list[int] = []
    if result is Confirmation.REUSED:
        email_ids.append(queue_report_email(session, analysis, base_url=app.settings.base_url).id)
    session.commit()
    if result is Confirmation.QUEUED:
        enqueue_analysis(app.queues[ANALYSIS_QUEUE], analysis.id)
    dispatch(app.queues[EMAIL_QUEUE], email_ids)

    if result is Confirmation.INVALID:
        return _message(request, app, 404, "Link inválido", "Esse link de confirmação não existe. Confira se copiou o endereço inteiro.")
    if result is Confirmation.EXPIRED:
        return _message(request, app, 410, "Link expirado", "Esse link venceu porque passou mais de 24 horas. Faça um novo pedido.")
    log.info("analysis confirmed", extra={"analysis_id": analysis.id, "result": result.value})
    return RedirectResponse(f"/r/{analysis.report_token}", status_code=303)


def _public_status(status: str) -> str:
    return "processing" if status in ("queued", "running") else status


def report_payload(analysis: Analysis) -> dict:
    done = analysis.status == "done"
    return {
        "status": _public_status(analysis.status),
        "url": analysis.url,
        "domain": analysis.domain,
        "final_url": analysis.final_url if done else None,
        "score": analysis.score if done else None,
        "band": {"id": analysis.band_id, "label": analysis.band_label} if done and analysis.band_id else None,
        "checks": [
            {
                "id": r.check_id, "status": r.status, "titulo": r.titulo, "explicacao": r.explicacao,
                "como_corrigir": r.como_corrigir, "evidencia": r.evidencia, "peso": r.peso,
            }
            for r in analysis.results
        ] if done else [],
        "analyzed_at": analysis.finished_at.isoformat() if done and analysis.finished_at else None,
        "error": {"code": analysis.error_code, "message": analysis.error_message} if analysis.status == "failed" else None,
        "version": analysis.version if done else None,
    }


def _by_report_token(session: Session, token: str) -> Analysis | None:
    return session.scalar(select(Analysis).where(Analysis.report_token == token))


@router.get("/api/r/{token}")
def report_json(token: str, session: Session = Depends(db_session)):
    analysis = _by_report_token(session, token)
    if analysis is None:
        return _error(404, "not_found", "Relatório não encontrado.")
    return report_payload(analysis)


@router.get("/r/{token}", response_class=HTMLResponse)
def report_page(token: str, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    analysis = _by_report_token(session, token)
    if analysis is None:
        return _message(request, app, 404, "Relatório não encontrado", "Confira se o endereço do relatório está completo.")
    return templates.TemplateResponse(request, "report.html", {"report": report_payload(analysis)})


@router.get("/healthz")
def healthz(session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    checks = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        checks["database"] = type(exc).__name__
    try:
        app.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = type(exc).__name__
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse({"ok": healthy, **checks}, status_code=200 if healthy else 503)
