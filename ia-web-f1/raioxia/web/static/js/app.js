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

  // Ad personalization choice (the flag itself is set earlier by ads-init.js, before the AdSense loader).
  var CONSENT_KEY = "raiox-ads-consent";
  var readChoice = function () { try { return window.localStorage.getItem(CONSENT_KEY); } catch (e) { return null; } };
  var saveChoice = function (value) { try { window.localStorage.setItem(CONSENT_KEY, value); } catch (e) { /* private mode */ } };
  var choice = readChoice();
  var units = document.querySelectorAll("ins.adsbygoogle");
  var consent = document.querySelector("[data-ads-consent]");
  if (consent) {
    if (!choice) { consent.hidden = false; }
    consent.querySelectorAll("[data-ads-choice]").forEach(function (button) {
      button.addEventListener("click", function () {
        var value = button.getAttribute("data-ads-choice");
        var changed = value !== (choice || "npa");
        saveChoice(value);
        consent.hidden = true;
        if (changed && units.length) { window.location.reload(); }  // ads already requested with the old setting
      });
    });
    var preferences = document.querySelector("[data-ads-preferences]");
    if (preferences) {
      preferences.addEventListener("click", function () { consent.hidden = false; consent.querySelector("button").focus(); });
    }
  }
  // Ask AdSense to fill each unit. A failing unit (e.g. no fill, blocked) must not stop the rest of this file.
  if (units.length) {
    window.adsbygoogle = window.adsbygoogle || [];
    units.forEach(function () { try { window.adsbygoogle.push({}); } catch (e) { /* ignore */ } });
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
