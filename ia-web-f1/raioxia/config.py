"""Loads and validates the versioned YAML config (config/*.yaml)."""

import os
from datetime import date
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, model_validator

from raioxia.models import Status

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class AgentCategory(StrEnum):
    TRAINING = "training"
    SEARCH = "search"
    USER = "user"


class Agent(BaseModel):
    token: str
    company: str
    category: AgentCategory
    user_agent: str | None = None
    robots_only: bool = False

    @model_validator(mode="after")
    def _ua_required_unless_robots_only(self) -> Self:
        if not self.robots_only and not self.user_agent:
            raise ValueError(f"agent {self.token!r} needs user_agent or robots_only: true")
        return self


class RetiredToken(BaseModel):
    token: str
    company: str
    replaced_by: list[str] = Field(default_factory=list)


class AgentsConfig(BaseModel):
    last_reviewed: date
    browser_user_agent: str
    agents: list[Agent]
    retired_tokens: list[RetiredToken] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_tokens(self) -> Self:
        tokens = [a.token.lower() for a in self.agents] + [r.token.lower() for r in self.retired_tokens]
        dupes = {t for t in tokens if tokens.count(t) > 1}
        if dupes:
            raise ValueError(f"duplicate agent tokens: {sorted(dupes)}")
        return self


class ScoreBand(BaseModel):
    id: str
    label: str
    min_score: int = Field(ge=0, le=100)


class ScoringConfig(BaseModel):
    status_values: dict[Status, float]
    weights: dict[str, float]
    bands: list[ScoreBand]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if Status.UNKNOWN in self.status_values:
            raise ValueError("'indeterminado' must not have a value: it is excluded from the score")
        missing = {Status.OK, Status.WARNING, Status.ERROR} - self.status_values.keys()
        if missing:
            raise ValueError(f"missing status_values: {sorted(missing)}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be >= 0")
        mins = [b.min_score for b in self.bands]
        if mins != sorted(mins, reverse=True) or len(set(mins)) != len(mins):
            raise ValueError("bands must be ordered by strictly decreasing min_score")
        if not mins or mins[-1] != 0:
            raise ValueError("the last band must have min_score 0")
        return self

    def band_for(self, score: int) -> ScoreBand:
        return next(b for b in self.bands if score >= b.min_score)


class Config(BaseModel):
    agents: AgentsConfig
    scoring: ScoringConfig


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = config_dir or Path(os.environ.get("RAIOXIA_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    return Config(
        agents=AgentsConfig.model_validate(_read_yaml(config_dir / "ai_agents.yaml")),
        scoring=ScoringConfig.model_validate(_read_yaml(config_dir / "scoring.yaml")),
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
