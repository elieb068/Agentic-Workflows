"""Result models shared by every checker.

CheckResult field names follow the project spec (PT-BR) because they are the
public JSON contract consumed by the report page and e-mails.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Status(StrEnum):
    OK = "ok"
    WARNING = "aviso"
    ERROR = "erro"
    UNKNOWN = "indeterminado"


class CheckResult(BaseModel):
    id: str
    status: Status
    titulo: str
    explicacao: str
    como_corrigir: str = ""
    evidencia: dict[str, Any] = Field(default_factory=dict)
    peso: float = 0.0


class Band(BaseModel):
    id: str
    label: str


class AnalysisReport(BaseModel):
    url: str
    final_url: str | None = None
    score: int | None = None  # None when every check is indeterminado
    band: Band | None = None
    checks: list[CheckResult]
    analyzed_at: datetime
    duration_ms: int
    version: str
