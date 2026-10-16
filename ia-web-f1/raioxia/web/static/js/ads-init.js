// Runs before the AdSense loader, so the personalization flag is in place before any ad request.
// Personalized ads only after consent (LGPD; see /privacidade#anuncios); the choice lives in
// localStorage, not in a cookie.
// With data-ads-loader (landing page), this file also loads AdSense itself, on the visitor's first
// scroll, tap or key press: the loader is heavy and would otherwise delay the first paint (LCP).
// A script created here runs under the page's 'strict-dynamic' CSP because this one carries the nonce.
(function () {
  "use strict";
  var choice = null;
  try { choice = window.localStorage.getItem("raiox-ads-consent"); } catch (e) { /* storage blocked */ }
  window.adsbygoogle = window.adsbygoogle || [];
  window.adsbygoogle.requestNonPersonalizedAds = choice === "personalized" ? 0 : 1;

  var me = document.currentScript;
  var src = me && me.getAttribute("data-ads-loader");
  if (!src) { return; }
  var events = ["scroll", "pointerdown", "keydown", "touchstart"];
  var loaded = false;
  var load = function () {
    if (loaded) { return; }
    loaded = true;
    events.forEach(function (name) { window.removeEventListener(name, load, { passive: true }); });
    var script = document.createElement("script");
    script.async = true;
    script.crossOrigin = "anonymous";
    script.src = src;
    document.head.appendChild(script);
  };
  events.forEach(function (name) { window.addEventListener(name, load, { passive: true }); });
})();
