"""What a sender receives, the sender protocol, MIME building and the dev ConsoleSender."""

import logging
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    html: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class EmailError(Exception):
    """transient=True: worth retrying (network, quota, 5xx). False: the message can never be sent."""

    def __init__(self, message: str, *, transient: bool):
        super().__init__(message)
        self.transient = transient


class EmailSender(Protocol):
    def send(self, email: OutgoingEmail) -> str:
        """Send one message; return the provider's message id. Raise EmailError on failure."""
        ...


def build_mime(email: OutgoingEmail, *, from_name: str, from_address: str) -> MimeMessage:
    msg = MimeMessage()
    local, _, domain = from_address.partition("@")
    msg["From"] = Address(display_name=from_name, username=local, domain=domain)
    msg["To"] = email.to
    msg["Reply-To"] = from_address
    msg["Subject"] = email.subject
    msg["Message-ID"] = make_msgid(domain=domain)
    for name, value in email.headers.items():
        msg[name] = value
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")
    return msg


class ConsoleSender:
    """Development and tests: logs the message and keeps it in memory."""

    def __init__(self, *, from_name: str = "Raio-X IA", from_address: str = "raiox@primesolutions.net.br"):
        self.from_name = from_name
        self.from_address = from_address
        self.sent: list[OutgoingEmail] = []

    def send(self, email: OutgoingEmail) -> str:
        mime = build_mime(email, from_name=self.from_name, from_address=self.from_address)
        self.sent.append(email)
        log.info("e-mail (console)", extra={"to": email.to, "subject": email.subject, "text": email.text})
        return mime["Message-ID"]
