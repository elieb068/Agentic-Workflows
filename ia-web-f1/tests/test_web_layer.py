import re

import pytest

from raioxia.web.assets import STATIC_DIR, TEMPLATES_DIR, built_css_digest, css_sources_digest, static_url


def test_committed_css_is_up_to_date():
    assert built_css_digest() == css_sources_digest(), "templates or styles changed: run `python scripts/build_css.py`"


def test_static_url_has_content_hash():
    url = static_url("app.css")
    assert re.fullmatch(r"/static/app\.css\?v=[0-9a-f]{10}", url)
    assert url == static_url("app.css")


@pytest.mark.parametrize("path", sorted(p.relative_to(TEMPLATES_DIR).as_posix() for p in TEMPLATES_DIR.rglob("*.html")))
def test_templates_are_csp_clean(path):
    """CSP allows no inline styles or scripts: everything must be classes and external files."""
    html = (TEMPLATES_DIR / path).read_text(encoding="utf-8")
    assert not re.search(r"\sstyle=", html), "inline style attribute"
    assert not re.search(r"<style", html), "inline <style>"
    # JSON-LD blocks are data, never executed, so CSP doesn't apply to them.
    assert not re.search(r'<script(?![^>]*\b(src=|type="application/ld\+json"))', html), "inline <script>"
    assert not re.search(r"\son[a-z]+=", html), "inline event handler"


def test_fonts_are_self_hosted_with_licenses():
    for name in ("inter", "outfit", "jetbrains-mono"):
        assert (STATIC_DIR / "fonts" / f"{name}-latin-wght.woff2").stat().st_size > 10_000
        assert "SIL Open Font License" in (STATIC_DIR / "fonts" / f"{name}-LICENSE.txt").read_text(encoding="utf-8")


@pytest.mark.db
def test_static_files_and_caching(client):
    css = client.get(static_url("app.css"))
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert css.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/img/prime-logo.png").headers["cache-control"] == "public, max-age=86400"
    font = client.get("/static/fonts/inter-latin-wght.woff2")  # unhashed URL (preload, @font-face): fonts never change
    assert font.status_code == 200 and font.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.db
def test_security_headers_and_csp(client):
    response = client.get("/healthz")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in csp
    assert "style-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.db
def test_not_found_html_for_pages_json_for_api(client):
    page = client.get("/nao-existe")
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")
    assert "Página não encontrada" in page.text and 'lang="pt-BR"' in page.text
    api = client.get("/api/nao-existe")
    assert api.status_code == 404 and api.json()["error"] == "http_error"
    assert client.post("/healthz").status_code == 405


@pytest.mark.db
def test_layout_has_brand_and_legal_footer(client, pipeline_env, db):
    page = client.get("/nao-existe").text
    assert "Prime Solutions Tecnologia e Serviços de Apoio Administrativo" in page and "43.757.807/0001-86" in page
    assert static_url("img/prime-logo-60.png") in page and static_url("app.css") in page
    for link in ("/guias", "/guias/robos-de-ia", "/privacidade", "/termos", "/bot", "/excluir-dados"):
        assert f'href="{link}"' in page
