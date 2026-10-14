from datetime import UTC, datetime, timedelta

import fakeredis

from raioxia.api.ratelimit import Limits, RateLimiter, Window, load_limits

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_approved_limits():
    limits = load_limits()
    assert [(w.window, w.max) for w in limits.ip] == [(3600, 3), (86400, 10)]
    assert [(w.window, w.max) for w in limits.email] == [(86400, 2)]
    assert [(w.window, w.max) for w in limits.domain] == [(86400, 10)]


def test_fixed_windows_and_hashed_keys():
    redis = fakeredis.FakeStrictRedis()
    limiter = RateLimiter(redis, Limits(ip=[Window(window=3600, max=2), Window(window=86400, max=3)], email=[], domain=[]))
    assert [limiter.hit("ip", "203.0.113.9", NOW) for _ in range(3)] == [True, True, False]
    assert limiter.hit("ip", "198.51.100.1", NOW)  # other IPs are independent
    later = NOW.replace(hour=13)
    assert not limiter.hit("ip", "203.0.113.9", later)  # hourly window reset, but the daily max (3) is reached
    keys = [k.decode() for k in redis.keys("*")]
    assert keys and not any("203.0.113.9" in k for k in keys)
    assert all(0 < redis.ttl(k) <= 86400 for k in keys)


def test_email_scope_counts_per_address():
    limiter = RateLimiter(fakeredis.FakeStrictRedis(), Limits(ip=[], email=[Window(window=86400, max=2)], domain=[]))
    assert [limiter.hit("email", "ana@exemplo.com.br", NOW + timedelta(minutes=i)) for i in range(3)] == [True, True, False]
    assert limiter.hit("email", "bia@exemplo.com.br", NOW)
