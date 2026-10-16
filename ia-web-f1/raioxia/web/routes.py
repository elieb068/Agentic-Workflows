"""Public HTML pages."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia import privacy
from raioxia.api.routes import by_report_token
from raioxia.api.submissions import process_deletion_request, process_submission
from raioxia.net.fetcher import FetcherSettings
from raioxia.web import ads, report_view, seo
from raioxia.web.assets import static_url
from raioxia.web.content import CHECKS, FAQ, STEPS
from raioxia.web.pages import message, page

router = APIRouter()

EMPTY_FORM = {"url": "", "email": "", "accepted_terms": False, "newsletter": False}


def _landing(request: Request, app: AppDeps, *, status: int = 200, values=None, errors=None, form_error=None):
    return page(
        request, "landing.html", status=status, nav="landing", canonical_path="/",
        steps=STEPS, checks=CHECKS, faq=FAQ, turnstile_site_key=app.settings.turnstile_site_key,
        jsonld=seo.landing_jsonld(app.settings.base_url, static_url("img/prime-logo.png")),
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
    ad_slots = ads.ads_for(app.settings) if view["status"] == "done" else None  # never on pages without content
    response = page(
        request, "report.html", noindex=True, nav="report", r=view, checks=CHECKS, newsletter_done=newsletter == "ok", ads=ad_slots,
    )
    if ad_slots:
        response.headers["Content-Security-Policy"] = ads.csp_for(ad_slots)
    return response


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


@router.get("/privacidade")
def privacy_page(request: Request):
    return page(request, "privacy.html")


@router.get("/termos")
def terms_page(request: Request):
    return page(request, "terms.html")


@router.get("/bot")
def bot_page(request: Request, app: AppDeps = Depends(deps)):
    user_agent = FetcherSettings().user_agent
    return page(request, "bot.html", bot_name=user_agent.split(" ", 1)[0], user_agent=user_agent, server_ip=app.settings.server_ip)


@router.get("/excluir-dados")
def delete_request_page(request: Request, app: AppDeps = Depends(deps)):
    return page(request, "delete_request.html", turnstile_site_key=app.settings.turnstile_site_key)


@router.post("/excluir-dados")
def delete_request_form(
    request: Request,
    email: Annotated[str, Form(max_length=320)] = "",
    turnstile_token: Annotated[str, Form(alias="cf-turnstile-response", max_length=4096)] = "",
    session: Session = Depends(db_session),
    app: AppDeps = Depends(deps),
):
    outcome = process_deletion_request(session, app, email=email, turnstile_token=turnstile_token, ip=request_ip(request))
    if outcome.accepted:
        return message(request, 200, "Confira o seu e-mail", outcome.message, tone="success",
                       detail="O link vale por 24 horas. Se não chegar, olhe a caixa de spam.")
    field_error = outcome.message if outcome.field == "email" else None
    return page(
        request, "delete_request.html", status=outcome.status, turnstile_site_key=app.settings.turnstile_site_key,
        email=email, email_error=field_error, form_error=None if field_error else outcome.message,
    )


@router.get("/robots.txt")
def robots(app: AppDeps = Depends(deps)):
    return PlainTextResponse(seo.robots_txt(app.settings.base_url), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml")
def sitemap(app: AppDeps = Depends(deps)):
    return Response(seo.sitemap_xml(app.settings.base_url), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/llms.txt")
def llms(app: AppDeps = Depends(deps)):
    return PlainTextResponse(seo.llms_txt(app.settings.base_url), headers={"Cache-Control": "public, max-age=3600"})
