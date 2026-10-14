"""Turns an outbox row (kind + context) into an OutgoingEmail. Links that need the secret are built here."""

from datetime import datetime

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from raioxia.email.base import OutgoingEmail
from raioxia.settings import Settings
from raioxia.tokens import sign

_env = Environment(
    loader=PackageLoader("raioxia.email", "templates"),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    undefined=StrictUndefined,  # a missing context key is a bug, not an empty string
    keep_trailing_newline=True,
)

# The deletion confirmation goes to someone leaving: no unsubscribe link there.
_NO_UNSUBSCRIBE = {"deletion"}


def unsubscribe_url(settings: Settings, lead_id: int) -> str:
    return f"{settings.base_url}/descadastrar/{sign(settings.secret_key.get_secret_value(), 'unsubscribe', lead_id)}"


def render(kind: str, context: dict, *, to: str, lead_id: int | None, settings: Settings, now: datetime) -> OutgoingEmail:
    unsubscribe = unsubscribe_url(settings, lead_id) if lead_id is not None and kind not in _NO_UNSUBSCRIBE else None
    values = {
        **context,
        "base_url": settings.base_url,
        "privacy_url": f"{settings.base_url}/privacidade",
        "unsubscribe_url": unsubscribe,
        "year": now.year,
    }
    subject = " ".join(_env.get_template(f"{kind}.subject.txt").render(values).split())
    values["subject"] = subject
    headers = {}
    if unsubscribe:
        headers["List-Unsubscribe"] = f"<{unsubscribe}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return OutgoingEmail(
        to=to,
        subject=subject,
        html=_env.get_template(f"{kind}.html").render(values),
        text=_env.get_template(f"{kind}.txt").render(values),
        headers=headers,
    )
