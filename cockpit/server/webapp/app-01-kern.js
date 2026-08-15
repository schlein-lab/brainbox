
(function () {
"use strict";

const TOKEN = window.PP_WSTOKEN || "";
const USER  = window.PP_USER || "";
const VOICE_OK = window.PP_VOICE !== false;

const $  = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
function el(tag, props, kids) {
  const n = document.createElement(tag);
  if (props) for (const k in props) {
    if (k === "class") n.className = props[k];
    else if (k === "text") n.textContent = props[k];
    else if (k === "html") n.innerHTML = props[k];
    else if (k.slice(0, 2) === "on" && typeof props[k] === "function") n.addEventListener(k.slice(2), props[k]);
    else if (props[k] === true) n.setAttribute(k, "");
    else if (props[k] != null && props[k] !== false) n.setAttribute(k, props[k]);
  }
  if (kids) (Array.isArray(kids) ? kids : [kids]).forEach(c => c != null && n.append(c.nodeType ? c : document.createTextNode(c)));
  return n;
}
function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sep = path.indexOf("?") >= 0 ? "&" : "?";
  return proto + "://" + location.host + path + sep + "token=" + encodeURIComponent(TOKEN);
}

let _authLost = false;
function sessionLost() {
  if (_authLost) return; _authLost = true;
  const b = el("div", { class: "sess-lost", text: "⚠ Sitzung abgelaufen (Portal neu gestartet) — hier tippen zum Neu-Anmelden",
    onclick: () => location.href = "/login" });
  document.body.appendChild(b);
}
function fmtWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return "";
  const now = Date.now(), diff = (now - d.getTime()) / 1000, p = (n) => String(n).padStart(2, "0");
  const hm = p(d.getHours()) + ":" + p(d.getMinutes());
  if (diff < 45) return "gerade eben";
  if (diff < 3600) return "vor " + Math.floor(diff / 60) + " Min";
  if (d.toDateString() === new Date(now).toDateString()) return hm;
  return p(d.getDate()) + "." + p(d.getMonth() + 1) + ". " + hm;
}
function fmtModel(m) {
  if (!m) return "";
  const s = String(m).toLowerCase();
  if (s.includes("opus")) return "Opus";
  if (s.includes("sonnet")) return "Sonnet";
  if (s.includes("haiku")) return "Haiku";
  if (s.includes("gemini")) { const v = s.match(/gemini[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*[- ]?\s*(pro|flash(?:-lite)?)?/); return v ? ("Gemini " + v[1] + (v[2] ? " " + v[2] : "")).trim() : "Gemini"; }
  if (s.includes("codex")) return "Codex";
  if (s.includes("gpt")) return String(m);
  const raw = String(m).replace(/^ollama\s*·?\s*/i, "");
  return raw.length > 24 ? raw.slice(0, 24) + "…" : raw;
}
async function api(path, opts) {
  let r;
  try { r = await fetch(path, opts || {}); }
  catch (e) { return { ok: false, _neterr: true, error: String(e) }; }
  const ct = r.headers.get("Content-Type") || "";
  const isJson = ct.indexOf("application/json") >= 0;

  if (r.status === 401 || (r.status === 403 && !isJson)) { sessionLost(); return { ok: false, _auth: false }; }
  if (isJson) { try { return await r.json(); } catch (e) { return { ok: false }; } }
  return r;
}
const jget  = (p) => api(p);
const jpost = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function toast(msg) {
  const t = el("div", { class: "toast", text: msg });
  $("#toastHost").appendChild(t);
  setTimeout(() => t.remove(), 2400);
}

const Theme = {
  init() {
    const saved = localStorage.getItem("pp-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    this.syncMeta();
    $("#themeBtn").addEventListener("click", () => this.toggle());
  },
  current() {
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr) return attr;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  },
  toggle() {
    const next = this.current() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("pp-theme", next);
    this.syncMeta();
    if (Console.term) Console.applyTermTheme();
  },
  syncMeta() {
    const bg = getComputedStyle(document.body).backgroundColor || "#f8fafc";
    const m = $("#themeColor"); if (m) m.setAttribute("content", bg);
  }
};

const LENS_TITLE = { start: "🏠 Start", screen: "🖥 Screen", console: "▟ Console", work: "📋 Work", software: "📦 Software" };

const MOBILE_MQ = window.matchMedia("(max-width: 640px)");
function isMobile() { return MOBILE_MQ.matches; }
const Router = {
  cur: "start",
  init() {
    $$("[data-go]").forEach(b => b.addEventListener("click", () => this.go(b.getAttribute("data-go"))));
    $$(".nav-item").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); this.go(a.getAttribute("data-lens")); }));
    window.addEventListener("hashchange", () => this.fromHash());
    this.fromHash();
  },
  fromHash() {
    const h = (location.hash || "#start").slice(1);
    const [lens, sub] = h.split(":");
    this.go(lens + (sub ? ":" + sub : ""), true);
  },
  go(spec, fromHash) {
    const [lens, sub] = String(spec || "start").split(":");

    if (lens === "screen") { lensGone("screen"); this.go("sessions", fromHash); return; }

    if (lens === "msgr" && !isMobile()) { this.go("start", fromHash); return; }

    if (!LENS_TITLE[lens]) {
      if (typeof lensGone === "function" && lensGone(lens)) this.go(lens === "devices" ? "settings" : "start", fromHash);
      return;
    }

    if (this.cur !== lens) { const prev = LENSES[this.cur]; if (prev && prev.hide) prev.hide(); }
    this.cur = lens;
    $$(".lens").forEach(s => s.classList.toggle("active", s.id === "lens-" + lens));
    $$(".nav-item").forEach(a => a.classList.toggle("active", a.getAttribute("data-lens") === lens));

    if (isMobile()) {
      const act = $(".nav-item.active");
      try { if (act && act.scrollIntoView) act.scrollIntoView({ inline: "center", block: "nearest" }); } catch (e) {}
    }
    $("#lensTitle").textContent = LENS_TITLE[lens];
    if (!fromHash) location.hash = spec;
    const L = LENSES[lens]; if (L && L.show) L.show(sub);

    if (typeof Rail !== "undefined" && Rail.render) { try { Rail.render(); } catch (e) {} }
  }
};

const LENS_GONE = {
  terminal: "Host-Shell entfernt — Arbeit läuft in einer Session (Reiter „Sessions“), Box-Verwaltung per SSH.",
  shell:    "Host-Shell entfernt — Arbeit läuft in einer Session (Reiter „Sessions“), Box-Verwaltung per SSH.",
  konsole:  "Host-Shell entfernt — Arbeit läuft in einer Session (Reiter „Sessions“), Box-Verwaltung per SSH.",
  screen:   "Der „Screen“-Reiter ist in „Sessions“ aufgegangen — jede Session startet bei Bedarf ihre eigene GUI. Inhalte auf den Fernseher laufen über den Medienserver.",
  browser:  "Kein eigener Box-Browser mehr — er lebt künftig in der Session-GUI. Dateien/Medien im LAN über den Medienserver (DLNA/SMB)."
};

function lensGone(name) {
  const m = LENS_GONE[String(name || "").toLowerCase()];
  if (!m) return false;
  try { toast(m); } catch (e) {}
  return true;
}

function mapLens(name) {
  switch (String(name || "").toLowerCase()) {
    case "landing": case "start": case "home": return "start";
    case "chat": case "cockpit": case "sessions": return "sessions";
    case "queue": case "work": case "jobs": return "work";
    case "messenger": case "nachrichten": return "msgr";
    default: return null;
  }
}

function claudeSignin(statusSel) {
  try { Router.go("settings"); } catch (e) {}
  try {
    if (typeof IS_ADMIN !== "undefined" && IS_ADMIN && typeof Settings !== "undefined") {
      setTimeout(() => Settings.brainStart(), 300);
    } else if (typeof Settings !== "undefined") {

      setTimeout(() => Settings.myLlmStart(), 300);
    }
  } catch (e) {}
}

function openSessionTerminal(sid) {
  Router.go("sessions");
  try { const f = $("#sessFrame"); if (f && f.contentWindow) f.contentWindow.postMessage({ type: "pp-open-session", sid: sid }, "*"); } catch (e) {}

  try { if (Messenger && Messenger.host) Messenger.focusSession(sid); } catch (e) {}
}

const Convo = {
  init() {
    this.convo = $("#convo"); this.log = $("#transcript");

    this.add("bot", "Sag mir, was ich tun soll — oder unterhalte dich. Ich kann jedes Programm bedienen, Pipelines bauen und Artefakte erzeugen.");
    Links.init(); Drop.init();
  },
  autospeak() { const c = $("#autospeak"); return c && c.checked; },
  add(who, text) {
    const d = el("div", { class: "msg " + who, text: text });
    this.log.appendChild(d); this.log.scrollTop = this.log.scrollHeight; return d;
  },

  _sq: [], _sqActive: false,
  async _ttsPlay(text) {
    const r = await jpost("/api/tts", { text });
    if (!(r && r.headers) || (r.headers.get("Content-Type") || "").indexOf("audio") < 0) return;
    let url; try { url = URL.createObjectURL(await r.blob()); } catch (e) { return; }
    await new Promise((res) => {
      const a = new Audio(url);
      const done = () => { try { URL.revokeObjectURL(url); } catch (e) {} res(); };
      a.onended = done; a.onerror = done; a.play().catch(done);
    });
  },
  playChime() {
    try {
      const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      const ac = new AC(), t0 = ac.currentTime;
      [[784, 0.0], [1046.5, 0.13]].forEach(([f, dt]) => {
        const o = ac.createOscillator(), g = ac.createGain();
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(ac.destination);
        const s = t0 + dt;
        g.gain.setValueAtTime(0.0001, s);
        g.gain.exponentialRampToValueAtTime(0.12, s + 0.03);
        g.gain.exponentialRampToValueAtTime(0.0006, s + 0.30);
        o.start(s); o.stop(s + 0.33);
      });
      setTimeout(() => { try { ac.close(); } catch (e) {} }, 900);
    } catch (e) {}
  },
  async _sqDrain() {
    if (this._sqActive) return; this._sqActive = true;
    try {
      while (this._sq.length) {
        const item = this._sq.shift();
        if (item === "__CHIME__") { this.playChime(); await new Promise((r) => setTimeout(r, 320)); }
        else await this._ttsPlay(item);
      }
    } finally { this._sqActive = false; }
  },
  speak(text) { if (!text) return; this._sq.push(text); this._sqDrain(); },
  chime() { this._sq.push("__CHIME__"); this._sqDrain(); },
  say(text) { const t = this.add("bot", text); if (this.autospeak()) this.speak(text); return t; },
  async talk(text) {
    if (!text) return;
    this.add("me", text);
    if (Ceremony.active) return Ceremony.turn(text);
    const t = this.add("bot", "…");
    const d = await jpost("/api/voice", { text });
    if (!d || d._neterr) { t.textContent = "Fehler: keine Antwort"; return; }
    if (d.action === "summon" && d.lens) { if (!lensGone(d.lens)) { const m = mapLens(d.lens); if (m) Router.go(m); } }
    if (d.action === "ceremony") { Ceremony.arm(d); t.remove(); return; }
    if (Array.isArray(d.actions)) runActions(d.actions);
    const auto = this.autospeak();
    const first = d.speak || d.reply || "";
    t.textContent = first || "(keine Antwort)";
    if (auto && first) this.speak(first);
    if (d.busy) this._voiceTail(t, (d.cursor | 0), auto);
    else if (auto && first) this.chime();
  },
  async _voiceTail(bubble, cursor, auto) {
    let off = cursor | 0, idle = 0;
    for (let i = 0; i < 600 && idle < 40; i++) {
      await new Promise((r) => setTimeout(r, 700));
      const d = await jget("/api/voice/tail?off=" + off);
      if (!d || d._neterr) { idle++; continue; }
      if (Array.isArray(d.texts) && d.texts.length) {
        for (const s of d.texts) {
          bubble.textContent += (bubble.textContent && bubble.textContent !== "…" ? " " : "") + s;
          if (auto) this.speak(s);
        }
        off = d.off | 0; idle = 0;
      } else idle++;
      if (!d.busy) { if (auto) this.chime(); return; }
    }
    if (auto) this.chime();
  }
};

const StandWache = {
  basis: null, letzt: 0, banner: null, still: null,
  tick() {
    const jetzt = Date.now();
    if (jetzt - this.letzt < 60000) return;
    this.letzt = jetzt;
    jget("/api/version").then((d) => {
      const stand = d && d.webapp_stand;
      if (!stand) return;
      if (this.basis === null) { this.basis = stand; return; }
      if (stand !== this.basis && stand !== this.still && !this.banner) this.zeig(stand);
    }).catch(() => {});
  },
  zeig(stand) {
    const b = el("div", { class: "stand-neu", role: "status" });
    b.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;" +
      "background:var(--bg-elevated,#1e293b);color:var(--text,#e2e8f0);border:1px solid var(--line,#475569);" +
      "border-radius:10px;padding:8px 14px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.35);" +
      "display:flex;gap:10px;align-items:center;cursor:pointer";
    b.appendChild(el("span", { text: "Portal wurde aktualisiert — neu laden" }));
    const x = el("button", { text: "✕", title: "Später (Banner schließen)" });
    x.style.cssText = "background:none;border:none;color:inherit;cursor:pointer;font-size:13px;padding:0";
    x.onclick = (e) => { e.stopPropagation(); this.still = stand; b.remove(); this.banner = null; };
    b.appendChild(x);
    b.onclick = () => location.reload();
    document.body.appendChild(b);
    this.banner = b;
  },
};
