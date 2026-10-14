"""Redis connection and the two RQ queues: "analysis" (Playwright, slow) and "email" (separate worker)."""

from functools import lru_cache

from redis import Redis
from rq import Queue

from raioxia.settings import get_settings

ANALYSIS_QUEUE = "analysis"
EMAIL_QUEUE = "email"


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_queue(name: str, connection: Redis | None = None) -> Queue:
    return Queue(name, connection=connection or get_redis())
