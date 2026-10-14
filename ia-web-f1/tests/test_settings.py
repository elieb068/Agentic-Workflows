from ipaddress import IPv4Network

import pytest
from pydantic import ValidationError

from raioxia.settings import TURNSTILE_TEST_SECRET_KEY, Settings

SECRET = "x" * 40


def make(**values) -> Settings:
    return Settings(_env_file=None, **values)  # never read a developer's .env in tests


def test_defaults_are_safe_for_dev():
    s = make(secret_key=SECRET)
    assert s.email_sender == "console"
    assert s.email_from_address == "raiox@primesolutions.net.br"
    assert s.turnstile_secret_key.get_secret_value() == TURNSTILE_TEST_SECRET_KEY
    assert "127.0.0.1:15432" in s.database_url


def test_secret_key_is_required_and_long():
    with pytest.raises(ValidationError):
        make()
    with pytest.raises(ValidationError):
        make(secret_key="short")


def test_gmail_sender_needs_service_account_file(tmp_path):
    with pytest.raises(ValidationError, match="GMAIL_SERVICE_ACCOUNT_FILE"):
        make(secret_key=SECRET, email_sender="gmail")
    key = tmp_path / "sa.json"
    assert make(secret_key=SECRET, email_sender="gmail", gmail_service_account_file=key).gmail_service_account_file == key


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("APP_BASE_URL", "https://raiox.primesolutions.net.br/")
    monkeypatch.setenv("TRUSTED_PROXIES", '["172.18.0.0/16"]')
    s = Settings(_env_file=None)
    assert s.base_url == "https://raiox.primesolutions.net.br"
    assert s.trusted_proxies == [IPv4Network("172.18.0.0/16")]
    assert s.secret_key.get_secret_value() not in repr(s)
