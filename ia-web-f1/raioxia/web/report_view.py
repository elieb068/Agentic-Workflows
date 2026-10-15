"""Everything the report page shows, computed from an Analysis (no logic in the template)."""

import json
import math
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy.orm import Session

from raioxia.db import Analysis
from raioxia.web.templating import COMPANY, local_datetime

GAUGE_RADIUS = 72
GAUGE_CIRCUMFERENCE = 2 * math.pi * GAUGE_RADIUS

STATUS = {
    "erro": {"label": "Problema", "icon": "error", "order": 0, "pill": "bg-red-400/15 text-red-200", "border": "border-red-400/45"},
    "aviso": {"label": "Atenção", "icon": "warning", "order": 1, "pill": "bg-amber-400/15 text-amber-200", "border": "border-amber-400/40"},
    "ok": {"label": "OK", "icon": "check", "order": 2, "pill": "bg-green-400/15 text-green-200", "border": "border-slate-400/15"},
    "indeterminado": {"label": "Não verificado", "icon": "unknown", "order": 3, "pill": "bg-slate-400/15 text-slate-300", "border": "border-slate-400/15"},
}
BANDS = {
    "open": {"stroke": "#4ade80", "pill": "border-green-400/45 bg-green-400/10 text-green-200",
             "summary": "A IA consegue ler o seu site. Veja abaixo os detalhes e o que ainda dá para melhorar."},
    "ajar": {"stroke": "#fbbf24", "pill": "border-amber-400/45 bg-amber-400/10 text-amber-200",
             "summary": "A IA chega ao seu site, mas há pontos que atrapalham. Corrigindo os itens abaixo, a porta se abre de vez."},
    "closed": {"stroke": "#f87171", "pill": "border-red-400/45 bg-red-400/10 text-red-200",
               "summary": "Hoje a IA tem dificuldade para ler o seu site. Os itens abaixo mostram o que está fechando a porta."},
}


@dataclass(frozen=True)
class CheckView:
    id: str
    titulo: str
    explicacao: str
    como_corrigir: str
    evidence_json: str
    status: str
    label: str
    icon: str
    pill: str
    border: str


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def build(session: Session, analysis: Analysis, *, base_url: str) -> dict:
    status = analysis.status
    public = "processing" if status in ("queued", "running") else status
    view = {"status": public, "domain": analysis.domain, "url": analysis.url, "token": analysis.report_token}
    if status == "failed":
        view["error_message"] = analysis.error_message or "Não conseguimos analisar o site."
    if status != "done":
        return view

    checks = sorted(analysis.results, key=lambda r: (STATUS[r.status]["order"], -r.peso, r.position))
    counts = {s: sum(1 for r in checks if r.status == s) for s in STATUS}
    summary_parts = [
        _plural(counts["erro"], "problema", "problemas"),
        _plural(counts["aviso"], "ponto de atenção", "pontos de atenção"),
        f"{counts['ok']} ok",
    ]
    if counts["indeterminado"]:
        summary_parts.append(_plural(counts["indeterminado"], "não verificado", "não verificados"))

    analyzed_at = analysis.finished_at
    if analysis.reused_from_id:  # show when the site was really analyzed, not when this copy was made
        source = session.get(Analysis, analysis.reused_from_id)
        analyzed_at = source.finished_at if source and source.finished_at else analyzed_at

    band = BANDS.get(analysis.band_id or "", {"stroke": "#94a3b8", "pill": "border-slate-400/45 text-slate-200", "summary": ""})
    score = analysis.score
    report_url = f"{base_url}/r/{analysis.report_token}"
    subject = f"Quero ajuda com o Raio-X IA de {analysis.domain}"
    body = (
        f"Olá! Fiz o Raio-X IA de {analysis.domain}"
        + (f" (nota {score} de 100)" if score is not None else "")
        + " e quero ajuda para corrigir os problemas.\n\n"
        + f"Relatório: {report_url}\n\nMeu nome:\nTelefone (opcional):\n"
    )
    view.update(
        score=score,
        band_label=analysis.band_label,
        band_pill=band["pill"],
        band_summary=band["summary"],
        gauge_stroke=band["stroke"],
        gauge_dash=f"{(score or 0) / 100 * GAUGE_CIRCUMFERENCE:.1f} {GAUGE_CIRCUMFERENCE:.1f}",
        analyzed_at=local_datetime(analyzed_at),
        reused=bool(analysis.reused_from_id),
        summary=" · ".join(summary_parts),
        has_problems=bool(counts["erro"] or counts["aviso"]),
        checks=[
            CheckView(
                id=r.check_id, titulo=r.titulo, explicacao=r.explicacao, como_corrigir=r.como_corrigir,
                evidence_json=json.dumps(r.evidencia, ensure_ascii=False, indent=2) if r.evidencia else "",
                status=r.status, **{k: STATUS[r.status][k] for k in ("label", "icon", "pill", "border")},
            )
            for r in checks
        ],
        help_mailto=f"mailto:{COMPANY['contact_email']}?subject={quote(subject)}&body={quote(body)}",
        lead_subscribed=bool(analysis.lead and analysis.lead.newsletter),
        can_subscribe=analysis.lead is not None and analysis.confirmed_at is not None,
    )
    return view
