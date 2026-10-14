"""The analysis job (queue "analysis"): run the M1 runner, store the report, queue the result e-mail."""

import asyncio
import logging
from datetime import UTC, datetime

from rq import Queue, Retry, get_current_job

from raioxia import pipeline
from raioxia.db import Analysis
from raioxia.db.engine import session_scope
from raioxia.email.outbox import dispatch, queue_failed_email, queue_report_email
from raioxia.queues import ANALYSIS_QUEUE, EMAIL_QUEUE, get_queue
from raioxia.runner import AnalysisError, analyze
from raioxia.settings import get_settings

log = logging.getLogger(__name__)

JOB_TIMEOUT = 60  # seconds; the runner gives each check 45s and the render 25s
RETRY = Retry(max=1, interval=30)  # infrastructure errors only; AnalysisError is final
INTERNAL_ERROR_MESSAGE = "Tivemos um problema do nosso lado ao analisar o site. Tente de novo em alguns minutos."


def enqueue_analysis(queue: Queue, analysis_id: int) -> None:
    queue.enqueue(
        run_analysis, analysis_id, job_id=f"analysis-{analysis_id}", job_timeout=JOB_TIMEOUT, retry=RETRY,
        result_ttl=3600, failure_ttl=7 * 86400,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(analysis_id: int, code: str, message: str) -> None:
    with session_scope() as session:
        analysis = session.get(Analysis, analysis_id)
        if analysis is None or analysis.status in ("done", "failed"):
            return
        pipeline.fail(session, analysis, code, message, now=_now())
        row = queue_failed_email(session, analysis) if analysis.lead is not None else None
    if row is not None:
        dispatch(get_queue(EMAIL_QUEUE), [row.id])


def run_analysis(analysis_id: int) -> str:
    """RQ entry point. Returns the final status, or "skipped" for a duplicate/stale job."""
    with session_scope() as session:
        analysis = pipeline.start(session, analysis_id)
        if analysis is None:
            return "skipped"
        url = analysis.url
    log.info("analysis started", extra={"analysis_id": analysis_id, "url": url})

    try:
        report = asyncio.run(analyze(url))
    except AnalysisError as exc:  # the site itself can't be analyzed: final, tell the user
        log.info("analysis refused", extra={"analysis_id": analysis_id, "code": exc.code})
        _fail(analysis_id, exc.code, exc.message)
        return "failed"
    except Exception:
        job = get_current_job()
        if job is None or not job.retries_left:  # last attempt: don't leave the analysis "running" forever
            log.exception("analysis crashed, giving up", extra={"analysis_id": analysis_id})
            _fail(analysis_id, "internal", INTERNAL_ERROR_MESSAGE)
        else:
            log.exception("analysis crashed, will retry", extra={"analysis_id": analysis_id})
        raise

    with session_scope() as session:
        analysis = session.get(Analysis, analysis_id)
        pipeline.finish(session, analysis, report, now=_now())
        row = queue_report_email(session, analysis, base_url=get_settings().base_url) if analysis.lead is not None else None
    if row is not None:
        dispatch(get_queue(EMAIL_QUEUE), [row.id])
    log.info("analysis done", extra={"analysis_id": analysis_id, "score": report.score, "duration_ms": report.duration_ms})
    return "done"


def run_worker(queue_name: str) -> None:
    """`python -m raioxia worker analysis|email`. Production (Linux) forks one process per job."""
    import os

    from rq import SimpleWorker, Worker
    from rq.timeouts import TimerDeathPenalty

    from raioxia.logs import configure_logging
    from raioxia.queues import get_redis

    settings = get_settings()
    configure_logging(settings.log_level, json_lines=settings.log_json)
    queue = get_queue(queue_name)
    if os.name == "nt":  # no fork() and no SIGALRM on Windows: development only
        worker = SimpleWorker([queue], connection=get_redis())
        worker.death_penalty_class = TimerDeathPenalty
    else:
        worker = Worker([queue], connection=get_redis())
    worker.work(with_scheduler=True)  # the scheduler runs the delayed retries


QUEUES = (ANALYSIS_QUEUE, EMAIL_QUEUE)
