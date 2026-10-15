"""Static assets: cache-busting URLs and the fingerprint that tells whether app.css is stale."""

import hashlib
from functools import lru_cache
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"
STYLES_SOURCE = WEB_DIR / "styles" / "app.css"
TAILWIND_VERSION = "4.3.3"
CSS_HEADER_PREFIX = "/*! raioxia sources "


@lru_cache(maxsize=256)
def static_url(name: str) -> str:
    """`/static/<name>?v=<content hash>`: safe to cache for a year, changes when the file does."""
    digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    return f"/static/{name}?v={digest}"


def css_sources_digest() -> str:
    """Hash of everything Tailwind reads (stylesheet source, templates, CLI version)."""
    h = hashlib.sha256(TAILWIND_VERSION.encode())
    for path in [STYLES_SOURCE, *sorted(TEMPLATES_DIR.rglob("*.html"))]:
        h.update(path.relative_to(WEB_DIR).as_posix().encode())
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def built_css_digest() -> str | None:
    """The digest recorded in the committed static/app.css (first line), if any."""
    css = STATIC_DIR / "app.css"
    if not css.exists():
        return None
    first = css.read_text(encoding="utf-8").split("\n", 1)[0]
    return first.removeprefix(CSS_HEADER_PREFIX).removesuffix(" */") if first.startswith(CSS_HEADER_PREFIX) else None
