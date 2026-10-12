import pytest

from raioxia.parsing.robots import MAX_ROBOTS_BYTES, Robots, looks_like_html, normalize_token, path_matches


def allowed(text: str, token: str, path: str) -> bool:
    return Robots.parse(text).decide(token, path).allowed


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/", "/anything", True),
        ("/fish", "/fish.html", True),
        ("/fish", "/Fish", False),  # paths are case-sensitive
        ("/fish/", "/fish", False),
        ("/*.pdf$", "/docs/a.pdf", True),
        ("/*.pdf$", "/docs/a.pdf?x=1", False),
        ("/*?", "/search?q=1", True),
        ("/$", "/", True),
        ("/$", "/page", False),
        ("*/private", "/a/private", True),
        ("/a*b*c", "/a-x-b-y-c", True),
        ("/a.b", "/axb", False),  # "." is literal
    ],
)
def test_path_patterns(pattern, path, expected):
    assert path_matches(pattern, path) is expected


def test_longest_match_wins():
    text = "User-agent: *\nDisallow: /\nAllow: /public"
    assert allowed(text, "x", "/public/page")
    assert not allowed(text, "x", "/other")


def test_allow_wins_tie():
    assert allowed("User-agent: *\nDisallow: /page\nAllow: /page", "x", "/page")


def test_specific_group_replaces_star_group():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /"
    robots = Robots.parse(text)
    assert robots.decide("GPTBot", "/").allowed
    assert robots.decide("GPTBot", "/").matched_by == "token"
    assert not robots.decide("ClaudeBot", "/").allowed
    assert robots.decide("ClaudeBot", "/").matched_by == "*"


def test_groups_for_same_agent_are_merged():
    text = "User-agent: GPTBot\nDisallow: /a\n\nUser-agent: GPTBot\nDisallow: /b"
    robots = Robots.parse(text)
    assert not robots.decide("GPTBot", "/a").allowed
    assert not robots.decide("GPTBot", "/b").allowed
    assert len(robots.decide("GPTBot", "/").groups) == 2


def test_consecutive_user_agents_share_a_group():
    text = "User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: /"
    assert not allowed(text, "GPTBot", "/") and not allowed(text, "ClaudeBot", "/")


def test_user_agent_after_rules_starts_new_group():
    text = "User-agent: GPTBot\nDisallow: /\nUser-agent: ClaudeBot\nAllow: /"
    assert not allowed(text, "GPTBot", "/") and allowed(text, "ClaudeBot", "/")


def test_unknown_records_do_not_split_group():
    text = "User-agent: GPTBot\nCrawl-delay: 10\nUser-agent: ClaudeBot\nDisallow: /"
    assert not allowed(text, "GPTBot", "/") and not allowed(text, "ClaudeBot", "/")


@pytest.mark.parametrize("line", ["USER-AGENT: gptbot", "user-agent: GPTBot/1.1", "useragent: GPTBOT", "User-agent:GPTBot # hi"])
def test_token_matching_is_case_insensitive_and_ignores_version(line):
    assert not allowed(f"{line}\nDisallow: /", "GPTBot", "/")


def test_no_matching_group_allows():
    decision = Robots.parse("User-agent: GPTBot\nDisallow: /").decide("ClaudeBot", "/")
    assert decision.allowed and decision.matched_by is None and decision.rule is None


def test_empty_disallow_allows_everything():
    assert allowed("User-agent: *\nDisallow:", "x", "/")


def test_rules_before_any_user_agent_are_ignored():
    assert allowed("Disallow: /\nUser-agent: *\nAllow: /x", "x", "/")


def test_misspelled_disallow_and_missing_slash():
    assert not allowed("User-agent: *\nDissallow: private", "x", "/private/a")


def test_comments_bom_and_crlf():
    text = "﻿User-agent: * # everyone\r\nDisallow: /secret # hidden\r\n"
    assert not allowed(text, "x", "/secret") and allowed(text, "x", "/")


def test_sitemaps_collected_outside_groups():
    robots = Robots.parse("Sitemap: https://a.example/s1.xml\nUser-agent: *\nDisallow:\nsitemap: /s2.xml")
    assert robots.sitemaps == ["https://a.example/s1.xml", "/s2.xml"]


def test_decision_reports_rule_and_line():
    decision = Robots.parse("User-agent: *\nAllow: /a\nDisallow: /").decide("x", "/b")
    assert decision.rule.text == "Disallow: /" and decision.rule.line == 3


def test_content_beyond_limit_is_ignored():
    text = "User-agent: *\n" + "#" * MAX_ROBOTS_BYTES + "\nDisallow: /"
    assert allowed(text, "x", "/")


def test_groups_naming():
    robots = Robots.parse("User-agent: anthropic-ai\nDisallow: /\nUser-agent: *\nAllow: /")
    assert len(robots.groups_naming("Anthropic-AI")) == 1
    assert robots.groups_naming("ClaudeBot") == []


def test_normalize_token():
    assert normalize_token(" GPTBot/1.1 (+https://openai.com) ") == "gptbot"
    assert normalize_token("") == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<!DOCTYPE html><html><body>Home</body></html>", True),
        ("\n  <html lang='pt'>", True),
        ("User-agent: *\nDisallow: /", False),
        ("<not really html", False),
    ],
)
def test_looks_like_html(text, expected):
    assert looks_like_html(text) is expected
