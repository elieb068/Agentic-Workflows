"""Unguessable tokens and signed links.

- Random tokens (confirmation, report): 256 bits from `secrets`. The confirmation
  token is stored only as its SHA-256 hash.
- Signed links (unsubscribe, data deletion): `<lead_id>.<expires>.<hmac>`, HMAC-SHA256
  over purpose + lead id + expiry with SECRET_KEY, so no table is needed. expires=0
  means no expiry (unsubscribe links must keep working).
"""

import hashlib
import hmac
import secrets
from datetime import datetime


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _mac(secret: str, purpose: str, lead_id: int, expires: int) -> str:
    message = f"{purpose}:{lead_id}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def sign(secret: str, purpose: str, lead_id: int, expires_at: datetime | None = None) -> str:
    expires = int(expires_at.timestamp()) if expires_at else 0
    return f"{lead_id}.{expires}.{_mac(secret, purpose, lead_id, expires)}"


def verify(secret: str, purpose: str, token: str, now: datetime) -> int | None:
    """The lead id if the token is authentic, for this purpose and not expired; else None."""
    try:
        lead_part, expires_part, mac = token.split(".")
        lead_id, expires = int(lead_part), int(expires_part)
    except ValueError:
        return None
    if not hmac.compare_digest(mac, _mac(secret, purpose, lead_id, expires)):
        return None
    if expires and now.timestamp() > expires:
        return None
    return lead_id
