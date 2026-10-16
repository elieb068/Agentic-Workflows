// Runs before the AdSense loader (loaded synchronously right above it, on the report page only), so
// the personalization flag is in place before any ad request. Personalized ads only after consent
// (LGPD; see /privacidade#anuncios); the choice lives in localStorage, not in a cookie.
(function () {
  "use strict";
  var choice = null;
  try { choice = window.localStorage.getItem("raiox-ads-consent"); } catch (e) { /* storage blocked */ }
  window.adsbygoogle = window.adsbygoogle || [];
  window.adsbygoogle.requestNonPersonalizedAds = choice === "personalized" ? 0 : 1;
})();
