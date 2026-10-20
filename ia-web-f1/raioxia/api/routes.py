"""JSON API, the e-mail link endpoints (confirm, unsubscribe, deletion) and health. HTML pages: raioxia.web."""

import hmac
import logging

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from raioxia import newsletter, pipeline, privacy, stats
from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia.db import Analysis, NewsletterIssue
from raioxia.api.submissions import ACCEPTED_MESSAGE, DELETION_ACCEPTED_MESSAGE, process_deletion_request, process_submission
from raioxia.email.outbox import dispatch, queue_report_email
from raioxia.jobs import enqueue_analysis
from raioxia.pipeline import Confirmation
from raioxia.queues import ANALYSIS_QUEUE, EMAIL_QUEUE
from raioxia.tokens import verify
from raioxia.web import archive
from raioxia.web.pages import action, message

log = logging.getLogger(__name__)
router = APIRouter()

ACCEPTED = {"status": "confirm_email", "message": ACCEPTED_MESSAGE}


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
    outcome = process_submission(
        session, app, url=body.url, email=body.email, accepted_terms=body.accepted_terms, newsletter=body.newsletter,
        turnstile_token=body.turnstile_token, ip=request_ip(request), user_agent=request.headers.get("user-agent", "")[:512] or None,
    )
    if not outcome.accepted:
        return _error(outcome.status, outcome.code, outcome.message)
    return ACCEPTED


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
    if result in (Confirmation.QUEUED, Confirmation.REUSED) and analysis.newsletter_opt_in:
        newsletter.dispatch_sync(app.queues[EMAIL_QUEUE], app.settings)

    if result is Confirmation.INVALID:
        return message(request, 404, "Link inválido", "Esse link de confirmação não existe. Confira se copiou o endereço inteiro.")
    if result is Confirmation.EXPIRED:
        return message(request, 410, "Link expirado", "Esse link venceu porque passou mais de 24 horas. Faça um novo pedido.", link_label="Fazer um novo pedido")
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


def by_report_token(session: Session, token: str) -> Analysis | None:
    return session.scalar(select(Analysis).where(Analysis.report_token == token))


@router.get("/api/r/{token}")
def report_json(token: str, session: Session = Depends(db_session)):
    analysis = by_report_token(session, token)
    if analysis is None:
        return _error(404, "not_found", "Relatório não encontrado.")
    return report_payload(analysis)


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


# --- LGPD -------------------------------------------------------------------------------------------
# Links from e-mails open a page with a button; only the POST acts. Mail scanners that prefetch links
# can't unsubscribe or delete anyone. Gmail's one-click unsubscribe POSTs straight to the same URL.

DELETION_ACCEPTED = {"status": "check_email", "message": DELETION_ACCEPTED_MESSAGE}


def _lead_from_link(app: AppDeps, purpose: str, token: str) -> int | None:
    return verify(app.settings.secret_key.get_secret_value(), purpose, token, app.clock())


@router.get("/descadastrar/{token}")
def unsubscribe_page(token: str, request: Request, app: AppDeps = Depends(deps)):
    if _lead_from_link(app, "unsubscribe", token) is None:
        return message(request, 404, "Link inválido", "Esse link de descadastro não é válido.")
    return action(request, "Parar de receber e-mails", "Você não vai mais receber a newsletter nem outros e-mails do Raio-X IA que não tenha pedido.", "Não quero mais receber e-mails")


@router.post("/descadastrar/{token}")
def unsubscribe(token: str, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    lead_id = _lead_from_link(app, "unsubscribe", token)
    if lead_id is None:
        return message(request, 404, "Link inválido", "Esse link de descadastro não é válido.")
    privacy.unsubscribe(session, lead_id, ip=request_ip(request), user_agent=request.headers.get("user-agent", "")[:512] or None, now=app.clock())
    session.commit()
    newsletter.dispatch_sync(app.queues[EMAIL_QUEUE], app.settings)
    return message(request, 200, "Pronto", "Você não vai mais receber e-mails do Raio-X IA. Se pedir um novo relatório, enviaremos só o que for necessário para ele.", tone="success")


class DeletionIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    turnstile_token: str = Field(default="", max_length=4096)


@router.post("/api/excluir-dados", status_code=202)
def request_deletion(body: DeletionIn, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    outcome = process_deletion_request(session, app, email=body.email, turnstile_token=body.turnstile_token, ip=request_ip(request))
    if not outcome.accepted:
        return _error(outcome.status, outcome.code, outcome.message)
    return DELETION_ACCEPTED


@router.get("/excluir-dados/{token}")
def deletion_page(token: str, request: Request, app: AppDeps = Depends(deps)):
    if _lead_from_link(app, "delete", token) is None:
        return message(request, 404, "Link inválido ou vencido", "Peça a exclusão de novo para receber um link novo.", link_href="/excluir-dados", link_label="Pedir a exclusão de novo")
    return action(
        request, "Excluir meus dados",
        "Vamos apagar seu e-mail, seus consentimentos, seus pedidos e os relatórios deles. Isso não pode ser desfeito.",
        "Excluir meus dados", danger=True,
    )


@router.post("/excluir-dados/{token}")
def delete_data(token: str, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    lead_id = _lead_from_link(app, "delete", token)
    if lead_id is None:
        return message(request, 404, "Link inválido ou vencido", "Peça a exclusão de novo para receber um link novo.", link_href="/excluir-dados", link_label="Pedir a exclusão de novo")
    privacy.delete_lead_data(session, lead_id)
    session.commit()
    newsletter.dispatch_sync(app.queues[EMAIL_QUEUE], app.settings)
    return message(request, 200, "Dados excluídos", "Apagamos os dados ligados a este e-mail no Raio-X IA.", tone="success")


# --- Newsletter (Brevo + the n8n workflow) ---------------------------------------------------------------

def _secret_matches(given: str, expected: SecretStr | None) -> bool:
    return expected is not None and hmac.compare_digest(given.encode(), expected.get_secret_value().encode())


@router.post("/webhooks/brevo/{secret}")
def brevo_webhook(
    secret: str, payload: dict | list = Body(...), session: Session = Depends(db_session), app: AppDeps = Depends(deps),
):
    """Unsubscribes, hard bounces and spam complaints from Brevo: recorded in Raio-X like any other unsubscribe."""
    if not _secret_matches(secret, app.settings.brevo_webhook_secret):
        return _error(404, "not_found", "Not found.")
    events = payload if isinstance(payload, list) else [payload]
    stopped = 0
    for event in events[:500]:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event", "")).strip().lower()
        email = str(event.get("email", "")).strip().lower()
        if name not in newsletter.STOP_EVENTS or not email:
            continue
        lead = privacy.find_lead(session, email)
        if lead is None or lead.unsubscribed_at is not None:
            continue
        privacy.unsubscribe(session, lead.id, ip=None, user_agent=f"brevo webhook: {name}", now=app.clock())
        stopped += 1
    session.commit()
    if stopped:
        newsletter.dispatch_sync(app.queues[EMAIL_QUEUE], app.settings)
    return {"ok": True, "unsubscribed": stopped}


@router.get("/api/newsletter/stats")
def newsletter_stats(
    request: Request, days: int = Query(7, ge=1, le=90), session: Session = Depends(db_session), app: AppDeps = Depends(deps),
):
    """Anonymous aggregate of recent analyses for the weekly edition. Bearer NEWSLETTER_STATS_TOKEN."""
    if not _newsletter_authorized(request, app):
        return _error(401, "unauthorized", "Missing or invalid token.")
    return stats.findings(session, now=app.clock(), days=days)


def _newsletter_authorized(request: Request, app: AppDeps) -> bool:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and _secret_matches(token, app.settings.newsletter_stats_token)


@router.put("/api/newsletter/issues/{edition}")
def publish_issue(
    edition: int, issue: archive.IssueIn, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps),
):
    """Archive an approved edition (n8n). Visible at /newsletter/<edition> from publish_at on. Idempotent."""
    if not _newsletter_authorized(request, app):
        return _error(401, "unauthorized", "Missing or invalid token.")
    if not 1 <= edition <= 100_000:
        return _error(422, "invalid_edition", "Edition must be a positive number.")
    row = archive.upsert(session, edition, issue)
    session.commit()
    log.info("newsletter issue archived", extra={"edition": edition})
    return {"ok": True, "edition": row.edition, "url": f"{app.settings.base_url}/newsletter/{row.edition}", "publish_at": row.publish_at.isoformat()}


@router.delete("/api/newsletter/issues/{edition}")
def unpublish_issue(edition: int, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    """Remove an edition from the archive (e.g. its Brevo send was cancelled)."""
    if not _newsletter_authorized(request, app):
        return _error(401, "unauthorized", "Missing or invalid token.")
    row = session.get(NewsletterIssue, edition)
    if row is not None:
        session.delete(row)
        session.commit()
    return {"ok": True, "deleted": row is not None}
