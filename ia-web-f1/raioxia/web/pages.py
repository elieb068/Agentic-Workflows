"""Rendering helpers shared by every HTML route."""

from fastapi import Request
from fastapi.responses import HTMLResponse

from raioxia.web.templating import templates


def page(request: Request, template: str, *, status: int = 200, noindex: bool = False, **context) -> HTMLResponse:
    settings = request.app.state.deps.settings
    values = {"base_url": settings.base_url, "canonical_path": request.url.path, "noindex": noindex, **context}
    return templates.TemplateResponse(request, template, values, status_code=status)


def message(
    request: Request, status: int, title: str, text: str, *, tone: str = "error", detail: str | None = None,
    link_href: str | None = None, link_label: str | None = None,
) -> HTMLResponse:
    return page(
        request, "message.html", status=status, noindex=True, title=title, message=text, tone=tone, detail=detail,
        link_href=link_href, link_label=link_label,
    )


def action(request: Request, title: str, text: str, button: str, *, danger: bool = False) -> HTMLResponse:
    """A page whose only control is a POST button to the same URL (links from e-mails never act on GET)."""
    return page(request, "action.html", noindex=True, title=title, message=text, button=button, danger=danger, action=request.url.path)
