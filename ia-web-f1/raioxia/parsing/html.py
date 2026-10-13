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

    def metas_by_name(self) -> dict[str, list[str]]:
        """All <meta name=... content=...> grouped by lowercased name."""
        found: dict[str, list[str]] = {}
        for node in self.tree.css("meta[name]"):
            name = (node.attributes.get("name") or "").strip().lower()
            content = node.attributes.get("content")
            if name and content is not None:
                found.setdefault(name, []).append(content.strip())
        return found

    def jsonld_blocks(self) -> list[str]:
        """Raw text of every <script type="application/ld+json"> block."""
        blocks = []
        for node in self.tree.css("script[type]"):
            script_type = (node.attributes.get("type") or "").split(";", 1)[0].strip().lower()
            if script_type == "application/ld+json":
                blocks.append(node.text() or "")
        return blocks

    def microdata_types(self) -> list[str]:
        """itemtype values (microdata), e.g. https://schema.org/Bakery."""
        types = []
        for node in self.tree.css("[itemtype]"):
            types += (node.attributes.get("itemtype") or "").split()
        return types

    def visible_text(self) -> str:
        """Text a non-JS crawler gets from the raw HTML (scripts, styles and <noscript> removed).

        Uses a separate parse because strip_tags mutates the tree.
        """
        tree = LexborHTMLParser(self.tree.html or "")
        tree.strip_tags(["script", "style", "noscript", "template", "svg", "head"])
        root = tree.body or tree.root
        return " ".join((root.text(separator=" ") if root else "").split())
