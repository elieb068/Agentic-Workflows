"""HTML helpers built on selectolax (fast, tolerant of broken markup)."""

from functools import cached_property

from selectolax.lexbor import LexborHTMLParser


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text or None


class HtmlDoc:
    def __init__(self, html: str):
        self.tree = LexborHTMLParser(html)

    @cached_property
    def title(self) -> str | None:
        node = self.tree.css_first("title")
        return _clean(node.text()) if node else None

    @cached_property
    def lang(self) -> str | None:
        node = self.tree.css_first("html")
        return _clean(node.attributes.get("lang")) if node else None

    def meta(self, name: str) -> str | None:
        """Content of the first <meta name=...> matching `name` (case-insensitive)."""
        wanted = name.lower()
        for node in self.tree.css("meta[name]"):
            if (node.attributes.get("name") or "").strip().lower() == wanted:
                return _clean(node.attributes.get("content"))
        return None
