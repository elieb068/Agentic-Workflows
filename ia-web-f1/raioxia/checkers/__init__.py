"""Pluggable analysis modules. Each checker receives the analysis context and returns a CheckResult.

To add a check: subclass Checker, decorate it with @register, import its module
below and give it a weight in config/scoring.yaml.
"""

from raioxia.checkers.base import AnalysisContext, Checker

REGISTRY: dict[str, type[Checker]] = {}


def register(cls: type[Checker]) -> type[Checker]:
    if cls.id in REGISTRY:
        raise ValueError(f"duplicate checker id {cls.id!r}")
    REGISTRY[cls.id] = cls
    return cls


def all_checkers() -> list[Checker]:
    return [cls() for cls in REGISTRY.values()]


# Registration order is the report order (spec numbering).
from raioxia.checkers import robots_ai, edge_block, llms_txt, meta_robots, js_dependency, structured_data, sitemap, basics  # noqa: E402,F401,I001  (registers on import)

__all__ = ["REGISTRY", "AnalysisContext", "Checker", "all_checkers", "register"]
