"""The submission rules, shared by the JSON API and the HTML form so both behave the same.

Order: Turnstile -> IP limit -> validation -> e-mail limit (silent) -> domain limit -> record + e-mail.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from raioxia import pipeline, privacy
from raioxia.api.deps import AppDeps
from raioxia.email.outbox import DELETION_LINK_TTL, dispatch, queue_confirm_email, queue_deletion_email
from raioxia.pipeline import SubmissionError
from raioxia.queues import EMAIL_QUEUE
from raioxia.tokens import sign

log = logging.getLogger(__name__)

ACCEPTED_MESSAGE = "Pronto! Enviamos um link de confirmação para o seu e-mail. A análise começa assim que você clicar nele."
TURNSTILE_MESSAGE = "Não conseguimos confirmar que você não é um robô. Recarregue a página e tente de novo."
IP_LIMIT_MESSAGE = "Você fez muitos pedidos em pouco tempo. Tente de novo mais tarde."
DOMAIN_LIMIT_MESSAGE = "Esse site já foi pedido muitas vezes hoje. Tente de novo amanhã."

# Which form field a refusal belongs to (None: a message for the whole form).
FIELD_OF_ERROR = {"invalid_url": "url", "blocked": "url", "opted_out": "url", "invalid_email": "email", "terms_required": "accepted_terms"}
DELETION_ACCEPTED_MESSAGE = "Se esse e-mail estiver no Raio-X IA, enviamos um link para confirmar a exclusão dos dados."


@dataclass(frozen=True)
class Outcome:
    status: int  # HTTP status the JSON API answers with
    code: str  # "accepted" or an error code
    message: str
    email: str | None = None  # normalized, for the "check your e-mail" page
    domain: str | None = None

    @property
    def accepted(self) -> bool:
        return self.code == "accepted"

    @property
    def field(self) -> str | None:
        return FIELD_OF_ERROR.get(self.code)


def process_submission(
    session: Session,
    app: AppDeps,
    *,
    url: str,
    email: str,
    accepted_terms: bool,
    newsletter: bool,
    turnstile_token: str,
    ip: str | None,
    user_agent: str | None,
) -> Outcome:
    now = app.clock()
    if not app.turnstile(turnstile_token, ip):
        return Outcome(403, "turnstile_failed", TURNSTILE_MESSAGE)
    if not app.limiter.hit("ip", ip or "unknown", now):
        log.info("submission rate limited", extra={"scope": "ip"})
        return Outcome(429, "rate_limited", IP_LIMIT_MESSAGE)
    try:
        valid = pipeline.validate_submission(url=url, email=email, accepted_terms=accepted_terms)
    except SubmissionError as exc:
        return Outcome(422, exc.code, exc.message)
    accepted = Outcome(202, "accepted", ACCEPTED_MESSAGE, email=valid.email, domain=valid.domain)
    if not app.limiter.hit("email", valid.email, now):
        log.info("submission rate limited", extra={"scope": "email"})
        return accepted  # same answer as a real submission: don't reveal anything about the address
    if not app.limiter.hit("domain", valid.domain, now):
        log.info("submission rate limited", extra={"scope": "domain", "domain": valid.domain})
        return Outcome(429, "rate_limited", DOMAIN_LIMIT_MESSAGE)

    submission = pipeline.submit(
        session, url=valid.url, email=valid.email, accepted_terms=accepted_terms, newsletter=newsletter,
        ip=ip, user_agent=user_agent, now=now,
    )
    row = queue_confirm_email(session, submission.analysis, submission.confirm_token, base_url=app.settings.base_url)
    session.commit()
    dispatch(app.queues[EMAIL_QUEUE], [row.id])
    return accepted


def process_deletion_request(session: Session, app: AppDeps, *, email: str, turnstile_token: str, ip: str | None) -> Outcome:
    """Same answer whether or not the address is known; the link goes only to the address itself."""
    now = app.clock()
    if not app.turnstile(turnstile_token, ip):
        return Outcome(403, "turnstile_failed", TURNSTILE_MESSAGE)
    if not app.limiter.hit("ip", f"deletion:{ip or 'unknown'}", now):  # own counter, same limits as submissions
        return Outcome(429, "rate_limited", IP_LIMIT_MESSAGE)
    try:
        address = pipeline.normalize_email(email)
    except SubmissionError as exc:
        return Outcome(422, exc.code, exc.message)
    accepted = Outcome(202, "accepted", DELETION_ACCEPTED_MESSAGE, email=address)
    lead = privacy.find_lead(session, address) if app.limiter.hit("email", f"deletion:{address}", now) else None
    if lead is None:
        return accepted
    token = sign(app.settings.secret_key.get_secret_value(), "delete", lead.id, now + DELETION_LINK_TTL)
    row = queue_deletion_email(session, lead, token, base_url=app.settings.base_url)
    session.commit()
    dispatch(app.queues[EMAIL_QUEUE], [row.id])
    return accepted
