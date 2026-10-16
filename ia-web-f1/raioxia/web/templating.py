"""Jinja2 templates for every HTML page, with the globals the layout needs."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from raioxia.pipeline import CONSENT_TEXT_VERSION
from raioxia.web.assets import TEMPLATES_DIR, static_url

TIMEZONE = ZoneInfo("America/Sao_Paulo")

# Who is responsible for the data (privacy policy, terms, footer). Approved 2026-10-15.
COMPANY = {
    "product": "Raio-X IA",
    "brand": "Prime Solutions",
    "legal_name": "Prime Solutions Tecnologia e Serviços de Apoio Administrativo",
    "cnpj": "43.757.807/0001-86",
    "contact_email": "raiox@primesolutions.net.br",
    "website": "https://www.primesolutions.net.br",
}
NEWSLETTER = {"name": "Raio-X IA Semanal", "promise": "o que mudou nas IAs e como isso afeta o seu site"}


def local_datetime(value: datetime | None, fmt: str = "%d/%m/%Y às %H:%M") -> str:
    return value.astimezone(TIMEZONE).strftime(fmt) if value else ""


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals.update(
    static_url=static_url,
    company=COMPANY,
    newsletter=NEWSLETTER,
    consent_version=CONSENT_TEXT_VERSION,
)
templates.env.filters["local_datetime"] = local_datetime


def nonce_attr(ads: dict | None) -> Markup:
    """` nonce="..."` for script tags on pages with ads (their CSP trusts only nonced scripts)."""
    return Markup(f' nonce="{ads["nonce"]}"') if ads else Markup("")


templates.env.globals["nonce_attr"] = nonce_attr
