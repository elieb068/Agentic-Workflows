"""Public HTML pages."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from raioxia.api.deps import AppDeps, db_session, deps, request_ip
from raioxia import newsletter, privacy
from raioxia.api.routes import by_report_token
from raioxia.api.submissions import process_deletion_request, process_submission
from raioxia.net.fetcher import FetcherSettings
from raioxia.runner import AnalysisError, normalize_url
from raioxia.web import ads, archive, guides, report_view, seo, tools
from raioxia.web.assets import static_url
from raioxia.web.content import CHECKS, FAQ, STEPS
from raioxia.queues import EMAIL_QUEUE
from raioxia.web.pages import message, page
from raioxia.web.templating import TIMEZONE

router = APIRouter()

EMPTY_FORM = {"url": "", "email": "", "accepted_terms": False, "newsletter": False}


def _landing(request: Request, app: AppDeps, *, status: int = 200, values=None, errors=None, form_error=None):
    ad_slots = ads.ads_for(app.settings, lazy=True) if app.settings.adsense_auto_ads else None
    response = page(
        request, "landing.html", status=status, nav="landing", canonical_path="/",
        steps=STEPS, checks=CHECKS, faq=FAQ, turnstile_site_key=app.settings.turnstile_site_key,
        jsonld=seo.landing_jsonld(app.settings.base_url, static_url("img/prime-logo.png")),
        crawlers_reviewed=guides.crawlers_reviewed(),
        values=values or EMPTY_FORM, errors=errors or {}, form_error=form_error, ads=ad_slots,
    )
    if ad_slots:
        response.headers["Content-Security-Policy"] = ads.csp_for(ad_slots)
    return response


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
    newsletter.dispatch_sync(app.queues[EMAIL_QUEUE], app.settings)
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


@router.get("/guias")
def guides_index(request: Request, app: AppDeps = Depends(deps)):
    return page(
        request, "guides/index.html", nav="guides", guides=guides.GUIDES, weights=guides.score_weights(),
        jsonld=seo.guides_index_jsonld(app.settings.base_url),
    )


@router.get("/guias/{slug}")
def guide_page(slug: str, request: Request, app: AppDeps = Depends(deps)):
    guide = guides.by_slug(slug)
    if guide is None:
        return message(request, 404, "Guia não encontrado", "Este guia não existe ou mudou de endereço.", link_href="/guias", link_label="Ver todos os guias")
    extra = {"crawlers": guides.crawler_rows(), "crawlers_reviewed": guides.crawlers_reviewed()} if slug == "robos-de-ia" else {}
    return page(
        request, f"guides/{slug}.html", nav="guides", guide=guide, guides=guides.GUIDES,
        jsonld=seo.guide_jsonld(app.settings.base_url, guide, static_url("img/prime-logo.png"), static_url("img/og-raiox.png")),
        **extra,
    )


@router.get("/excluir-dados")
def delete_request_page(request: Request, app: AppDeps = Depends(deps)):
    # A utility form: out of search results (and of the sitemap), still linked from every footer.
    return page(request, "delete_request.html", noindex=True, turnstile_site_key=app.settings.turnstile_site_key)


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
        request, "delete_request.html", status=outcome.status, noindex=True, turnstile_site_key=app.settings.turnstile_site_key,
        email=email, email_error=field_error, form_error=None if field_error else outcome.message,
    )


@router.get("/robots.txt")
def robots(app: AppDeps = Depends(deps)):
    return PlainTextResponse(seo.robots_txt(app.settings.base_url), headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml")
def sitemap(session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    issues = {f"/newsletter/{i.edition}": i.publish_at.astimezone(TIMEZONE).date() for i in archive.published(session, app.clock())}
    return Response(seo.sitemap_xml(app.settings.base_url, issues), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/indexnow.txt")
def indexnow_key(app: AppDeps = Depends(deps)):
    """IndexNow key file: proves the URLs we submit (python -m raioxia indexnow) are ours."""
    if not app.settings.indexnow_key:
        return PlainTextResponse("Not found", status_code=404)
    return PlainTextResponse(app.settings.indexnow_key)


@router.get("/llms.txt")
def llms(session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    issues = [(f"/newsletter/{i.edition}", i.subject) for i in archive.published(session, app.clock())]
    return PlainTextResponse(seo.llms_txt(app.settings.base_url, issues), headers={"Cache-Control": "public, max-age=3600"})


# --- Newsletter archive -----------------------------------------------------------------------------

@router.get("/newsletter")
def newsletter_index(request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    return page(request, "newsletter/index.html", nav="guides", issues=archive.published(session, app.clock()))


@router.get("/newsletter/{edition}")
def newsletter_issue(edition: int, request: Request, session: Session = Depends(db_session), app: AppDeps = Depends(deps)):
    issue = archive.get_published(session, edition, app.clock())
    if issue is None:
        return message(request, 404, "Edição não encontrada", "Esta edição não existe ou ainda não foi publicada.", link_href="/newsletter", link_label="Ver todas as edições")
    jsonld = seo.issue_jsonld(app.settings.base_url, issue, static_url("img/prime-logo.png"), static_url("img/og-raiox.png"))
    return page(request, "newsletter/issue.html", nav="guides", issue=issue, c=issue.content, jsonld=jsonld)


# --- Free tools --------------------------------------------------------------------------------------

ROBOTS_TOOL = "tools/robots_ai.html"


def _robots_tool(request: Request, app: AppDeps, *, status: int = 200, **context):
    return page(
        request, ROBOTS_TOOL, status=status, nav="guides", turnstile_site_key=app.settings.turnstile_site_key,
        jsonld=seo.robots_tool_jsonld(app.settings.base_url), **context,
    )


@router.get("/ferramentas/robots-txt-ia")
def robots_tool_page(request: Request, app: AppDeps = Depends(deps)):
    return _robots_tool(request, app, url="")


@router.post("/ferramentas/robots-txt-ia")
async def robots_tool_run(
    request: Request,
    url: Annotated[str, Form(max_length=2048)] = "",
    turnstile_token: Annotated[str, Form(alias="cf-turnstile-response", max_length=4096)] = "",
    app: AppDeps = Depends(deps),
):
    ip = request_ip(request)
    now = app.clock()
    if not await run_in_threadpool(app.turnstile, turnstile_token, ip):
        return _robots_tool(request, app, status=403, url=url, error="Não conseguimos confirmar que você não é um robô. Recarregue a página e tente de novo.")
    if not app.limiter.hit("tool_ip", ip or "unknown", now):
        return _robots_tool(request, app, status=429, url=url, error="Muitos testes seguidos. Espere alguns minutos e tente de novo.")
    try:
        target = normalize_url(url)
    except AnalysisError as exc:
        return _robots_tool(request, app, status=422, url=url, error=exc.message)
    host = target.url.split("://", 1)[1].split("/", 1)[0]
    if not app.limiter.hit("tool_domain", host, now):
        return _robots_tool(request, app, status=429, url=url, error="Esse site foi testado muitas vezes na última hora. Tente mais tarde.")
    try:
        result = await tools.robots_ai_report(url)
    except tools.ToolError as exc:
        return _robots_tool(request, app, status=422, url=url, error=exc.message)
    return _robots_tool(request, app, url=url, result=result, noindex=True)
