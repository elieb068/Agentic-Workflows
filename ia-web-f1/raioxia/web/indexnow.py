"""IndexNow: tell Bing (and the other engines that share IndexNow) which public pages changed.

Run after each deploy (deploy/deploy.sh): by default it submits the pages whose sitemap <lastmod> is today,
so an unchanged deploy sends nothing. The key file is served at /indexnow.txt (routes.py).
"""

from datetime import date, datetime
from urllib.parse import urlsplit

import httpx

from raioxia.web import seo
from raioxia.web.templating import TIMEZONE

ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_PATH = "/indexnow.txt"


def changed_paths(today: date | None = None) -> list[str]:
    today = today or datetime.now(TIMEZONE).date()
    return [path for path, updated in seo.sitemap_pages().items() if updated >= today]


def submit(base_url: str, key: str, paths: list[str], client: httpx.Client | None = None) -> int:
    """POSTs the URLs; returns the HTTP status (200/202 = accepted)."""
    payload = {
        "host": urlsplit(base_url).hostname,
        "key": key,
        "keyLocation": f"{base_url}{KEY_PATH}",
        "urlList": [f"{base_url}{path}" for path in paths],
    }
    if client is not None:
        return client.post(ENDPOINT, json=payload).status_code
    with httpx.Client(timeout=15) as http:
        return http.post(ENDPOINT, json=payload).status_code
