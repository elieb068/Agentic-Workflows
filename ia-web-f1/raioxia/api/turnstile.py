"""Cloudflare Turnstile server-side check. Fixed Cloudflare host, so plain httpx (not the SafeFetcher).

Fails closed: if Cloudflare can't be reached, the submission is refused (the user can retry).
"""

import logging

import httpx

from raioxia.settings import Settings

log = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier:
    def __init__(self, secret_key: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 5.0):
        self.secret_key = secret_key
        self.client = httpx.Client(transport=transport, timeout=timeout)

    @classmethod
    def from_settings(cls, settings: Settings) -> "TurnstileVerifier":
        return cls(settings.turnstile_secret_key.get_secret_value())

    def __call__(self, token: str, ip: str | None) -> bool:
        if not token:
            return False
        data = {"secret": self.secret_key, "response": token}
        if ip:
            data["remoteip"] = ip
        try:
            response = self.client.post(SITEVERIFY_URL, data=data)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("turnstile siteverify unavailable", extra={"error": str(exc)})
            return False
        if not result.get("success"):
            log.info("turnstile rejected", extra={"codes": result.get("error-codes")})
        return bool(result.get("success"))
