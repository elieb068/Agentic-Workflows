from datetime import UTC, datetime, timedelta

from raioxia.tokens import hash_token, new_token, sign, verify

SECRET = "s" * 40
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_random_tokens_are_long_unique_and_urlsafe():
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 and t.replace("-", "").replace("_", "").isalnum() for t in tokens)


def test_hash_is_sha256_hex():
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_signed_link_roundtrip_without_expiry():
    token = sign(SECRET, "unsubscribe", 42)
    assert verify(SECRET, "unsubscribe", token, NOW + timedelta(days=3650)) == 42


def test_signed_link_expires():
    token = sign(SECRET, "delete", 7, NOW + timedelta(hours=24))
    assert verify(SECRET, "delete", token, NOW) == 7
    assert verify(SECRET, "delete", token, NOW + timedelta(hours=25)) is None


def test_signed_link_rejects_tampering_other_purpose_and_other_secret():
    token = sign(SECRET, "unsubscribe", 42)
    lead, expires, mac = token.split(".")
    assert verify(SECRET, "unsubscribe", f"43.{expires}.{mac}", NOW) is None
    assert verify(SECRET, "delete", token, NOW) is None
    assert verify("t" * 40, "unsubscribe", token, NOW) is None
    for garbage in ("", "x", "1.2", "a.b.c", "1.2.3.4"):
        assert verify(SECRET, "unsubscribe", garbage, NOW) is None
