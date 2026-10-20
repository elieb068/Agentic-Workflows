"""Accessibility (axe-core, WCAG 2.1 AA) and horizontal-overflow audit of the public pages.

    uvicorn raioxia.api.app:create_app --factory --port 8000   # in another terminal
    python scripts/a11y_audit.py [report-token] [--base http://127.0.0.1:8000]

Checks every page at 1280, 390 and 360 px in Playwright's Chromium. axe-core is downloaded into
.tools/ on first use. Exit code 1 if anything fails. Not part of pytest (needs a running app).
"""

import argparse
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from raioxia.web.guides import GUIDES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AXE_VERSION = "4.10.3"
AXE = ROOT / ".tools" / f"axe-{AXE_VERSION}.min.js"
WIDTHS = (1280, 390, 360)
RULES = ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"]


def axe_script() -> Path:
    if not AXE.exists():
        AXE.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(f"https://cdn.jsdelivr.net/npm/axe-core@{AXE_VERSION}/axe.min.js", AXE)
    return AXE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("token", nargs="?", help="a report token from the dev database, to audit /r/<token>")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    paths = ["/", "/guias", *(g.path for g in GUIDES), "/ferramentas/robots-txt-ia", "/newsletter", "/privacidade", "/termos", "/bot", "/excluir-dados", "/nao-existe"]
    paths += [f"/r/{args.token}"] if args.token else []
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": 900}, bypass_csp=True)  # to inject axe
            for path in paths:
                page = context.new_page()
                page.goto(args.base + path, wait_until="load")
                page.wait_for_timeout(1000)
                page.add_script_tag(path=str(axe_script()))
                violations = page.evaluate(
                    "async (rules) => (await axe.run(document, {runOnly: rules})).violations"
                    ".map(v => `${v.impact} ${v.id} (${v.nodes.length}): ${v.help}`)",
                    RULES,
                )
                overflow = page.evaluate("document.documentElement.scrollWidth") > width
                ok = not violations and not overflow
                failures += not ok
                print(f"{'ok  ' if ok else 'FAIL'} {width:>4}px {path}{'  [horizontal overflow]' if overflow else ''}")
                for v in violations:
                    print(f"       {v}")
                page.close()
            context.close()
        browser.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
