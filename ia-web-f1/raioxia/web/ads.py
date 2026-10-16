"""Google AdSense: on the finished report (ad units) and, with Auto ads, on the landing page too.

Ads are non-personalized unless the visitor accepts personalized ads in the notice on the report.
static/js/ads-init.js sets the personalization flag (consent kept in localStorage) before the
loader runs. That page gets its own Content-Security-Policy: a per-response nonce with 'strict-dynamic', so the
AdSense loader and the scripts it pulls in run, while every other page keeps the strict policy.
"""

import secrets

from raioxia.settings import Settings

ADSENSE_SCRIPT = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"

# Ads render in Google iframes and fetch images, frames and beacons from many Google hosts.
ADS_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'nonce-{nonce}' 'strict-dynamic' https:",
    "frame-src https:",
    "style-src 'self' 'unsafe-inline'",  # the loader styles the ad containers
    "img-src 'self' data: https:",
    "font-src 'self'",
    "connect-src 'self' https:",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


def ads_for(settings: Settings) -> dict | None:
    """Template context for the report's ad slots, or None when AdSense is not configured."""
    if not settings.adsense_client:
        return None
    top, bottom, auto = settings.adsense_slot_top, settings.adsense_slot_bottom, settings.adsense_auto_ads
    return {
        "client": settings.adsense_client,
        "top": top,
        "bottom": bottom,
        "auto": auto,
        # Ask about personalization only when ads can actually appear (units, or Auto ads).
        "consent": bool(top or bottom or auto),
        "nonce": secrets.token_urlsafe(16),
        "script": f"{ADSENSE_SCRIPT}?client={settings.adsense_client}",
    }


def csp_for(ads: dict) -> str:
    return ADS_CSP.format(nonce=ads["nonce"])
