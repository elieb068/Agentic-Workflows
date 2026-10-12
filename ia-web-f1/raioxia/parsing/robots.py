"""robots.txt parser following RFC 9309 (the rules Google and the AI crawlers follow).

- Groups start with one or more consecutive user-agent lines; groups naming the
  same agent are merged. A crawler uses its own groups, else the "*" groups.
- Rule matching: the longest matching path pattern wins; on a tie, Allow wins.
  Patterns support "*" (any sequence) and a trailing "$" (end of path).
- Only the first 500 KiB are parsed (the RFC's minimum limit).
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

MAX_ROBOTS_BYTES = 500 * 1024

_UA_KEYS = {"user-agent", "useragent", "user agent"}
_ALLOW_KEYS = {"allow"}
_DISALLOW_KEYS = {"disallow", "dissallow", "dissalow", "disalow", "diasllow", "disallaw"}
_SITEMAP_KEYS = {"sitemap", "site-map"}


@dataclass(frozen=True)
class Rule:
    allow: bool
    path: str
    line: int

    @property
    def text(self) -> str:
        return f"{'Allow' if self.allow else 'Disallow'}: {self.path}"


@dataclass
class Group:
    user_agents: list[str]  # as written
    rules: list[Rule] = field(default_factory=list)
    line: int = 0

    @property
    def tokens(self) -> set[str]:
        return {normalize_token(ua) for ua in self.user_agents}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: Rule | None  # None: no rule matched (allowed by default)
    groups: list[Group]  # groups that applied to the crawler
    matched_by: str | None  # "token", "*" or None (no group applies)


def normalize_token(value: str) -> str:
    """'GPTBot/1.1 extra' -> 'gptbot'. Matching is case-insensitive on the product token."""
    return value.strip().split("/", 1)[0].split(None, 1)[0].lower() if value.strip() else ""


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""), re.DOTALL)


def path_matches(pattern: str, path: str) -> bool:
    return _compile(pattern).match(path) is not None


class Robots:
    def __init__(self, groups: list[Group], sitemaps: list[str]):
        self.groups = groups
        self.sitemaps = sitemaps

    @classmethod
    def parse(cls, text: str) -> "Robots":
        text = text[:MAX_ROBOTS_BYTES].lstrip("﻿")
        groups: list[Group] = []
        sitemaps: list[str] = []
        current: Group | None = None
        in_ua_block = False

        for number, raw in enumerate(text.splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()

            if key in _UA_KEYS:
                if not value:
                    continue
                if current is None or not in_ua_block:
                    current = Group(user_agents=[], line=number)
                    groups.append(current)
                current.user_agents.append(value)
                in_ua_block = True
            elif key in _ALLOW_KEYS or key in _DISALLOW_KEYS:
                in_ua_block = False
                if current is None or not value:
                    continue  # rule outside a group, or empty rule (= no restriction)
                if not value.startswith(("/", "*")):
                    value = "/" + value
                current.rules.append(Rule(allow=key in _ALLOW_KEYS, path=value, line=number))
            elif key in _SITEMAP_KEYS and value:
                sitemaps.append(value)
            # Other records (crawl-delay, host...) are ignored and do not end a group.

        return cls(groups=groups, sitemaps=sitemaps)

    def groups_for(self, token: str) -> tuple[list[Group], str | None]:
        wanted = normalize_token(token)
        own = [g for g in self.groups if wanted in g.tokens]
        if own:
            return own, "token"
        star = [g for g in self.groups if "*" in g.tokens]
        return (star, "*") if star else ([], None)

    def decide(self, token: str, path: str) -> Decision:
        groups, matched_by = self.groups_for(token)
        best: Rule | None = None
        for group in groups:
            for rule in group.rules:
                if not path_matches(rule.path, path):
                    continue
                if (
                    best is None
                    or len(rule.path) > len(best.path)
                    or (len(rule.path) == len(best.path) and rule.allow and not best.allow)
                ):
                    best = rule
        return Decision(allowed=best is None or best.allow, rule=best, groups=groups, matched_by=matched_by)

    def groups_naming(self, token: str) -> list[Group]:
        wanted = normalize_token(token)
        return [g for g in self.groups if wanted in g.tokens]


def looks_like_html(text: str) -> bool:
    head = text.lstrip()[:512].lower()
    return head.startswith("<") and ("<html" in head or "<!doctype" in head or "<head" in head or "<body" in head)
