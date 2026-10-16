import re

import pytest
from sqlalchemy import select

from raioxia.db import Analysis

pytestmark = pytest.mark.db

FORM = {"url": "exemplo.com.br", "email": "ana@exemplo.com.br", "accepted_terms": "1", "cf-turnstile-response": "tok"}
CLIENT = "ca-pub-5217885742353376"


def report_location(client, env) -> str:
    client.post("/pedido", data=FORM)
    email = next(e for e in reversed(env.sender.sent) if e.subject.startswith("Confirme"))
    link = re.search(r"https://raiox\.primesolutions\.net\.br(/confirmar/\S+)", email.text).group(1)
    return client.get(link).headers["location"]


def test_without_adsense_the_report_shows_placeholders_and_strict_csp(client, pipeline_env):
    response = client.get(report_location(client, pipeline_env))
    assert response.text.count('data-ad-slot="report-') == 2 and "adsbygoogle" not in response.text
    assert "script-src 'self' https://challenges.cloudflare.com" in response.headers["content-security-policy"]


def test_before_approval_loads_adsense_but_shows_no_units(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    response = client.get(report_location(client, pipeline_env))
    html, csp = response.text, response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={CLIENT}" crossorigin="anonymous" nonce="{nonce}"' in html
    assert f'js/app.js?v=' in html and f'defer nonce="{nonce}"' in html
    assert "'strict-dynamic'" in csp and "frame-ancestors 'none'" in csp
    assert "<ins" not in html and 'data-ad-slot="report-' not in html  # no empty boxes in production


def test_with_slot_ids_renders_real_units(client, pipeline_env):
    pipeline_env.settings.adsense_client = CLIENT
    pipeline_env.settings.adsense_slot_top = "1111111111"
    pipeline_env.settings.adsense_slot_bottom = "2222222222"
    html = client.get(report_location(client, pipeline_env)).text
    units = re.findall(r'<ins class="adsbygoogle block" data-ad-client="([^"]+)" data-ad-slot="(\d+)"', html)
    assert units == [(CLIENT, "1111111111"), (CLIENT, "2222222222")]
    assert "nonce" not in client.get("/r/desconhecido").text  # unknown report: plain 404, no ads


def test_no_ads_where_there_is_no_content(client, pipeline_env, db):
    pipeline_env.settings.adsense_client = CLIENT
    pipeline_env.settings.adsense_slot_top = "1111111111"
    client.post("/pedido", data=FORM)
    pending = db.scalars(select(Analysis)).one()
    for path in ("/", f"/r/{pending.report_token}", "/privacidade", "/bot"):
        response = client.get(path)
        assert "adsbygoogle" not in response.text, path
        assert "'strict-dynamic'" not in response.headers["content-security-policy"], path


def test_robots_lets_the_adsense_crawler_read_reports(client):
    robots = client.get("/robots.txt").text
    assert "User-agent: Mediapartners-Google\nAllow: /" in robots
    assert "User-agent: *\nAllow: /\nDisallow: /r/" in robots
