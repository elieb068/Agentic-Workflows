"""Public HTML pages."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia import privacy
from raioxia.api.routes import by_report_token
from raioxia.api.submissions import process_submission
from raioxia.web import report_view
from raioxia.web.content import CHECKS, FAQ, STEPS
from raioxia.web.pages import message, page

router = APIRouter()

EMPTY_FORM = {"url": "", "email": "", "accepted_terms": False, "newsletter": False}


def _landing(request: Request, app: AppDeps, *, status: int = 200, values=None, errors=None, form_error=None):
    return page(
        request, "landing.html", status=status, nav="landing", canonical_path="/",
        steps=STEPS, checks=CHECKS, faq=FAQ, turnstile_site_key=app.settings.turnstile_site_key,
        values=values or EMPTY_FORM, errors=errors or {}, form_error=form_error,
    )


@router.get("/")
def landing(request: Request, app: AppDeps = Depends(deps)):
    return _landing(request, app)


@router.post("/pedido")
def submit_form(
    request: Request,
    url: Annotated[str, Form(max_length=2048)] = "",
    email: Annotated[str, Form(max_length=320)] = "",
    accepted_terms: Annotated[bool, Form()] = False,
    newsletter: Annotated[bool, Form()] = False,
    turnstile_token: Annotated[str, Form(alias="cf-turnstile-response", max_length=4096)] = "",
    session: Session = Depends(db_session),
    app: AppDeps = Depends(deps),
):
    outcome = process_submission(
        session, app, url=url, email=email, accepted_terms=accepted_terms, newsletter=newsletter,
        turnstile_token=turnstile_token, ip=request_ip(request), user_agent=request.headers.get("user-agent", "")[:512] or None,
    )
    if outcome.accepted:
        return page(request, "check_email.html", noindex=True, email=outcome.email, domain=outcome.domain)
    values = {"url": url, "email": email, "accepted_terms": accepted_terms, "newsletter": newsletter}
    field = outcome.field
    return _landing(
        request, app, status=outcome.status, values=values,
        errors={field: outcome.message} if field else {},
        form_error="Confira os campos marcados e tente de novo." if field else outcome.message,
    )


@router.get("/r/{token}")
def report_page(token: str, request: Request, newsletter: str = "", session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    analysis = by_report_token(session, token)
    if analysis is None:
        return message(request, 404, "Relatório não encontrado", "Confira se o endereço do relatório está completo.")
    view = report_view.build(session, analysis, base_url=app.settings.base_url)
    return page(request, "report.html", noindex=True, nav="report", r=view, checks=CHECKS, newsletter_done=newsletter == "ok")


@router.post("/r/{token}/newsletter")
def report_newsletter(token: str, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    """One click from the report: the report link only reaches people who confirmed this e-mail."""
    analysis = by_report_token(session, token)
    if analysis is None or analysis.lead is None or analysis.confirmed_at is None:
        return message(request, 404, "Relatório não encontrado", "Confira se o endereço do relatório está completo.")
    privacy.subscribe(
        session, analysis.lead, ip=request_ip(request), user_agent=request.headers.get("user-agent", "")[:512] or None, now=app.clock()
    )
    session.commit()
    return RedirectResponse(f"/r/{token}?newsletter=ok", status_code=303)
