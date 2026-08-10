
(function () {
  "use strict";
  var TEILE = [
    "app-01-kern.js",
    "app-02-voice-zeremonie.js",
    "app-03-start-tresor-widgets.js",
    "app-04-rail-cockpit.js",
    "app-05-einstellungen.js",
    "app-06-screen-konsole.js",
    "app-07-work.js",
    "app-08-admin.js",
    "app-09-statistik-software.js",
    "app-10-post-identitaet.js",
    "app-11-messenger-notify-boot.js"
  ];
  function fallback(grund) {
    try { console.error("app-lader: Fallback auf die alte app.js —", grund); } catch (e) {}
    var s = document.createElement("script");
    s.src = "/app/app.js";
    document.head.appendChild(s);
  }
  Promise.all(TEILE.map(function (name) {
    return fetch("/app/" + name, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok || r.redirected) throw new Error(name + " → HTTP " + r.status + (r.redirected ? " (Umleitung)" : ""));
      return r.text();
    });
  })).then(function (texte) {
    var s = document.createElement("script");

    s.textContent = texte.join("") + "\n//# sourceURL=app-zusammengesetzt.js";
    document.head.appendChild(s);
  }).catch(function (err) {
    fallback(err && err.message ? err.message : String(err));
  });
})();
