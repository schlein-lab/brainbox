
(function () {
  "use strict";

  try { if (window.self !== window.top) return; } catch (e) { return; }
  if (window.__ppPortalNav) return;
  window.__ppPortalNav = true;

  var HOME = "/";

  function pageIsDark() {
    function lumOf(el) {
      try {
        var m = (getComputedStyle(el).backgroundColor || "").match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        var p = m[1].split(",").map(function (x) { return parseFloat(x); });
        var a = p.length > 3 ? p[3] : 1;
        if (a < 0.4) return null;
        return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
      } catch (e) { return null; }
    }
    var lum = lumOf(document.body);
    if (lum === null) lum = lumOf(document.documentElement);
    if (lum !== null) return lum < 128;
    try { return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme:dark)").matches); } catch (e) { return false; }
  }

  function injectCSS() {
    if (document.getElementById("pp-portal-nav-css")) return;
    var css = document.createElement("style");
    css.id = "pp-portal-nav-css";
    css.textContent =
      "#pp-portal-nav{position:fixed;top:0;left:0;right:0;z-index:2147483000;display:flex;align-items:center;gap:12px;" +
      "padding:8px 14px;box-sizing:border-box;width:100%;" +
      "background:var(--ppn-bg);border-bottom:1px solid var(--ppn-bd);" +
      "backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);" +
      "font:14px/1.2 system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}" +
      "#pp-portal-nav .ppn-back{display:inline-flex;align-items:center;gap:7px;text-decoration:none;" +
      "padding:6px 14px;border-radius:9px;font-weight:600;cursor:pointer;" +
      "color:var(--ppn-ink);background:var(--ppn-btn);border:1px solid var(--ppn-bd);" +
      "transition:background .12s ease,transform .08s ease}" +
      "#pp-portal-nav .ppn-back:hover{background:var(--ppn-btnh)}" +
      "#pp-portal-nav .ppn-back:active{transform:translateY(1px)}" +
      "#pp-portal-nav .ppn-back:focus-visible{outline:2px solid var(--ppn-acc);outline-offset:2px}" +
      "#pp-portal-nav .ppn-here{color:var(--ppn-muted);font-size:12.5px;font-weight:500;" +
      "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}" +
      "#pp-portal-nav.ppn-light{--ppn-bg:rgba(245,247,251,.94);--ppn-bd:rgba(20,30,55,.12);" +
      "--ppn-ink:#182031;--ppn-btn:rgba(20,30,55,.06);--ppn-btnh:rgba(20,30,55,.11);--ppn-muted:#5f6d87;--ppn-acc:#3a55d0}" +
      "#pp-portal-nav.ppn-dark{--ppn-bg:rgba(14,20,32,.94);--ppn-bd:rgba(255,255,255,.13);" +
      "--ppn-ink:#e8eef8;--ppn-btn:rgba(255,255,255,.08);--ppn-btnh:rgba(255,255,255,.14);--ppn-muted:#93a0b8;--ppn-acc:#6e88ff}";
    (document.head || document.documentElement).appendChild(css);
  }

  function reserveSpace(bar) {
    var h = bar.offsetHeight || 40;

    var de = document.documentElement;
    de.style.boxSizing = "border-box";
    de.style.paddingTop = h + "px";

    try {
      var all = (document.body || de).querySelectorAll("*");
      for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.id === "pp-portal-nav") continue;
        var cs = getComputedStyle(el);
        if (cs.position === "sticky" && (cs.top === "0px" || cs.top === "0")) el.style.top = h + "px";
      }
    } catch (e) {}
  }

  function build() {
    if (document.getElementById("pp-portal-nav")) return;
    injectCSS();

    var bar = document.createElement("div");
    bar.id = "pp-portal-nav";
    bar.className = pageIsDark() ? "ppn-dark" : "ppn-light";
    var back = document.createElement("a");
    back.className = "ppn-back";
    back.href = HOME;
    back.setAttribute("aria-label", "Zurück zum Portal-Hauptmenü");
    back.textContent = "← Portal";
    bar.appendChild(back);
    var here = document.createElement("span");
    here.className = "ppn-here";

    here.textContent = (document.title || "").replace(/\s*[·—|].*$/, "").trim();
    bar.appendChild(here);

    var body = document.body || document.documentElement;
    body.insertBefore(bar, body.firstChild);
    reserveSpace(bar);
    setTimeout(function () { reserveSpace(bar); }, 300);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
