"""Builds the derived images in raioxia/web/static/img/ with Playwright's Chromium (no image library needed).

    python scripts/build_images.py

- prime-logo-60.png: the Prime Solutions logo at 60 px high (2x the 30 px it is shown at). The 781 px
  original stays for the JSON-LD logo.
- og-raiox.png: the 1200x630 sharing image (og:image / twitter:card), in the site's colors and fonts.

Run it again after changing the logo, the fonts or the sharing image text, and commit the results.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parent.parent / "raioxia" / "web" / "static"
IMG = STATIC / "img"
LOGO = IMG / "prime-logo.png"
LOGO_HEIGHT = 60

OG_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><style>
@font-face { font-family: Outfit; src: url("{fonts}/outfit-latin-wght.woff2") format("woff2"); font-weight: 100 900; }
@font-face { font-family: Inter; src: url("{fonts}/inter-latin-wght.woff2") format("woff2"); font-weight: 100 900; }
* { margin: 0; box-sizing: border-box; }
body { width: 1200px; height: 630px; overflow: hidden; font-family: Inter, sans-serif; color: #e2e8f0;
  background: radial-gradient(circle at 85% 15%, rgba(34,163,159,.35), transparent 45%), #1a2e38; }
.wrap { height: 100%; padding: 64px 72px; display: flex; flex-direction: column; justify-content: space-between; }
.top { display: flex; align-items: center; gap: 22px; }
.top img { height: 44px; filter: brightness(0) invert(1); }
.top .bar { width: 2px; height: 40px; background: rgba(163,177,194,.4); }
.top span { font-family: Outfit; font-weight: 700; font-size: 34px; color: #f1f5f9; }
.kicker { font-family: Outfit; font-weight: 700; font-size: 32px;
  background: linear-gradient(90deg, #22a39f, #60a5fa); -webkit-background-clip: text; color: transparent; }
h1 { font-family: Outfit; font-weight: 800; font-size: 72px; line-height: 1.04; letter-spacing: -1.5px; color: #f1f5f9; margin-top: 14px; }
.foot { display: flex; justify-content: space-between; align-items: center; font-size: 26px; color: #cbd5e1; }
.pill { background: #17736f; color: #fff; font-family: Outfit; font-weight: 700; font-size: 28px; padding: 14px 30px; border-radius: 14px; }
</style></head><body><div class="wrap">
<div class="top"><img src="{logo}" alt=""><div class="bar"></div><span>Raio-X IA</span></div>
<div><p class="kicker">Sua porta está aberta para a IA?</p>
<h1>Seu site aparece no ChatGPT,<br>no Gemini e no Claude?</h1></div>
<div class="foot"><span>8 verificações · nota de 0 a 100 · raiox.primesolutions.net.br</span><span class="pill">Teste grátis</span></div>
</div></body></html>"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        # Pages are file:// so the local logo and fonts load (about:blank may not read file:// URLs).
        tmp = STATIC / "_build_images.html"
        try:
            tmp.write_text(f'<body style="margin:0"><img id="l" src="{LOGO.as_uri()}" style="height:{LOGO_HEIGHT}px;display:block"></body>', encoding="utf-8")
            page.goto(tmp.as_uri())
            page.wait_for_function("document.getElementById('l').naturalWidth > 0")
            page.locator("#l").screenshot(path=str(IMG / f"prime-logo-{LOGO_HEIGHT}.png"), omit_background=True)

            tmp.write_text(OG_HTML.replace("{fonts}", (STATIC / "fonts").as_uri()).replace("{logo}", LOGO.as_uri()), encoding="utf-8")
            page.set_viewport_size({"width": 1200, "height": 630})
            page.goto(tmp.as_uri())
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(IMG / "og-raiox.png"))
        finally:
            tmp.unlink(missing_ok=True)
        browser.close()
    for name in (f"prime-logo-{LOGO_HEIGHT}.png", "og-raiox.png"):
        print(name, (IMG / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
