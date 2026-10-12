import json
from datetime import UTC, datetime

from raioxia.models import AnalysisReport, CheckResult, Status


def test_check_result_serializes_spec_fields_and_ptbr_status():
    result = CheckResult(id="basics", status=Status.WARNING, titulo="t", explicacao="e", peso=6)
    data = json.loads(result.model_dump_json())
    assert set(data) == {"id", "status", "titulo", "explicacao", "como_corrigir", "evidencia", "peso"}
    assert data["status"] == "aviso"


def test_status_values_match_spec():
    assert {s.value for s in Status} == {"ok", "aviso", "erro", "indeterminado"}


def test_report_allows_missing_score():
    report = AnalysisReport(
        url="https://example.com",
        checks=[],
        analyzed_at=datetime.now(UTC),
        duration_ms=0,
        version="0.0.1",
    )
    assert report.score is None and report.band is None
