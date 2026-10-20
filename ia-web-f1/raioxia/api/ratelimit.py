"""Fixed-window counters in Redis, limits from config/limits.yaml."""

import hashlib
import os
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from redis import Redis

from raioxia.config import DEFAULT_CONFIG_DIR


class Window(BaseModel):
    window: int = Field(gt=0)  # seconds
    max: int = Field(gt=0)


class Limits(BaseModel):
    ip: list[Window]
    email: list[Window]
    domain: list[Window]
    # Free instant tools (no e-mail): looser per-IP limit, plus a per-site cap so nobody hammers one site through us.
    tool_ip: list[Window] = Field(default_factory=lambda: [Window(window=3600, max=20), Window(window=86400, max=60)])
    tool_domain: list[Window] = Field(default_factory=lambda: [Window(window=3600, max=10)])


def load_limits(config_dir: Path | None = None) -> Limits:
    config_dir = config_dir or Path(os.environ.get("RAIOXIA_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    with (config_dir / "limits.yaml").open(encoding="utf-8") as f:
        return Limits.model_validate(yaml.safe_load(f))


class RateLimiter:
    def __init__(self, redis: Redis, limits: Limits, prefix: str = "rl"):
        self.redis = redis
        self.limits = limits
        self.prefix = prefix

    def hit(self, scope: str, value: str, now: datetime) -> bool:
        """Count one request for `value` in every window of `scope`; False if any window is over its max.

        Values are hashed: no e-mail addresses or IPs end up in Redis keys.
        """
        digest = hashlib.sha256(value.encode()).hexdigest()[:32]
        ts = int(now.timestamp())
        windows: list[Window] = getattr(self.limits, scope)
        pipe = self.redis.pipeline()
        for w in windows:
            key = f"{self.prefix}:{scope}:{w.window}:{ts // w.window}:{digest}"
            pipe.incr(key)
            pipe.expire(key, w.window, nx=True)
        counts = pipe.execute()[::2]
        return all(count <= w.max for count, w in zip(counts, windows, strict=True))
