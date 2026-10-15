"""Build raioxia/web/static/app.css with the Tailwind v4 standalone CLI (no Node).

    python scripts/build_css.py           # build once
    python scripts/build_css.py --watch   # rebuild while editing templates

Downloads the pinned CLI for this platform into .tools/ on first use. The output is committed;
its first line records a digest of the sources, and a test fails if it is stale.
"""

import platform
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from raioxia.web.assets import CSS_HEADER_PREFIX, STATIC_DIR, STYLES_SOURCE, TAILWIND_VERSION, css_sources_digest  # noqa: E402

TOOLS = ROOT / ".tools"
OUTPUT = STATIC_DIR / "app.css"


def _asset_name() -> str:
    system, machine = platform.system().lower(), platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system == "windows":
        return f"tailwindcss-windows-{arch}.exe"
    if system == "darwin":
        return f"tailwindcss-macos-{arch}"
    return f"tailwindcss-linux-{arch}"


def cli() -> Path:
    name = _asset_name()
    path = TOOLS / f"{TAILWIND_VERSION}-{name}"
    if not path.exists():
        TOOLS.mkdir(exist_ok=True)
        url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/v{TAILWIND_VERSION}/{name}"
        print(f"downloading {url}")
        urllib.request.urlretrieve(url, path)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def build(watch: bool = False) -> None:
    args = [str(cli()), "-i", str(STYLES_SOURCE), "-o", str(OUTPUT), "--minify"]
    if watch:
        subprocess.run([*args, "--watch"], check=True)
        return
    subprocess.run(args, check=True, cwd=ROOT)
    css = OUTPUT.read_text(encoding="utf-8")
    OUTPUT.write_text(f"{CSS_HEADER_PREFIX}{css_sources_digest()} */\n{css}", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build(watch="--watch" in sys.argv)
