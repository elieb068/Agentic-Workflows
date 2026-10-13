"""Structure of an llms.txt file (https://llmstxt.org).

Expected layout:
    # Project name                      (H1, first content line)
    > Short summary                     (blockquote right after the H1)
    Optional free text...
    ## Section                          (H2 sections with lists of links)
    - [Page](https://example.com/page): notes
"""

import re
from dataclasses import dataclass, field

_H1 = re.compile(r"^#\s+(\S.*)$")
_H2 = re.compile(r"^##\s+(\S.*)$")
_LIST_LINK = re.compile(r"^\s*[-*+]\s+\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_ANY_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")


@dataclass(frozen=True)
class Link:
    text: str
    url: str
    section: str | None


@dataclass
class LlmsTxt:
    title: str | None = None
    title_is_first: bool = False
    summary: str | None = None
    sections: dict[str, int] = field(default_factory=dict)  # H2 title -> number of listed links
    links: list[Link] = field(default_factory=list)  # listed links inside H2 sections
    other_links: list[str] = field(default_factory=list)  # inline links elsewhere
    h1_count: int = 0

    @property
    def issues(self) -> list[str]:
        problems = []
        if self.title is None:
            problems.append("missing_title")
        elif not self.title_is_first:
            problems.append("title_not_first")
        if self.h1_count > 1:
            problems.append("multiple_titles")
        if self.summary is None:
            problems.append("missing_summary")
        if not any(self.sections.values()):
            problems.append("no_link_sections")
        return problems


def parse_llms_txt(text: str) -> LlmsTxt:
    doc = LlmsTxt()
    section: str | None = None
    in_fence = False
    seen_content = False
    expecting_summary = False

    for raw in text.lstrip("﻿").splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            seen_content = True
            continue
        if in_fence or not line.strip():
            continue

        if match := _H1.match(line):
            doc.h1_count += 1
            if doc.title is None:
                doc.title = match.group(1).strip()
                doc.title_is_first = not seen_content
                expecting_summary = True
            seen_content = True
            continue
        seen_content = True

        if expecting_summary:
            expecting_summary = False
            if line.lstrip().startswith(">"):
                doc.summary = line.lstrip()[1:].strip() or None
                continue

        if match := _H2.match(line):
            section = match.group(1).strip()
            doc.sections.setdefault(section, 0)
            continue

        listed = _LIST_LINK.match(line)
        if listed and section is not None:
            doc.links.append(Link(text=listed.group(1).strip(), url=listed.group(2), section=section))
            doc.sections[section] += 1
            continue
        doc.other_links += _ANY_LINK.findall(line)

    return doc
