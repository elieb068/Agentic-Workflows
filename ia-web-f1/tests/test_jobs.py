from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from raioxia import pipeline
from raioxia.db import Analysis, EmailOutbox
from raioxia.jobs import INTERNAL_ERROR_MESSAGE, enqueue_analysis, run_analysis
from raioxia.runner import AnalysisError
from tests.conftest import sample_report

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def confirmed(db, url="exemplo.com.br") -> int:
    sub = pipeline.submit(
        db, url=url, email="ana@exemplo.com.br", accepted_terms=True, newsletter=False, ip="203.0.113.7", user_agent="UA", now=NOW
    )
    pipeline.confirm(db, sub.confirm_token, now=NOW)
    db.commit()
    return sub.analysis.id


def outbox(db):
    return db.scalars(select(EmailOutbox).order_by(EmailOutbox.id)).all()


def test_successful_analysis_stores_report_and_sends_result_email(db, pipeline_env):
    analysis_id = confirmed(db)
    enqueue_analysis(pipeline_env.queues["analysis"], analysis_id)  # sync queue: runs now

    assert pipeline_env.analyze_calls == ["https://exemplo.com.br/"]
    db.expire_all()
    analysis = db.get(Analysis, analysis_id)
    assert (analysis.status, analysis.score, analysis.band_label) == ("done", 71, "Entreaberta")
    assert [r.check_id for r in analysis.results] == ["robots_ai", "js_dependency", "llms_txt"]
    [row] = outbox(db)
    assert (row.kind, row.status) == ("report", "sent")
    [email] = pipeline_env.sender.sent
    assert email.subject == "Raio-X IA de exemplo.com.br: nota 71 de 100"
    assert f"https://raiox.primesolutions.net.br/r/{analysis.report_token}" in email.text
    assert "1. Conteúdo só aparece com JavaScript (problema)" in email.text


def test_unanalyzable_site_fails_with_ptbr_email(db, pipeline_env):
    pipeline_env.analyze_result = AnalysisError("dns", "Não encontramos esse domínio. Confira se o endereço está certo.")
    analysis_id = confirmed(db)
    assert run_analysis(analysis_id) == "failed"
    db.expire_all()
    analysis = db.get(Analysis, analysis_id)
    assert (analysis.status, analysis.error_code) == ("failed", "dns")
    [row] = outbox(db)
    assert row.kind == "failed"
    assert pipeline_env.sender.sent[0].subject == "Não conseguimos analisar exemplo.com.br"
    assert "Não encontramos esse domínio" in pipeline_env.sender.sent[0].text


def test_crash_on_last_attempt_marks_failed_and_reraises(db, pipeline_env):
    pipeline_env.analyze_result = RuntimeError("chromium died")
    analysis_id = confirmed(db)
    with pytest.raises(RuntimeError):
        run_analysis(analysis_id)  # outside RQ there is no job: treated as the last attempt
    db.expire_all()
    analysis = db.get(Analysis, analysis_id)
    assert (analysis.status, analysis.error_code, analysis.error_message) == ("failed", "internal", INTERNAL_ERROR_MESSAGE)
    assert [r.kind for r in outbox(db)] == ["failed"]


def test_crash_with_retries_left_keeps_the_analysis_for_the_retry(db, pipeline_env, monkeypatch):
    class Job:
        retries_left = 1

    monkeypatch.setattr("raioxia.jobs.get_current_job", lambda: Job())
    pipeline_env.analyze_result = RuntimeError("redis blip")
    analysis_id = confirmed(db)
    with pytest.raises(RuntimeError):
        run_analysis(analysis_id)
    db.expire_all()
    assert db.get(Analysis, analysis_id).status == "running"
    assert outbox(db) == []

    pipeline_env.analyze_result = sample_report()
    assert run_analysis(analysis_id) == "done"  # the retry picks the running analysis back up


def test_duplicate_or_unconfirmed_jobs_do_nothing(db, pipeline_env):
    analysis_id = confirmed(db)
    assert run_analysis(analysis_id) == "done"
    assert run_analysis(analysis_id) == "skipped"
    pending = pipeline.submit(
        db, url="outro.com.br", email="bia@exemplo.com.br", accepted_terms=True, newsletter=False, ip=None, user_agent=None, now=NOW
    )
    db.commit()
    assert run_analysis(pending.analysis.id) == "skipped"
    assert run_analysis(999_999) == "skipped"
    assert pipeline_env.analyze_calls == ["https://exemplo.com.br/"]


def test_enqueue_uses_a_stable_job_id(pipeline_env):
    import fakeredis
    from rq import Queue

    queue = Queue("analysis", connection=fakeredis.FakeStrictRedis())
    enqueue_analysis(queue, 42)
    [job] = queue.get_jobs()
    assert job.id == "analysis-42" and job.args == (42,) and job.timeout == 60 and job.retries_left == 1
