"""What the routes need, read from app.state (set by create_app, replaced in tests)."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session, sessionmaker

from raioxia.api.client_ip import client_ip
from raioxia.settings import Settings


@dataclass
class AppDeps:
    settings: Settings
    sessions: sessionmaker[Session]
    redis: Redis
    queues: dict[str, Queue]
    clock: Callable[[], datetime]


def deps(request: Request) -> AppDeps:
    return request.app.state.deps


def db_session(request: Request) -> Iterator[Session]:
    session = deps(request).sessions()
    try:
        yield session
    finally:
        session.close()


def request_ip(request: Request) -> str | None:
    return client_ip(request, deps(request).settings.trusted_proxies)
