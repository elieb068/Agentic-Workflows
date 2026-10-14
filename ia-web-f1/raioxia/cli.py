"""Command line: python -m raioxia analyze <url> | migrate | worker <queue>"""

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
    return parser


def main(argv: list[str] | None = None, fetcher_factory: Callable[[], SafeFetcher] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        return migrate()
    if args.command == "worker":
        from raioxia.jobs import run_worker

        run_worker(args.queue)
        return 0
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
