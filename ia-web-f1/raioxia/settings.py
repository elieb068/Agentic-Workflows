"""Runtime settings for the pipeline (API, workers), read from the environment / .env.

The M1 analysis core keeps reading its own few variables (FETCHER_USER_AGENT,
CHROMIUM_EXECUTABLE, RAIOXIA_CONFIG_DIR) so the CLI works without any of this.
"""

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloudflare's documented Turnstile test keys: this pair always passes.
TURNSTILE_TEST_SITE_KEY = "1x00000000000000000000AA"
TURNSTILE_TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_base_url: str = "http://localhost:8000"
    # Signs unsubscribe/deletion links. Required: generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    secret_key: SecretStr = Field(min_length=32)

    database_url: str = "postgresql+psycopg://raioxia:raioxia@127.0.0.1:15432/raioxia"
    redis_url: str = "redis://127.0.0.1:16379/0"

    turnstile_site_key: str = TURNSTILE_TEST_SITE_KEY
    turnstile_secret_key: SecretStr = SecretStr(TURNSTILE_TEST_SECRET_KEY)

    # Proxies allowed to set X-Forwarded-For (Caddy reaches the API over a Docker network).
    trusted_proxies: list[IPv4Network | IPv6Network] = Field(
        default_factory=lambda: [IPv4Network("127.0.0.0/8"), IPv4Network("172.16.0.0/12"), IPv6Network("::1/128")]
    )

    email_sender: Literal["console", "gmail"] = "console"
    email_from_address: str = "raiox@primesolutions.net.br"
    email_from_name: str = "Raio-X IA"
    # Gmail API: Workspace service account with domain-wide delegation (scope gmail.send only),
    # impersonating this mailbox. The from address must be one of its "Send mail as" aliases.
    gmail_delegated_user: str = "eric.lieb@primesolutions.net.br"
    gmail_service_account_file: Path | None = None

    # Google AdSense, report page only, non-personalized ads. Without a client the report shows
    # dashed placeholders (dev); with a client but no slot IDs (before approval) it shows nothing.
    adsense_client: str | None = None  # ca-pub-...
    adsense_slot_top: str | None = None
    adsense_slot_bottom: str | None = None

    # Public IP our fetcher connects from, shown on the /bot page so site owners can recognize it.
    server_ip: str | None = None

    log_level: str = "INFO"
    log_json: bool = True

    @model_validator(mode="after")
    def _gmail_needs_key(self) -> Self:
        if self.email_sender == "gmail" and self.gmail_service_account_file is None:
            raise ValueError("EMAIL_SENDER=gmail needs GMAIL_SERVICE_ACCOUNT_FILE")
        return self

    @property
    def base_url(self) -> str:
        return self.app_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
