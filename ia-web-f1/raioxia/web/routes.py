"""Public HTML pages."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from raioxia.api.deps import db_session
from raioxia.api.routes import by_report_token, report_payload
from raioxia.web.pages import message, page

router = APIRouter()


@router.get("/r/{token}")
def report_page(token: str, request: Request, session: Session = Depends(db_session)):
    analysis = by_report_token(session, token)
    if analysis is None:
        return message(request, 404, "Relatório não encontrado", "Confira se o endereço do relatório está completo.")
    return page(request, "report.html", noindex=True, nav="report", report=report_payload(analysis), analysis=analysis)
