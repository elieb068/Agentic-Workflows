"""Pipeline domain logic shared by the API and the workers (sync, one Session per call).

Lifecycle of an analysis:
    pending_confirmation --confirm--> queued --job--> running --> done | failed
                         \--confirm, same domain done < 24h--> done (results copied)
                         \--24h without confirmation--> expired
"""

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import httpx
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from raioxia.db import Analysis, CheckResultRow, Consent, Lead
from raioxia.models import AnalysisReport
from raioxia.net.ssrf import is_public_ip
from raioxia.runner import AnalysisError, normalize_url
from raioxia.tokens import hash_token, new_token

log = logging.getLogger(__name__)

CONFIRM_TTL = timedelta(hours=24)
REUSE_WINDOW = timedelta(hours=24)
# Version of the consent texts shown on the form (M3 holds the texts; bump both together).
CONSENT_TEXT_VERSION = "2026-10-14"


class SubmissionError(Exception):
    """A request we refuse. `message` is user-facing (PT-BR)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def normalize_email(raw: str) -> str:
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise SubmissionError("invalid_email", "Esse e-mail não parece válido. Confira e tente de novo.") from exc


@dataclass(frozen=True)
class Submission:
    analysis: Analysis
    confirm_token: str  # only ever in memory: goes into the confirmation e-mail


def submit(
    session: Session,
    *,
    url: str,
    email: str,
    accepted_terms: bool,
    newsletter: bool,
    ip: str | None,
    user_agent: str | None,
    now: datetime,
) -> Submission:
    """Record the lead, its consents and a pending analysis. Nothing runs until the e-mail is confirmed."""
    if not accepted_terms:
        raise SubmissionError("terms_required", "Para receber o relatório, é preciso aceitar os termos e a política de privacidade.")
    try:
        normalized = normalize_url(url)
    except AnalysisError as exc:
        raise SubmissionError(exc.code, exc.message) from exc
    domain = _host(normalized.url)
    if _is_internal_literal(domain):  # hostnames are checked when the worker resolves them
        raise SubmissionError("blocked", "Não podemos analisar esse endereço.")
    address = normalize_email(email)

    lead = session.scalar(select(Lead).where(Lead.email == address))
    if lead is None:
        lead = Lead(email=address)
        session.add(lead)
    session.add(Consent(lead=lead, kind="report_terms", granted=True, text_version=CONSENT_TEXT_VERSION, ip=ip, user_agent=user_agent))
    if newsletter:
        session.add(Consent(lead=lead, kind="newsletter", granted=True, text_version=CONSENT_TEXT_VERSION, ip=ip, user_agent=user_agent))

    token = new_token()
    analysis = Analysis(
        lead=lead,
        url=normalized.url,
        domain=domain,
        confirm_token_hash=hash_token(token),
        confirm_expires_at=now + CONFIRM_TTL,
        report_token=new_token(),
        requested_ip=ip,
        newsletter_opt_in=newsletter,
    )
    session.add(analysis)
    session.flush()
    log.info("analysis submitted", extra={"analysis_id": analysis.id, "domain": analysis.domain})
    return Submission(analysis, token)


def _is_internal_literal(host: str) -> bool:
    try:
        return not is_public_ip(ipaddress.ip_address(host))
    except ValueError:
        return False


def _host(url: str) -> str:
    return httpx.URL(url).host  # lowercase, no port; IPv6 without brackets


class Confirmation(StrEnum):
    QUEUED = "queued"  # caller must enqueue the analysis job
    REUSED = "reused"  # results copied from a recent analysis: caller sends the report e-mail
    ALREADY = "already"  # link clicked again: just show the report
    EXPIRED = "expired"
    INVALID = "invalid"


def confirm(session: Session, token: str, *, now: datetime) -> tuple[Confirmation, Analysis | None]:
    analysis = session.scalar(select(Analysis).where(Analysis.confirm_token_hash == hash_token(token)).with_for_update())
    if analysis is None:
        return Confirmation.INVALID, None
    if analysis.status != "pending_confirmation":
        return (Confirmation.EXPIRED if analysis.status == "expired" else Confirmation.ALREADY), analysis
    if analysis.confirm_expires_at is not None and now > analysis.confirm_expires_at:
        analysis.status = "expired"
        return Confirmation.EXPIRED, analysis

    analysis.confirmed_at = now
    lead = analysis.lead
    if lead is not None:
        lead.confirmed_at = lead.confirmed_at or now
        if analysis.newsletter_opt_in:  # double opt-in: only now does the subscription start
            lead.newsletter = True
            lead.unsubscribed_at = None

    source = _recent_done(session, analysis.domain, now)
    if source is not None:
        _copy_results(source, analysis, now)
        log.info("analysis reused", extra={"analysis_id": analysis.id, "reused_from_id": analysis.reused_from_id})
        return Confirmation.REUSED, analysis
    analysis.status = "queued"
    return Confirmation.QUEUED, analysis


def _recent_done(session: Session, domain: str, now: datetime) -> Analysis | None:
    return session.scalar(
        select(Analysis)
        .where(Analysis.domain == domain, Analysis.status == "done", Analysis.finished_at >= now - REUSE_WINDOW)
        .order_by(Analysis.finished_at.desc())
        .limit(1)
    )


def _copy_results(source: Analysis, target: Analysis, now: datetime) -> None:
    target.reused_from_id = source.reused_from_id or source.id  # always point at the analysis that really ran
    for field in ("score", "band_id", "band_label", "final_url", "version", "duration_ms"):
        setattr(target, field, getattr(source, field))
    target.results = [
        CheckResultRow(
            check_id=r.check_id, position=r.position, status=r.status, titulo=r.titulo,
            explicacao=r.explicacao, como_corrigir=r.como_corrigir, evidencia=r.evidencia, peso=r.peso,
        )
        for r in source.results
    ]
    target.status = "done"
    target.finished_at = now


def start(session: Session, analysis_id: int) -> Analysis | None:
    """Claim a queued analysis for the worker; None if it is gone or not queued (e.g. a duplicate job)."""
    analysis = session.get(Analysis, analysis_id, with_for_update=True)
    if analysis is None or analysis.status not in ("queued", "running"):  # running: a retry after a crash
        return None
    analysis.status = "running"
    return analysis


def finish(session: Session, analysis: Analysis, report: AnalysisReport, *, now: datetime) -> None:
    analysis.score = report.score
    analysis.band_id = report.band.id if report.band else None
    analysis.band_label = report.band.label if report.band else None
    analysis.final_url = report.final_url
    analysis.version = report.version
    analysis.duration_ms = report.duration_ms
    analysis.results = [
        CheckResultRow(
            check_id=c.id, position=i, status=c.status.value, titulo=c.titulo, explicacao=c.explicacao,
            como_corrigir=c.como_corrigir, evidencia=c.evidencia, peso=c.peso,
        )
        for i, c in enumerate(report.checks)
    ]
    analysis.status = "done"
    analysis.finished_at = now


def fail(session: Session, analysis: Analysis, code: str, message: str, *, now: datetime) -> None:
    analysis.status = "failed"
    analysis.error_code = code
    analysis.error_message = message
    analysis.finished_at = now


def expire_unconfirmed(session: Session, *, now: datetime) -> int:
    result = session.execute(
        update(Analysis)
        .where(Analysis.status == "pending_confirmation", Analysis.confirm_expires_at < now)
        .values(status="expired")
    )
    return result.rowcount
