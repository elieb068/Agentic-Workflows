// Progressive enhancement only: every page works without this file.
(function () {
  "use strict";

  // Forms marked data-submit-once: disable the button while the request is on its way.
  document.querySelectorAll("form[data-submit-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type=submit]");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "Enviando…";
      }
    });
  });

  // AdSense units on the report: non-personalized only (LGPD; see /privacidade), set before any request.
  var units = document.querySelectorAll("ins.adsbygoogle");
  if (units.length) {
    window.adsbygoogle = window.adsbygoogle || [];
    window.adsbygoogle.requestNonPersonalizedAds = 1;
    units.forEach(function () { window.adsbygoogle.push({}); });
  }

  // Report still processing: ask the API every 4 s and reload when the status changes.
  var pending = document.querySelector("[data-poll-report]");
  if (pending && window.fetch) {
    var url = pending.getAttribute("data-poll-report");
    var status = pending.getAttribute("data-status");
    var tries = 0;
    var tick = function () {
      tries += 1;
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && data.status !== status) { window.location.reload(); return; }
          if (tries < 150) { setTimeout(tick, 4000); }
        })
        .catch(function () { if (tries < 150) { setTimeout(tick, 8000); } });
    };
    setTimeout(tick, 4000);
  }
})();
