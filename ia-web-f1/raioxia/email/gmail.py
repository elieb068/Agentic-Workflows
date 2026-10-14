"""Gmail API sender: Workspace service account with domain-wide delegation, scope gmail.send only.

The service account impersonates GMAIL_DELEGATED_USER (eric.lieb@primesolutions.net.br) and sends
From the alias EMAIL_FROM_ADDRESS (raiox@primesolutions.net.br), which must be one of that
mailbox's "Send mail as" addresses, or Gmail silently rewrites From to the primary address.
gmail.send can't read that list; `python -m raioxia email-test <to>` sends a real message to check.

google-auth's token refresh runs over httpx through a small transport adapter (no `requests`).
"""

import base64
import logging
from collections.abc import Mapping

import google.auth.transport
import httpx
from google.auth.exceptions import GoogleAuthError, TransportError
from google.oauth2 import service_account

from raioxia.email.base import EmailError, OutgoingEmail, build_mime
from raioxia.settings import Settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# 403 reasons that mean "slow down / try later" rather than "never".
_TRANSIENT_403 = {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "quotaExceeded"}


class _Response(google.auth.transport.Response):
    def __init__(self, response: httpx.Response):
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    @property
    def data(self) -> bytes:
        return self._response.content


class HttpxAuthRequest(google.auth.transport.Request):
    """google-auth transport over httpx (used only to fetch OAuth tokens)."""

    def __init__(self, client: httpx.Client):
        self.client = client

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        try:
            response = self.client.request(method, url, content=body, headers=headers, timeout=timeout or 10.0)
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc
        return _Response(response)


class GmailApiSender:
    def __init__(self, credentials, *, from_name: str, from_address: str, transport: httpx.BaseTransport | None = None):
        self.credentials = credentials
        self.from_name = from_name
        self.from_address = from_address
        self.client = httpx.Client(transport=transport, timeout=httpx.Timeout(20.0, connect=5.0))

    @classmethod
    def from_settings(cls, settings: Settings) -> "GmailApiSender":
        credentials = service_account.Credentials.from_service_account_file(
            str(settings.gmail_service_account_file), scopes=SCOPES, subject=settings.gmail_delegated_user
        )
        return cls(credentials, from_name=settings.email_from_name, from_address=settings.email_from_address)

    def _token(self) -> str:
        if not self.credentials.valid:
            try:
                self.credentials.refresh(HttpxAuthRequest(self.client))
            except TransportError as exc:
                raise EmailError(f"gmail auth unreachable: {exc}", transient=True) from exc
            except GoogleAuthError as exc:
                # e.g. unauthorized_client: delegation or scope not set up in the Admin console.
                # Retried (so a fixed config recovers queued mail), and logged loudly.
                log.error("gmail auth refused: check domain-wide delegation", extra={"error": str(exc)})
                raise EmailError(f"gmail auth refused: {exc}", transient=True) from exc
        return self.credentials.token

    def send(self, email: OutgoingEmail) -> str:
        mime = build_mime(email, from_name=self.from_name, from_address=self.from_address)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        try:
            response = self.client.post(SEND_URL, json={"raw": raw}, headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as exc:
            raise EmailError(f"gmail unreachable: {exc}", transient=True) from exc
        if response.status_code == 200:
            return response.json().get("id", "")
        reason = _reason(response)
        detail = f"gmail {response.status_code} {reason}".strip()
        if response.status_code == 401:
            self.credentials.token = None  # force a refresh on the next attempt
            raise EmailError(detail, transient=True)
        if response.status_code == 429 or response.status_code >= 500 or reason in _TRANSIENT_403:
            raise EmailError(detail, transient=True)
        raise EmailError(detail, transient=False)  # 400 bad recipient/message, 403 forbidden, ...


def _reason(response: httpx.Response) -> str:
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return ""
    details = error.get("errors") or [{}]
    return details[0].get("reason") or error.get("status") or ""
