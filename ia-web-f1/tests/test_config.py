from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from raioxia.config import AgentCategory, ScoringConfig, load_config
from raioxia.models import Status

SPEC_TOKENS = {
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-SearchBot", "Claude-User",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Googlebot",
    "Bingbot", "Applebot-Extended", "CCBot", "Meta-ExternalAgent", "Bytespider",
}
SPEC_CHECK_IDS = {
    "robots_ai", "edge_block", "llms_txt", "meta_robots",
    "js_dependency", "structured_data", "sitemap", "basics",
}


@pytest.fixture(scope="module")
def config():
    return load_config()


def test_agents_cover_spec_tokens(config):
    assert {a.token for a in config.agents.agents} == SPEC_TOKENS


def test_retired_tokens_listed(config):
    assert {r.token for r in config.agents.retired_tokens} == {"anthropic-ai", "Claude-Web"}


def test_every_category_present(config):
    assert {a.category for a in config.agents.agents} == set(AgentCategory)


def test_robots_only_agents_have_no_ua(config):
    robots_only = {a.token for a in config.agents.agents if a.robots_only}
    assert robots_only == {"Google-Extended", "Applebot-Extended"}
    for agent in config.agents.agents:
        assert agent.robots_only or agent.user_agent


def test_weights_cover_all_checks(config):
    assert set(config.scoring.weights) == SPEC_CHECK_IDS


def test_edge_weighs_less_than_robots_and_js(config):
    w = config.scoring.weights
    assert w["edge_block"] < w["robots_ai"]
    assert w["edge_block"] < w["js_dependency"]


@pytest.mark.parametrize(
    ("score", "band"),
    [(100, "open"), (80, "open"), (79, "ajar"), (50, "ajar"), (49, "closed"), (0, "closed")],
)
def test_band_boundaries(config, score, band):
    assert config.scoring.band_for(score).id == band


def _scoring(**overrides):
    base = {
        "status_values": {"ok": 1, "aviso": 0.5, "erro": 0},
        "weights": {"basics": 1},
        "bands": [{"id": "a", "label": "A", "min_score": 50}, {"id": "b", "label": "B", "min_score": 0}],
    }
    return base | overrides


def test_scoring_rejects_value_for_unknown_status():
    values = {"ok": 1, "aviso": 0.5, "erro": 0, "indeterminado": 0}
    with pytest.raises(ValidationError, match="indeterminado"):
        ScoringConfig.model_validate(_scoring(status_values=values))


def test_scoring_rejects_bands_without_zero_floor():
    with pytest.raises(ValidationError, match="min_score 0"):
        ScoringConfig.model_validate(_scoring(bands=[{"id": "a", "label": "A", "min_score": 50}]))


def test_scoring_rejects_unordered_bands():
    bands = [{"id": "b", "label": "B", "min_score": 0}, {"id": "a", "label": "A", "min_score": 50}]
    with pytest.raises(ValidationError, match="decreasing"):
        ScoringConfig.model_validate(_scoring(bands=bands))


def test_scoring_rejects_negative_weight():
    with pytest.raises(ValidationError, match=">= 0"):
        ScoringConfig.model_validate(_scoring(weights={"basics": -1}))


def test_agent_without_ua_must_be_robots_only(tmp_path: Path):
    agents = yaml.safe_load((Path(__file__).parent.parent / "config/ai_agents.yaml").read_text(encoding="utf-8"))
    agents["agents"][0].pop("user_agent")
    (tmp_path / "ai_agents.yaml").write_text(yaml.safe_dump(agents), encoding="utf-8")
    (tmp_path / "scoring.yaml").write_text(yaml.safe_dump(_scoring()), encoding="utf-8")
    with pytest.raises(ValidationError, match="robots_only"):
        load_config(tmp_path)


def test_duplicate_tokens_rejected(tmp_path: Path):
    agents = yaml.safe_load((Path(__file__).parent.parent / "config/ai_agents.yaml").read_text(encoding="utf-8"))
    agents["retired_tokens"].append({"token": "gptbot", "company": "OpenAI"})
    (tmp_path / "ai_agents.yaml").write_text(yaml.safe_dump(agents), encoding="utf-8")
    (tmp_path / "scoring.yaml").write_text(yaml.safe_dump(_scoring()), encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate"):
        load_config(tmp_path)


def test_status_values_keyed_by_enum(config):
    assert config.scoring.status_values[Status.OK] == 1.0
