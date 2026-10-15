"""JSON API, the e-mail link endpoints (confirm, unsubscribe, deletion) and health. HTML pages: raioxia.web."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from raioxia import pipeline, privacy
from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia.db import Analysis
from raioxia.api.submissions import ACCEPTED_MESSAGE, IP_LIMIT_MESSAGE, TURNSTILE_MESSAGE, process_submission
from raioxia.email.outbox import DELETION_LINK_TTL, dispatch, queue_deletion_email, queue_report_email
from raioxia.jobs import enqueue_analysis
from raioxia.pipeline import Confirmation, SubmissionError
from raioxia.queues import ANALYSIS_QUEUE, EMAIL_QUEUE
from raioxia.tokens import sign, verify
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

DELETION_ACCEPTED = {
    "status": "check_email",
    "message": "Se esse e-mail estiver no Raio-X IA, enviamos um link para confirmar a exclusão dos dados.",
}


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
    return message(request, 200, "Pronto", "Você não vai mais receber e-mails do Raio-X IA. Se pedir um novo relatório, enviaremos só o que for necessário para ele.", tone="success")


class DeletionIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    turnstile_token: str = Field(default="", max_length=4096)


@router.post("/api/excluir-dados", status_code=202)
def request_deletion(body: DeletionIn, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    ip, now = request_ip(request), app.clock()
    if not app.turnstile(body.turnstile_token, ip):
        return _error(403, "turnstile_failed", TURNSTILE_MESSAGE)
    if not app.limiter.hit("ip", f"deletion:{ip or 'unknown'}", now):  # own counter, same limits as submissions
        return _error(429, "rate_limited", IP_LIMIT_MESSAGE)
    try:
        address = pipeline.normalize_email(body.email)
    except SubmissionError as exc:
        return _error(422, exc.code, exc.message)
    lead = privacy.find_lead(session, address) if app.limiter.hit("email", f"deletion:{address}", now) else None
    if lead is None:
        return DELETION_ACCEPTED  # same answer whether or not the address is known
    token = sign(app.settings.secret_key.get_secret_value(), "delete", lead.id, now + DELETION_LINK_TTL)
    row = queue_deletion_email(session, lead, token, base_url=app.settings.base_url)
    session.commit()
    dispatch(app.queues[EMAIL_QUEUE], [row.id])
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
    return message(request, 200, "Dados excluídos", "Apagamos os dados ligados a este e-mail no Raio-X IA.", tone="success")
