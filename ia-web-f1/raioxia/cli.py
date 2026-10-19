"""Command line: python -m raioxia analyze <url> | migrate | worker <queue> | purge | stats | email-test <to> | indexnow"""

import argparse
import asyncio
import json
import sys
from collections.abc import Callable

from raioxia.checkers import REGISTRY
from raioxia.net.fetcher import SafeFetcher
from raioxia.runner import AnalysisError, analyze


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="raioxia", description="Raio-X IA: AI visibility checker")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("analyze", help="analyze a site's home page and print the report as JSON")
    cmd.add_argument("url", help="site address, e.g. example.com or https://example.com")
    cmd.add_argument("--no-render", action="store_true", help="skip the Playwright render (check 5)")
    cmd.add_argument("--pretty", action="store_true", help="indent the JSON output")
    cmd.add_argument("--only", help=f"comma-separated check ids to run ({', '.join(REGISTRY)})")
    sub.add_parser("migrate", help="apply database migrations (DATABASE_URL) up to the latest revision")
    worker = sub.add_parser("worker", help="run an RQ worker for one queue (analysis or email)")
    worker.add_argument("queue", choices=("analysis", "email"))
    sub.add_parser("purge", help="daily maintenance: expire, apply retention (LGPD), re-dispatch stuck e-mails")
    stats = sub.add_parser("stats", help="daily requests, confirmation rate and newsletter opt-ins")
    stats.add_argument("--days", type=int, default=30)
    email_test = sub.add_parser("email-test", help="send one test e-mail through the configured sender (setup check)")
    email_test.add_argument("to")
    indexnow = sub.add_parser("indexnow", help="tell Bing & co. which public pages changed today (needs INDEXNOW_KEY)")
    indexnow.add_argument("--all", action="store_true", help="submit every page in the sitemap")
    return parser


def main(argv: list[str] | None = None, fetcher_factory: Callable[[], SafeFetcher] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        return migrate()
    if args.command == "worker":
        from raioxia.jobs import run_worker

        run_worker(args.queue)
        return 0
    if args.command == "purge":
        return purge()
    if args.command == "stats":
        return stats(args.days)
    if args.command == "email-test":
        return email_test(args.to)
    if args.command == "indexnow":
        return indexnow(submit_all=args.all)
    checkers = None
    if args.only:
        ids = [i.strip() for i in args.only.split(",") if i.strip()]
        unknown = [i for i in ids if i not in REGISTRY]
        if unknown:
            print(f"unknown check ids: {', '.join(unknown)}", file=sys.stderr)
            return 2
        checkers = [REGISTRY[i]() for i in ids]

    async def run():
        async with fetcher_factory() if fetcher_factory else SafeFetcher() as fetcher:
            return await analyze(args.url, fetcher=fetcher, checkers=checkers, render=not args.no_render)

    try:
        report = asyncio.run(run())
    except AnalysisError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(report.model_dump_json(indent=2 if args.pretty else None))
    return 0


def migrate() -> int:
    from raioxia.db.migrate import upgrade  # pipeline-only deps and settings: not needed by `analyze`

    upgrade()
    return 0


def purge() -> int:
    import json
    from datetime import UTC, datetime

    from raioxia import newsletter, privacy
    from raioxia.db.engine import session_scope
    from raioxia.email.outbox import requeue_stale
    from raioxia.queues import EMAIL_QUEUE, get_queue

    now = datetime.now(UTC)
    with session_scope() as session:
        counts = privacy.purge(session, now=now)
    with session_scope() as session:
        counts["emails_requeued"] = requeue_stale(session, get_queue(EMAIL_QUEUE), now=now)
    try:
        counts["brevo"] = newsletter.sync_job()  # safety net for missed or failed sync jobs
    except newsletter.BrevoError as exc:
        counts["brevo"] = str(exc)
    print(json.dumps(counts))
    return 0


def stats(days: int) -> int:
    from datetime import UTC, datetime

    from raioxia import stats as metrics
    from raioxia.db.engine import session_scope

    with session_scope() as session:
        print(metrics.format_table(metrics.daily(session, now=datetime.now(UTC), days=days)))
    return 0


def email_test(to: str) -> int:
    """Bypasses the outbox on purpose: checks credentials, delegation and the From alias right now."""
    from html import escape

    from raioxia.email.base import EmailError, OutgoingEmail
    from raioxia.email.jobs import get_sender
    from raioxia.settings import get_settings

    settings = get_settings()
    paragraphs = [
        "Este é um e-mail de teste do Raio-X IA.",
        f"Remetente configurado: {settings.email_from_name} <{settings.email_from_address}>. Envio: {settings.email_sender}.",
        "Confira se o remetente aparece exatamente assim (e não como o endereço principal da caixa).",
    ]
    text = "\n\n".join(paragraphs)
    html = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
    try:
        message_id = get_sender().send(OutgoingEmail(to=to, subject="Teste de envio do Raio-X IA", html=html, text=text))
    except EmailError as exc:
        print(f"failed ({'transient' if exc.transient else 'permanent'}): {exc}", file=sys.stderr)
        return 1
    print(f"sent: {message_id}")
    return 0


def indexnow(submit_all: bool = False) -> int:
    from raioxia.settings import get_settings
    from raioxia.web import indexnow as index_now, seo

    settings = get_settings()
    if not settings.indexnow_key:
        print("INDEXNOW_KEY not set: nothing sent")
        return 0
    paths = list(seo.sitemap_pages()) if submit_all else index_now.changed_paths()
    if not paths:
        print("no page changed today: nothing sent")
        return 0
    try:
        status = index_now.submit(settings.base_url, settings.indexnow_key, paths)
    except Exception as exc:  # network trouble must not fail a deploy
        print(f"indexnow failed: {exc}", file=sys.stderr)
        return 1
    print(f"indexnow: {len(paths)} URL(s), HTTP {status}")
    return 0 if status in (200, 202) else 1
