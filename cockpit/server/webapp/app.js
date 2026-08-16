
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

const NavVoice = {
  _rec: null, _live: false, _gen: 0, _keyHeld: false, el: {},
  supported() { return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder); },
  init() {
    const bar = $("#voiceBar"); if (!bar) return;
    this.el = { bar, sel: $("#voiceTarget"), ptt: $("#navPtt"), live: $("#navLive") };
    if (!this.supported()) {
      const why = location.protocol === "https:" ? "Dieser Browser kann kein Mikrofon aufnehmen."
        : "Mikrofon braucht HTTPS — Portal über https:// öffnen (Zertifikat unter /trust).";
      this.el.ptt.disabled = this.el.live.disabled = true; bar.title = why; return;
    }
    this.refreshTargets();
    this.el.sel.addEventListener("focus", () => this.refreshTargets());
    this.el.sel.addEventListener("change", () => { try { localStorage.setItem("pp-voice-target", this.el.sel.value); } catch (e) {} });
    this.el.ptt.addEventListener("pointerdown", (e) => { e.preventDefault(); this.pttDown(); });
    ["pointerup", "pointercancel", "pointerleave"].forEach(ev => this.el.ptt.addEventListener(ev, () => this.pttUp()));
    this.el.ptt.addEventListener("contextmenu", (e) => e.preventDefault());
    this.el.live.addEventListener("click", () => this.toggleLive());
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.code === "Space") {
        e.preventDefault();
        if (!e.repeat && !this._keyHeld) { this._keyHeld = true; this.pttDown(); }
      } else if (e.key === "Escape" && this._live) this.toggleLive();
    }, true);
    document.addEventListener("keyup", (e) => {
      if (this._keyHeld && (e.code === "Space" || e.key === "Control")) { this._keyHeld = false; this.pttUp(); }
    }, true);

    window.addEventListener("message", (e) => {
      const d = e.data || {};
      if (d && d.type === "pp-ptt") { if (d.down) this.pttDown(); else this.pttUp(); }
    });
  },
  async refreshTargets() {
    const sel = this.el.sel;
    let saved = null; try { saved = localStorage.getItem("pp-voice-target"); } catch (e) {}
    const cur = sel.value || saved || "__voice__";
    const r = await jget("/api/sessions");
    const list = ((r && r.ok && r.sessions) || []).filter(s => !s.archived);
    const opts = [{ v: "__voice__", t: "🎙 Sprachassistent" }]
      .concat(list.map(s => ({ v: s.sid || s.id, t: "🗂 " + (s.title || s.sid || s.id) })));
    sel.innerHTML = opts.map(o => `<option value="${esc(o.v)}">${esc(o.t)}</option>`).join("");
    sel.value = opts.some(o => o.v === cur) ? cur : "__voice__";
  },
  target() { return (this.el.sel && this.el.sel.value) || "__voice__"; },

  async _record(vad, stream) {
    if (this._rec) return null;
    const own = !stream;
    if (!stream) {
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (e) { toast("Mikrofon: " + (e.message || "Zugriff verweigert")); return null; }
    }
    return await new Promise(res => {
      const mr = new MediaRecorder(stream), chunks = [];
      let ac = null, spoke = false, quietSince = 0; const started = Date.now();
      this._rec = mr; this.el.ptt.classList.add("rec");
      const cleanup = () => {
        this._rec = null; this.el.ptt.classList.remove("rec");
        if (ac) { try { ac.close(); } catch (e) {} }
        if (own) stream.getTracks().forEach(t => t.stop());
      };
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = () => { cleanup(); res(chunks.length ? new Blob(chunks, { type: chunks[0].type || "audio/webm" }) : null); };
      mr.start();
      if (vad) {

        const AC = window.AudioContext || window.webkitAudioContext;
        ac = new AC();
        const srcN = ac.createMediaStreamSource(stream);
        const sp = ac.createScriptProcessor(4096, 1, 1);
        const mute = ac.createGain(); mute.gain.value = 0;
        srcN.connect(sp); sp.connect(mute); mute.connect(ac.destination);
        sp.onaudioprocess = (ev) => {
          if (mr.state !== "recording") { try { sp.disconnect(); } catch (e) {} return; }
          const buf = ev.inputBuffer.getChannelData(0);
          let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
          const rms = Math.sqrt(s / buf.length), now = Date.now();
          if (rms > 0.015) { spoke = true; quietSince = 0; }
          else if (spoke && !quietSince) quietSince = now;
          if ((spoke && quietSince && now - quietSince > 1400) || (!spoke && now - started > 8000) || now - started > 45000) mr.stop();
        };
      } else {
        setTimeout(() => { if (mr.state === "recording") mr.stop(); }, 45000);
      }
    });
  },
  async pttDown() {
    if (this._rec || this._live) return;
    const blob = await this._record(false);
    if (!blob || blob.size < 1200) return;
    const text = await this.stt(blob);
    if (text) { const sent = await this.deliver(text); this._speakVoiceReply(sent); }
  },
  pttUp() { if (this._rec && this._rec.state === "recording") this._rec.stop(); },
  async stt(blob) {
    const r = await api("/api/stt", { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob });
    let j = r; if (r && r.json) { try { j = await r.json(); } catch (e) { j = null; } }
    if (j && j.error) { toast(j.error); return null; }
    const text = ((j && j.text) || "").trim();
    if (!text) { toast("Nichts verstanden — bitte noch einmal."); return null; }
    return text;
  },
  async deliver(text) {
    const t = this.target();
    toast("🎙 " + (text.length > 90 ? text.slice(0, 90) + "…" : text));
    if (t === "__voice__") {
      const d = await jpost("/api/voice", { text });
      if (d && d.action === "ceremony") { Ceremony.arm(d); return { voice: true, d, ceremony: true }; }
      if (d && Array.isArray(d.actions)) runActions(d.actions);
      return { voice: true, d };
    }
    const d = await jpost("/api/session/say", { sid: t, text });
    if (!(d && d.ok)) toast("Zustellung: " + ((d && d.error) || "fehlgeschlagen"));
    return { voice: false, d, sid: t, ok: !!(d && d.ok) };
  },
  _speakVoiceReply(sent) {

    if (!(sent && sent.voice && sent.d) || sent.ceremony) return;
    const first = sent.d.speak || sent.d.reply || "";
    if (first) Convo.speak(first);
    if (sent.d.busy) this._tailSpeak(sent.d.cursor | 0, this._gen, null);
  },
  async _tailSpeak(cursor, gen, liveGuard) {
    let off = cursor | 0, idle = 0;
    for (let i = 0; i < 600 && idle < 40; i++) {
      if (liveGuard && (!this._live || gen !== this._gen)) return;
      await new Promise(r => setTimeout(r, 700));
      const d = await jget("/api/voice/tail?off=" + off);
      if (!d || d._neterr) { idle++; continue; }
      if (Array.isArray(d.texts) && d.texts.length) { d.texts.forEach(s => Convo.speak(s)); off = d.off | 0; idle = 0; }
      else idle++;
      if (!d.busy) { Convo.chime(); return; }
    }
  },

  async toggleLive() {
    if (this._live) {
      this._live = false; this._gen++;
      this.el.live.classList.remove("on", "busy");
      if (this._rec && this._rec.state === "recording") this._rec.stop();
      toast("Live-Gespräch beendet"); return;
    }
    this._live = true; const gen = ++this._gen;
    this.el.live.classList.add("on");
    toast("🎧 Live-Gespräch — einfach sprechen (Esc beendet)");
    let leer = 0, liveStream = null;
    try { liveStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch (e) { toast("Mikrofon: " + (e.message || "Zugriff verweigert")); this._live = false; this.el.live.classList.remove("on"); return; }
    while (this._live && gen === this._gen) {
      const blob = await this._record(true, liveStream);
      if (!this._live || gen !== this._gen) break;
      if (!blob || blob.size < 1200) { if (++leer >= 3) { toast("Lange nichts gehört — Live-Gespräch beendet"); break; } continue; }
      leer = 0;
      const text = await this.stt(blob);
      if (!this._live || gen !== this._gen) break;
      if (!text) continue;
      this.el.live.classList.add("busy");
      const sent = await this.deliver(text);
      await this._collectReply(sent, gen);
      await this._waitSpeechDone();
      this.el.live.classList.remove("busy");
    }
    if (liveStream) liveStream.getTracks().forEach(t => t.stop());
    if (gen === this._gen) { this._live = false; this.el.live.classList.remove("on", "busy"); }
  },
  async _collectReply(sent, gen) {
    if (sent.ceremony) return;
    if (sent.voice) {
      const d = sent.d || {};
      const first = d.speak || d.reply || "";
      if (first) Convo.speak(first);
      if (d.busy) await this._tailSpeak(d.cursor | 0, gen, true);
      return;
    }
    if (!sent.ok) return;

    let cursor = 0;
    try { const r0 = await jget("/api/transcript?sid=" + encodeURIComponent(sent.sid)); cursor = (r0 && r0.next) || 0; } catch (e) {}
    const t0 = Date.now(); let gotAt = 0;
    while (this._live && gen === this._gen && Date.now() - t0 < 120000) {
      await new Promise(r => setTimeout(r, 1200));
      const r = await jget("/api/transcript?sid=" + encodeURIComponent(sent.sid) + "&since=" + cursor);
      if (!r || r._neterr) continue;
      const turns = ((r && r.turns) || []).filter(x => x.role === "assistant" && (x.text || "").trim());
      if (turns.length) { turns.forEach(x => Convo.speak(x.text)); cursor = (r.next != null ? r.next : cursor); gotAt = Date.now(); }
      else if (gotAt && Date.now() - gotAt > 5000) { Convo.chime(); return; }
    }
    if (!gotAt) toast("Keine Antwort erhalten — ich höre weiter zu.");
  },
  async _waitSpeechDone() {

    while (this._live && (Convo._sqActive || Convo._sq.length)) await new Promise(r => setTimeout(r, 250));
  },
};

function runActions(actions) {
  actions.forEach(a => {
    if (typeof a === "string") { const n = a.replace(/^summon:/, ""); if (lensGone(n)) return; const m = mapLens(n); if (m) Router.go(m); return; }
    if (!a || typeof a !== "object") return;
    if ((a.action === "summon" || a.action === "navigate") && (a.lens || a.screen)) { if (lensGone(a.lens || a.screen)) return; const m = mapLens(a.lens || a.screen); if (m) Router.go(m); }
    else if (a.verb) agentExec(a.verb, a.args);
  });
}

async function agentExec(verb, args) {
  const d = await jpost("/api/agent/exec", { verb, args: args || {} });
  if (!d) return d;
  if (d.spoken) { Convo.add("bot", d.spoken); if (Convo.autospeak()) Convo.speak(d.spoken); }
  else if (d.result != null) toast(typeof d.result === "string" ? d.result : "ok");
  if (Array.isArray(d.actions)) runActions(d.actions);
  return d;
}

const Ceremony = {
  active: null,
  arm(d) {
    this.active = { re: d.re, verb: d.verb, readback: d.readback || {}, hold_ms: d.hold_ms || 10000, state: "prompted", speak: d.speak };

    this.active.webauthn = d.webauthn || null;
    this.active.nonce = (d.challenge && d.challenge.nonce) || "";
    const rb = d.readback || {}; let facts = "⚠ " + (d.verb || "irreversibel");
    if (rb.recipient) facts += " · " + rb.recipient;
    if (rb.subject) facts += " · Betreff: " + rb.subject;
    if (rb.digest && rb.digest.length) facts += " · " + rb.digest.join(" · ");
    const t = el("div", { class: "msg cer", text: (d.speak || "Bestätigung nötig.") + "   [" + facts + "]" });
    Convo.log.appendChild(t); Convo.log.scrollTop = Convo.log.scrollHeight;
    if (d.webauthn && window.PublicKeyCredential) {
      const b = el("button", { class: "btn sm", style: "margin:6px 0", text: "🔐 Mit Passkey bestätigen" });
      b.addEventListener("click", () => this.confirmWithPasskey());
      Convo.log.appendChild(b); Convo.log.scrollTop = Convo.log.scrollHeight;
    }
    if (Convo.autospeak() && d.speak) Convo.speak(d.speak);
    Work.renderNeedsYou();
  },
  _b64uToBuf(s) { s = String(s).replace(/-/g, "+").replace(/_/g, "/"); const p = s.length % 4; if (p) s += "=".repeat(4 - p); const bin = atob(s), b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return b.buffer; },
  _bufToB64u(buf) { const b = new Uint8Array(buf); let s = ""; for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); },
  async confirmWithPasskey() {
    const c = this.active; if (!c || !c.webauthn) return;
    try {
      const w = c.webauthn;
      const cred = await navigator.credentials.get({ publicKey: {
        challenge: this._b64uToBuf(w.challenge), rpId: w.rpId, userVerification: w.userVerification || "required",
        allowCredentials: (w.allowCredentials || []).map(x => ({ type: "public-key", id: this._b64uToBuf(x.id) })) } });
      const passkey = { id: cred.id, rawId: this._bufToB64u(cred.rawId), type: cred.type, response: {
        authenticatorData: this._bufToB64u(cred.response.authenticatorData),
        clientDataJSON: this._bufToB64u(cred.response.clientDataJSON),
        signature: this._bufToB64u(cred.response.signature) } };
      const d = await jpost("/api/ceremony/confirm", { re: c.re, nonce_response: c.nonce || "", passkey });
      if (d && d.accepted) { c.state = "holding"; Convo.say(d.speak || "Bestätigt."); this.holdWatch(c); }
      else { this.active = null; Convo.say((d && d.speak) || "Abgebrochen."); }
      Work.renderNeedsYou();
    } catch (e) { Convo.say("Passkey-Bestätigung nicht möglich: " + (e && e.message ? e.message : e)); }
  },
  async turn(text) {
    const c = this.active, low = text.trim().toLowerCase();
    const isStop = /(^|\b)(stopp|stop|abbrechen|abbruch|cancel|halt|nein)(\b|$)/.test(low);
    if (c.state === "holding") { if (isStop) return this.cancel(); Convo.say("Sende läuft — sag stopp zum Abbrechen."); return; }
    if (isStop) return this.cancel();
    return this.confirm(text);
  },
  async confirm(nonce) {
    const c = this.active; if (!c) return;
    const d = await jpost("/api/ceremony/confirm", { re: c.re, nonce_response: nonce });
    if (d && d.accepted) { c.state = "holding"; Convo.say(d.speak || "Bestätigt."); this.holdWatch(c); }
    else { this.active = null; Convo.say((d && d.speak) || "Abgebrochen."); }
    Work.renderNeedsYou();
  },
  async cancel() {
    const c = this.active; if (!c) return;
    const d = await jpost("/api/ceremony/cancel", { re: c.re });
    this.active = null; Convo.say((d && d.speak) || "Gestoppt."); Work.renderNeedsYou();
  },
  holdWatch(c) {
    setTimeout(async () => {
      if (!this.active || this.active.re !== c.re) return;
      const d = await jget("/api/ceremony/status?re=" + encodeURIComponent(c.re));
      this.active = null; if (d && d.speak) Convo.say(d.speak); Work.renderNeedsYou();
    }, (c.hold_ms || 10000) + 700);
  }
};

const Links = {
  init() { this.strip = $("#linkChips"); this.sig = ""; this.refresh(); setInterval(() => this.refresh(), 3000); },
  async refresh() {
    if (document.hidden) return;
    const d = await jget("/api/links"); const links = (d && d.links) || [];
    const sig = links.map(l => l.url).join("|"); if (sig === this.sig) return; this.sig = sig; this.render(links);
  },
  render(links) {
    const s = this.strip; s.textContent = "";
    if (!links.length) { s.hidden = true; return; }
    s.hidden = false;
    s.appendChild(el("span", { class: "lc-title", text: "🌐 " + links.length }));
    links.slice(0, 10).forEach(l => {
      const chip = el("button", { class: "chip", title: l.url }, [el("span", { class: "u", text: l.url })]);
      chip.addEventListener("click", () => this.open(l.url, chip));
      s.appendChild(chip);
    });
    s.appendChild(el("button", { class: "lc-clear", text: "leeren", onclick: () => { jpost("/api/links/clear"); this.sig = ""; this.render([]); } }));
  },
  async open(url, chip) {
    if (chip) { chip.disabled = true; }
    const d = await jpost("/api/screen/open", { url });

    if (d && d.ok) { toast("im Box-Browser geöffnet"); window.open("/vnc3", "_blank"); }
    else { if (chip) chip.disabled = false; toast("Öffnen fehlgeschlagen"); }
  }
};

const Drop = {
  init() {
    this.veil = $("#dropVeil"); this.depth = 0;
    window.addEventListener("dragenter", (e) => { if (this.hasFiles(e)) { e.preventDefault(); this.depth++; this.veil.classList.add("on"); } });
    window.addEventListener("dragover", (e) => { if (this.veil.classList.contains("on")) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; } });
    window.addEventListener("dragleave", () => { if (this.veil.classList.contains("on")) { this.depth--; if (this.depth <= 0) this.hide(); } });
    window.addEventListener("drop", (e) => {
      if (!this.veil.classList.contains("on")) return; e.preventDefault(); this.hide();
      const fs = e.dataTransfer && e.dataTransfer.files; if (!fs || !fs.length) return;
      for (const f of fs) this.upload(f, f.name, f.type);
    });
  },
  hasFiles(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0; },
  hide() { this.veil.classList.remove("on"); this.depth = 0; },
  async upload(blob, name, type) {
    await api("/api/upload", { method: "POST", headers: { "X-Filename": encodeURIComponent(name), "Content-Type": type || blob.type || "application/octet-stream" }, body: blob });
    toast("hochgeladen: " + name);
  }
};

const Vault = {
  panel: null,
  _blob: null, _version: 0, _vmk: null, _entries: null,
  _poll: null, _idle: null, _asks: null, _seen: null,
  KINDS: [
    { v: "api_key", t: "API-Key" },
    { v: "oauth", t: "OAuth-Token" },
    { v: "app_password", t: "App-Passwort" },
    { v: "login", t: "Login (User/Passwort)" },
    { v: "ssh_key", t: "SSH-Key" },
    { v: "vpn", t: "VPN-Zugang" },
    { v: "totp", t: "2FA-Secret (TOTP)" },
    { v: "bot_token", t: "Bot-Token" },
    { v: "note", t: "Notiz" },
    { v: "", t: "Anderes" },
  ],
  POLICIES: [
    { v: "ask", t: "bei Nutzung fragen" },
    { v: "auto", t: "automatisch freigeben" },
    { v: "touch", t: "Bestätigung nötig" },
  ],
  _C() { return (typeof VaultCrypto !== "undefined") ? VaultCrypto : (window.VaultCrypto || null); },

  async toggle() {
    this.panel = this.panel || $("#vaultPanel");
    if (!this.panel.hidden) { this.panel.hidden = true; return; }
    this.panel.hidden = false;
    await this.open();
  },

  lock() {
    this._stopPolling();
    if (this._idle) { clearTimeout(this._idle); this._idle = null; }
    if (this._linkPoll) { clearInterval(this._linkPoll); this._linkPoll = null; }
    if (this._vmk) { try { this._vmk.fill(0); } catch (e) {} }
    this._vmk = null; this._entries = null; this._asks = null; this._seen = null;
  },

  _afterUnlock() { this._asks = this._asks || []; this._seen = this._seen || new Set(); this._startPolling(); this._armIdle(); },
  _startPolling() { if (this._poll) return; this._poll = setInterval(() => this._pollReleases(), 3000); this._pollReleases(); },
  _stopPolling() { if (this._poll) { clearInterval(this._poll); this._poll = null; } },
  _armIdle() {
    if (this._idle) clearTimeout(this._idle);
    this._idle = setTimeout(() => { this.lock(); if (this.panel && !this.panel.hidden) this.open(); toast("🔒 Tresor automatisch gesperrt"); }, 10 * 60 * 1000);
  },
  async _pollReleases() {
    if (!this._vmk) { this._stopPolling(); return; }
    const r = await jget("/api/vault/release/pending");
    if (!r || !r.pending) return;
    this._seen = this._seen || new Set(); this._asks = this._asks || [];
    let changed = false;
    for (const req of r.pending) {
      if (this._seen.has(req.req_id)) continue;
      this._seen.add(req.req_id);
      const entry = this._entries[req.name];
      if (!entry) { this._deny(req, "nicht im Tresor"); continue; }
      if ((entry.policy || "ask") === "auto") { this._fulfill(req, entry); }
      else { this._asks.push(req); changed = true; toast("🔐 Freigabe angefragt: " + req.name);
             if (this.panel && this.panel.hidden) this.panel.hidden = false; }
    }
    if (changed && this.panel && !this.panel.hidden && this._entries) this._renderUnlocked(this.panel);
  },
  async _fulfill(req, entry) {
    try {
      const sealed = await this._C().sealTo(req.box_pub, this._C().utf8(entry.value));
      const r = await jpost("/api/vault/release/fulfill", { req_id: req.req_id, sealed: sealed });
      this._asks = (this._asks || []).filter(a => a.req_id !== req.req_id);
      if (r && r.ok) toast("🔓 freigegeben: " + req.name);
      if (this.panel && !this.panel.hidden && this._entries) this._renderUnlocked(this.panel);
    } catch (e) { toast("Freigabe fehlgeschlagen"); }
  },
  async _deny(req, reason) {
    await jpost("/api/vault/release/deny", { req_id: req.req_id, reason: reason || "" });
    this._asks = (this._asks || []).filter(a => a.req_id !== req.req_id);
    if (this.panel && !this.panel.hidden && this._entries) this._renderUnlocked(this.panel);
  },

  async open() {
    const p = this.panel; p.textContent = "";
    const C = this._C();
    if (!C) { p.appendChild(el("div", { class: "empty", text: "Tresor-Krypto nicht geladen." })); return; }
    p.appendChild(el("div", { class: "vault-busy", text: "lade …" }));
    const meta = await jget("/api/vault/blob");
    p.textContent = "";
    if (!meta || meta.error) { p.appendChild(el("div", { class: "empty", text: "Tresor nicht verfügbar." })); return; }
    this._version = meta.version || 0;
    this._blob = (meta.version && meta.blob_b64) ? C.deserialize(C.b64d(meta.blob_b64)) : null;
    if (!this._blob) this._renderSetup(p);
    else if (this._vmk) this._renderUnlocked(p);
    else this._renderLocked(p);
  },

  async _push() {
    const C = this._C();
    const r = await jpost("/api/vault/blob", { blob_b64: C.b64e(C.serialize(this._blob)), base_version: this._version });
    if (r && r.version) { this._version = r.version; return true; }
    if (r && r.error === "conflict") { toast("Konflikt — anderes Gerät war schneller. Lade neu …"); await this.open(); return false; }
    if (r && r.error === "too big") { toast("Tresor zu groß."); return false; }
    toast("Speichern fehlgeschlagen"); return false;
  },

  _renderSetup(p) {
    p.appendChild(el("div", { class: "vault-head", text: "🔐 Tresor einrichten" }));
    p.appendChild(el("div", { class: "vault-note", text:
      "Wofür: Hier legst du Zugangsdaten ab, die die Box für deine Aufträge braucht — "
      + "z. B. VPN-Zugang, API-Schlüssel, App-Passwort, SSH-Schlüssel oder 2FA-Code." }));
    p.appendChild(el("div", { class: "vault-note", text:
      "Was du davon hast: Einmal eintragen statt jedes Mal tippen. Braucht ein Auftrag einen "
      + "Zugang, fragt die Box danach, und du gibst ihn mit einem Klick frei (oder erlaubst ihn "
      + "dauerhaft). Entschlüsselt wird nur hier in deinem Browser — die Box speichert "
      + "ausschließlich den verschlüsselten Block und kann ihn selbst nicht lesen." }));
    p.appendChild(el("div", { class: "vault-note", text:
      "Ohne Tresor: Es funktioniert alles weiter. Du gibst Zugangsdaten dann bei Bedarf von "
      + "Hand ein, und die Box merkt sich nichts davon. Der Tresor ist optional." }));
    p.appendChild(el("div", { class: "vault-note", text:
      "Wichtig: Passphrase und Recovery-Code verlassen NIE dieses Gerät. Die Box kann den "
      + "Tresor nicht öffnen und nicht zurücksetzen — vergisst du beide, sind die "
      + "gespeicherten Zugangsdaten unwiederbringlich weg (alles andere bleibt)." }));
    const p1 = el("input", { class: "vault-inp", type: "password", placeholder: "Passphrase (min. 8 Zeichen)", autocomplete: "new-password" });
    const p2 = el("input", { class: "vault-inp", type: "password", placeholder: "Passphrase wiederholen", autocomplete: "new-password" });
    p.appendChild(el("div", { class: "vault-add col" }, [p1, p2,
      el("button", { class: "btn sm", text: "Tresor anlegen", onclick: () => this._create(p1.value, p2.value) })]));
  },

  async _create(pass, pass2) {
    if (!pass || pass.length < 8) { toast("Passphrase min. 8 Zeichen"); return; }
    if (pass !== pass2) { toast("Passphrasen stimmen nicht überein"); return; }
    const C = this._C(), which = this._devWhich();
    toast("lege Tresor an …");
    const recov = C.generateRecoveryCode();
    this._blob = await C.createVault(pass, recov, { passWhich: which });
    const u = await C.unlock(this._blob, pass, which);
    this._vmk = u.vmkBytes; this._entries = u.entries;
    if (!await this._push()) { toast("Anlegen fehlgeschlagen"); return; }
    this._showRecovery(recov);
  },

  _devId() {
    try {
      var k = localStorage.getItem("pp-vault-dev");
      if (k) return k;
      var b = crypto.getRandomValues(new Uint8Array(6));
      k = Array.from(b).map(function (x) { return x.toString(16).padStart(2, "0"); }).join("");
      localStorage.setItem("pp-vault-dev", k); return k;
    } catch (e) { return "local"; }
  },
  _devWhich() { return "dev:" + this._devId(); },

  _showRecovery(recov) {
    const p = this.panel; p.textContent = "";
    p.appendChild(el("div", { class: "vault-head", text: "✅ angelegt — Recovery-Code sichern" }));
    p.appendChild(el("div", { class: "vault-note", text: "Der EINZIGE Weg zurück, wenn du die Passphrase vergisst oder das Gerät verlierst. Jetzt notieren/ausdrucken — wird nie wieder angezeigt." }));
    p.appendChild(el("div", { class: "vault-recovery", text: recov }));
    p.appendChild(el("div", { class: "vault-add" }, [
      el("button", { class: "btn sm ghost", text: "📋 kopieren", onclick: () => { try { navigator.clipboard.writeText(recov); toast("kopiert"); } catch (e) {} } }),
      el("button", { class: "btn sm", text: "gesichert →", onclick: () => this._renderUnlocked(this.panel) })]));
  },

  _renderLocked(p) {
    const which = this._devWhich();
    const hasDev = !!(this._blob && this._blob.wrap && this._blob.wrap[which]);
    p.appendChild(el("div", { class: "vault-head", text: "🔒 Tresor gesperrt" }));
    if (hasDev) {
      const inp = el("input", { class: "vault-inp", type: "password", placeholder: "Passphrase", autocomplete: "current-password" });
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") this._unlock(inp.value, which); });
      p.appendChild(el("div", { class: "vault-add" }, [inp,
        el("button", { class: "btn sm", text: "Entsperren", onclick: () => this._unlock(inp.value, which) })]));
    } else {
      p.appendChild(el("div", { class: "vault-note", text: "Dieses Gerät ist noch nicht eingerichtet. Verknüpfe es mit einem eingeloggten Gerät — oder entsperre mit dem Recovery-Code." }));
      p.appendChild(el("div", { class: "vault-add" }, [
        el("button", { class: "btn sm", text: "🔗 Gerät verknüpfen", onclick: () => this._linkNewStart() })]));
    }
    const rc = el("input", { class: "vault-inp", type: "text", placeholder: "… oder Recovery-Code", autocomplete: "off" });
    p.appendChild(el("div", { class: "vault-add" }, [rc,
      el("button", { class: "btn sm ghost", text: "mit Code", onclick: () => this._unlock(rc.value.trim().toUpperCase(), "recov") })]));
  },

  _linkAuthorizeUI() {
    const code = el("input", { class: "vault-inp", type: "text", placeholder: "Code vom neuen Gerät (AAA-BBB)", autocomplete: "off" });
    const area = el("div", { class: "vault-asks" }, [
      el("div", { class: "vault-asklabel", text: "🔗 Neues Gerät autorisieren" }),
      el("div", { class: "vault-add" }, [code,
        el("button", { class: "btn sm", text: "verknüpfen", onclick: () => this._linkAuthorize(code.value.trim().toUpperCase(), area) })]),
    ]);
    this.panel.insertBefore(area, this.panel.children[1] || null);
  },
  async _linkAuthorize(code, area) {
    if (!this._vmk) { toast("erst entsperren"); return; }
    const C = this._C();
    const res = await jget("/api/vault/link/resolve?code=" + encodeURIComponent(code));
    if (!res || res.error || !res.new_pub) { toast("Code nicht gefunden / abgelaufen"); return; }
    const sealed = await C.sealTo(res.new_pub, this._vmk);
    const sas = await C.linkSAS(res.new_pub, sealed.epk);
    const r = await jpost("/api/vault/link/offer", { link_id: res.link_id, sealed: sealed });
    if (!r || !r.ok) { toast("Verknüpfen fehlgeschlagen"); return; }
    area.textContent = "";
    area.appendChild(el("div", { class: "vault-asklabel", text: "🔗 Auf dem neuen Gerät muss die gleiche Zahl stehen:" }));
    area.appendChild(el("div", { class: "vault-recovery", text: sas }));
  },
  async _linkNewStart() {
    const C = this._C();
    const eph = await C.genEphemeral();
    const st = await jpost("/api/vault/link/start", { new_pub: eph.pubRawB64 });
    if (!st || !st.link_id) { toast("Start fehlgeschlagen"); return; }
    const p = this.panel; p.textContent = "";
    p.appendChild(el("div", { class: "vault-head", text: "🔗 Gerät verknüpfen" }));
    p.appendChild(el("div", { class: "vault-note", text: "Auf einem bereits eingeloggten, entsperrten Gerät: Tresor öffnen → ＋ Gerät → diesen Code eingeben:" }));
    p.appendChild(el("div", { class: "vault-recovery", text: st.code }));
    p.appendChild(el("div", { class: "vault-note", text: "warte auf Bestätigung …" }));
    const poll = setInterval(async () => {
      const g = await jget("/api/vault/link/get?link_id=" + st.link_id);
      if (g && g.offer) {
        clearInterval(poll); this._linkPoll = null;
        try {
          const vmk = await C.openFrom(eph, g.offer);
          const sas = await C.linkSAS(eph.pubRawB64, g.offer.epk);
          this._linkNewConfirm(vmk, sas);
        } catch (e) { toast("Verknüpfen fehlgeschlagen"); this.open(); }
      }
    }, 2000);
    this._linkPoll = poll;
    setTimeout(() => { if (this._linkPoll === poll) { clearInterval(poll); this._linkPoll = null; } }, 300000);
  },
  _linkNewConfirm(vmk, sas) {
    const p = this.panel; p.textContent = "";
    p.appendChild(el("div", { class: "vault-head", text: "🔗 Zahlen vergleichen" }));
    p.appendChild(el("div", { class: "vault-note", text: "Auf dem anderen Gerät muss die GLEICHE Zahl stehen. Nur dann bestätigen (schützt vor Manipulation):" }));
    p.appendChild(el("div", { class: "vault-recovery", text: sas }));
    const p1 = el("input", { class: "vault-inp", type: "password", placeholder: "Passphrase für DIESES Gerät (min. 8)", autocomplete: "new-password" });
    p.appendChild(el("div", { class: "vault-add col" }, [p1,
      el("button", { class: "btn sm", text: "✓ stimmt überein — Gerät einrichten", onclick: () => this._linkNewFinish(vmk, p1.value) })]));
  },
  async _linkNewFinish(vmk, pass) {
    if (!pass || pass.length < 8) { toast("Passphrase min. 8 Zeichen"); return; }
    const C = this._C();
    const meta = await jget("/api/vault/blob");
    if (!meta || !meta.blob_b64) { toast("Tresor nicht gefunden"); return; }
    this._version = meta.version || 0;
    let blob = C.deserialize(C.b64d(meta.blob_b64));
    blob = await C.addWrap(blob, vmk, pass, this._devWhich());
    this._blob = blob;
    if (!await this._push()) { toast("Speichern fehlgeschlagen"); return; }
    this._vmk = vmk;
    const u = await C.unlock(this._blob, pass, this._devWhich());
    this._entries = u.entries;
    toast("🔗 Gerät verknüpft"); this._renderUnlocked(this.panel);
  },

  async _unlock(secret, which) {
    if (!secret) return;
    try {
      const u = await this._C().unlock(this._blob, secret, which);
      this._vmk = u.vmkBytes; this._entries = u.entries;
      this._renderUnlocked(this.panel); toast("🔓 entsperrt");
    } catch (e) { toast("Entsperren fehlgeschlagen"); }
  },

  _renderUnlocked(p) {
    p.textContent = "";
    this._afterUnlock();
    p.appendChild(el("div", { class: "vault-head" }, [
      el("span", { text: "🔓 Tresor" }),
      el("span", { class: "vault-headbtns" }, [
        el("button", { class: "btn xs ghost", text: "＋ Gerät", title: "neues Gerät verknüpfen", onclick: () => this._linkAuthorizeUI() }),
        el("button", { class: "btn xs ghost", text: "🔒 sperren", onclick: () => { this.lock(); this.open(); } })])]));

    if ((this._asks || []).length) {
      const box = el("div", { class: "vault-asks" });
      box.appendChild(el("div", { class: "vault-asklabel", text: "🔐 Freigabe-Anfragen — ein Task will ein Credential nutzen" }));
      this._asks.forEach(rq => box.appendChild(el("div", { class: "vault-row" }, [
        el("span", { class: "vault-name", text: rq.name }),
        el("button", { class: "btn xs", text: "erlauben", onclick: () => this._fulfill(rq, this._entries[rq.name]) }),
        el("button", { class: "btn xs ghost", text: "ablehnen", onclick: () => this._deny(rq, "abgelehnt") }),
      ])));
      p.appendChild(box);
    }
    const names = Object.keys(this._entries || {}).sort();
    const list = el("div", { class: "vault-list" });
    if (!names.length) list.appendChild(el("div", { class: "empty", text: "noch keine Einträge" }));
    names.forEach(nm => {
      const e = this._entries[nm];
      list.appendChild(el("div", { class: "vault-row" }, [
        el("span", { class: "vault-name", text: nm }),
        el("span", { class: "vault-kind", text: (this.KINDS.find(k => k.v === e.kind) || {}).t || e.kind || "" }),
        el("span", { class: "vault-policy " + (e.policy || "ask"), text: this._polLabel(e.policy) }),
        el("button", { class: "btn xs ghost", text: "👁", title: "anzeigen", onclick: (ev) => this._reveal(nm, ev.target) }),
        el("button", { class: "btn xs ghost", text: "🗑", title: "löschen", onclick: () => this._del(nm) }),
      ]));
    });
    p.appendChild(list);
    const kindSel = el("select", { class: "vault-sel" }, this.KINDS.map(k => el("option", { value: k.v, text: k.t })));
    const polSel = el("select", { class: "vault-sel" }, this.POLICIES.map(k => el("option", { value: k.v, text: k.t })));
    const nameInp = el("input", { class: "vault-inp", type: "text", placeholder: "Name", autocomplete: "off" });
    const valInp = el("input", { class: "vault-inp", type: "password", placeholder: "Wert", autocomplete: "off" });
    p.appendChild(el("div", { class: "vault-add col" }, [
      el("div", { class: "vault-add" }, [nameInp, kindSel]),
      el("div", { class: "vault-add" }, [valInp, polSel]),
      el("button", { class: "btn sm", text: "＋ Hinzufügen", onclick: () => this._add(nameInp, valInp, kindSel, polSel) })]));
    p.appendChild(el("div", { class: "vault-note", text: "Ende-zu-Ende verschlüsselt. Die Box speichert nur Chiffrate, die sie nicht öffnen kann." }));
  },

  _polLabel(pol) { return pol === "auto" ? "⚡ auto" : pol === "touch" ? "✋ Touch" : "❓ fragen"; },

  _reveal(nm, btn) {
    const e = this._entries[nm]; if (!e) return;
    const row = btn.closest(".vault-row");
    const old = row.querySelector(".vault-val");
    if (old) { old.remove(); return; }
    const f = el("input", { class: "vault-inp vault-val", type: "text", value: e.value, readonly: true });
    row.appendChild(f); f.select();
    setTimeout(() => { if (f.parentNode) f.remove(); }, 15000);
  },

  async _add(nameInp, valInp, kindSel, polSel) {
    const name = (nameInp.value || "").trim(), value = valInp.value;
    if (!name || !value) { toast("Name und Wert nötig"); return; }
    this._entries[name] = { kind: kindSel.value || "", policy: polSel.value || "ask", value: value, updated: Math.floor(Date.now() / 1000) };
    valInp.value = "";
    this._blob = await this._C().save(this._blob, this._vmk, this._entries);
    if (await this._push()) { toast("🔐 " + name); this._renderUnlocked(this.panel); }
  },

  async _del(name) {
    if (!confirm("Eintrag „" + name + "“ löschen?")) return;
    delete this._entries[name];
    this._blob = await this._C().save(this._blob, this._vmk, this._entries);
    if (await this._push()) { toast("gelöscht: " + name); this._renderUnlocked(this.panel); }
  },
};

const WIDGETS = {
  msgr: {
    title: "💬 Messenger", grow: true,
    fill(host) { Messenger.rail = true; Messenger.mount(host); },
  },
  sessions: {
    title: "🖥 Aktive Sessions",
    async fill(host) {
      host.textContent = "";
      let d; try { d = await jget("/api/queue"); } catch (e) { d = null; }
      const ss = (d && d.sessions) || [];
      if (!ss.length) { host.appendChild(el("div", { class: "empty", text: "keine aktiven Sessions" })); return; }
      ss.slice(0, 8).forEach(s => {
        host.appendChild(el("div", { class: "rw-row", onclick: () => { if (s.sid && !s.voice) openSessionTerminal(s.sid); } }, [
          el("span", { class: "as-dot" }),
          el("span", { class: "ellipsis", text: (s.voice ? "🎙 " : "") + (s.title || s.sid) }),
          s.mem_mb ? el("span", { class: "muted tnum", text: s.mem_mb + " MiB" }) : null,
        ]));
      });
    },
  },

  boardcmt: {
    title: "🧭 Sessions-Kommentar",
    titleFn() { return Rail.openSid ? "💬 Session-Verlauf" : "🧭 Sessions-Kommentar"; },
    fill(host) { if (Rail.openSid) railSessionFill(host, Rail.openSid); else railChannelFill(host, "board"); },
  },
  opslog: {
    title: "📊 Betriebs-Log",
    fill(host) { railChannelFill(host, "work"); },
  },

  pulse: {
    title: "🫀 Box-Puls",
    async fill(host) {
      host.textContent = "";
      let o = null, q = null;
      try { o = await jget("/api/overview"); } catch (e) {}
      try { q = await jget("/api/queue"); } catch (e) {}
      if (!o || o.ok === false) { host.appendChild(el("div", { class: "empty", text: "keine Daten" })); return; }
      const row = (label, pct, txt) => {
        const p = Math.max(0, Math.min(100, pct | 0));
        const cls = p >= 90 ? "bad" : p >= 70 ? "warn" : "good";
        host.appendChild(el("div", { class: "bp-row" }, [
          el("span", { class: "bp-lab", text: label }),
          el("span", { class: "usebar " + cls }, [el("i", { style: "width:" + p + "%" })]),
          el("span", { class: "bp-val muted tnum", text: txt }),
        ]));
      };
      row("CPU", o.load_pct || 0, (o.load1 != null ? o.load1 : "?") + "/" + (o.ncpu || "?"));
      if (o.mem) row("RAM", o.mem.used_pct || 0, (o.mem.used_pct || 0) + "%");

      try {
        const nd = await jget("/api/nodes");
        const others = ((nd && nd.nodes) || []).filter(n => !n.local && n.res && n.res.nproc);
        others.forEach(n => {
          const r = n.res;
          const short = String(n.name || n.id).split(" · ")[0].split(" (")[0];
          if (n.state !== "online") {
            host.appendChild(el("div", { class: "bp-row", title: (n.name || n.id) + " · offline" }, [
              el("span", { class: "bp-lab ellipsis muted", text: "🔴 " + short }),
              el("span", { class: "bp-val muted", text: "offline" })]));
            return;
          }
          const cp = r.load1 != null ? Math.max(0, Math.min(100, Math.round(r.load1 / r.nproc * 100))) : 0;
          const mp = (r.mem_total_mb && r.mem_avail_mb != null)
            ? Math.round((r.mem_total_mb - r.mem_avail_mb) / r.mem_total_mb * 100) : null;
          const rr = el("div", { class: "bp-row", title: (n.name || n.id) + " · " + r.nproc + " Kerne · "
            + (r.mem_total_mb ? (r.mem_total_mb / 1024).toFixed(0) + " GB RAM" : "") }, [
            el("span", { class: "bp-lab ellipsis", text: short }),
            el("span", { class: "usebar " + (cp >= 90 ? "bad" : cp >= 70 ? "warn" : "good") },
              [el("i", { style: "width:" + cp + "%" })]),
            el("span", { class: "bp-val muted tnum", text: cp + "%" + (mp != null ? " · R" + mp + "%" : "") }),
          ]);
          host.appendChild(rr);
        });
        const on = ((nd && nd.nodes) || []).filter(n => n.state === "online" && n.res && n.res.nproc);
        if (on.length > 1) {
          const cores = on.reduce((s, n) => s + (n.res.nproc || 0), 0);
          const mem = on.reduce((s, n) => s + (n.res.mem_total_mb || 0), 0);
          host.appendChild(el("div", { class: "bp-foot muted tnum",
            text: "🖧 Fleet: " + cores + " Kerne · " + Math.round(mem / 1024) + " GB RAM über " + on.length + " Nodes" }));
        }
      } catch (e) {}
      const jobs = (q && (q.jobs || q.queue)) || [];
      const run = jobs.filter(j => /run|activ/i.test(String(j.state || j.status || ""))).length;
      const wait = jobs.length - run;
      host.appendChild(el("div", { class: "bp-foot muted tnum",
        text: (o.active_vms != null ? o.active_vms + " VMs · " : "") + wait + " wartend · " + run + " laufend",
      }));
      host.classList.add("bp-click");
      host.onclick = () => { try { Router.go("work"); } catch (e) {} };
    },
  },

  llmtank: {
    title: "⛽ LLM-Tank",
    async fill(host) {
      host.textContent = "";
      const d = await aget("/api/admin/llm/pool");
      if (!d || d._forbidden) { host.appendChild(el("div", { class: "empty", text: "nur für Admins sichtbar" })); return; }
      if (d._neterr || d.ok === false) { host.appendChild(el("div", { class: "empty", text: "keine Daten" })); return; }
      const accs = (d.accounts || []).filter(a => a.enabled);
      if (!accs.length) { host.appendChild(el("div", { class: "empty", text: "kein Konto aktiv" })); return; }
      accs.forEach(a => {
        const nfo = a.info || {};
        const p5 = nfo.five_hour_pct, p7 = nfo.seven_day_pct;
        const pct = p5 != null ? p5 : p7;
        const cls = pct == null ? "good" : pct >= 80 ? "bad" : pct >= 60 ? "warn" : "good";
        const r = el("div", { class: "bp-row" }, [
          el("span", { class: "bp-lab bp-wide ellipsis", text: (d.preferred === a.id ? "⭐ " : "") + (nfo.display_name || a.id) }),
          el("span", { class: "usebar " + cls }, [el("i", { style: "width:" + (pct != null ? Math.round(pct) : 0) + "%" })]),
          el("span", { class: "bp-val muted tnum", text: pct != null ? Math.round(pct) + "%" : (a.logged_in ? "—" : "aus") }),
        ]);
        if (p7 != null) r.title = "7-Tage-Auslastung: " + Math.round(p7) + "%";
        if (a.cooling) r.title = (r.title ? r.title + " · " : "") + "Cooldown aktiv";
        host.appendChild(r);
      });
      host.classList.add("bp-click");
      host.onclick = () => { try { Router.go("admin"); Admin.switchTab("llm"); } catch (e) {} };
    },
  },
  watchdog: {
    title: "🐕 Watchdog / Rotalarm",
    async fill(host) {

      if (host.childElementCount && host.matches && host.matches(":hover")) return;
      host.textContent = "";
      let fl; try { fl = await jget("/api/watchdog/fleet"); } catch (e) { host.appendChild(el("div", { class: "empty", text: "Watchdog nicht erreichbar" })); return; }
      if (!fl || fl.ok === false) { host.appendChild(el("div", { class: "empty", text: (fl && fl.error) || "nur für Admins" })); return; }
      const hb = fl.heartbeat || {};
      const seq = (hb.primary && hb.primary.seq) || 0;
      const age = hb.age_primary_s == null ? "?" : Math.round(hb.age_primary_s) + "s";
      const inc = hb.incidents && Object.keys(hb.incidents).length;
      const trees = wdVisibleTrees(fl);
      const hiddenN = fl.compact ? ((fl.hidden_trees || {}).n || 0) : ((fl.trees || []).length - trees.length);

      const _wsum = wdActSummary({ trees: trees, sessions: fl.sessions, heartbeat: fl.heartbeat });
      host.appendChild(el("div", { style: wdNoteStyle(_wsum.act ? "act" : _wsum.watch ? "watch" : "good") }, [
        el("span", { text: _wsum.act ? ("🔴 " + _wsum.act + " Punkt(e) könnten dich brauchen — unten rot markiert.")
          : _wsum.watch ? ("🟡 Die Box regelt gerade " + _wsum.watch + " Sache(n) selbst — du musst nichts tun.")
          : "🟢 Alles im grünen Bereich — du musst nichts tun." })
      ]));
      host.appendChild(el("div", { class: "rw-row" }, [
        el("span", { title: "Der Watchdog meldet sich regelmäßig („Herzschlag“). Steigt die Nummer, läuft die Überwachung normal.", text: "Herzschlag " + seq + " · vor " + age }),
        inc ? el("span", { class: "warn", text: " · " + inc + " Alarm(e)" }) : el("span", { style: "color:#22c55e", text: " · ok" })
      ]));
      if (inc) host.appendChild(el("div", { style: "font-size:11px;opacity:.78;margin:0 0 3px 2px", text: "🔴 Rotalarm heißt: die Überwachung selbst meldet ein Problem (z. B. Stillstand). Handlungsbedarf: ja — bitte kurz prüfen." }));
      const MAXT = 8;
      trees.slice(0, MAXT).forEach(t => {
        const c = t.counts || {};
        const wx = wdExplain("tree", t.aggregate);
        host.appendChild(el("div", { class: "rw-row bp-click", title: "Details & Verlauf im Work-Reiter öffnen",
          onclick: () => { try { Router.go("work"); Work.djDetail(t.orchestrator); } catch (e) {} } }, [
          el("span", { class: "ellipsis", text: t.title || t.orchestrator }),
          el("span", { style: "color:" + wx.col, title: wx.mean, text: " " + wx.dot + " " + t.aggregate }),
          el("span", { class: "muted tnum", text: " " + (c.running || 0) + "▶ " + (c.done || 0) + "✓" + ((c.error || 0) ? " " + c.error + "✗" : "") })
        ]));

        const g = (t.error_groups || [])[0];
        if (g) host.appendChild(el("div", { class: "muted ellipsis", style: "font-size:11px;margin:0 0 2px 8px", title: g.sig,
          text: "⚠ " + g.sig.slice(0, 70) + (g.count > 1 ? " ×" + g.count : "") + (t.more_error_groups ? " · +" + t.more_error_groups + " weitere Ursachen" : "") }));
        else if (wx.sev === "act" || wx.sev === "watch")
          host.appendChild(el("div", { style: "font-size:11px;opacity:.78;margin:0 0 3px 2px", text: wx.dot + " " + wx.mean + " Handlungsbedarf: " + wx.act }));
      });
      if (trees.length > MAXT) host.appendChild(el("div", { class: "muted", text: "+" + (trees.length - MAXT) + " weitere Bäume — im Work-Reiter" }));
      if (hiddenN) host.appendChild(el("div", { class: "muted bp-click", title: "beendete/inaktive Aufträge: Verlauf im Work-Reiter",
        onclick: () => { try { Router.go("work"); } catch (e) {} },
        text: "🗂 " + hiddenN + " beendete/inaktive Aufträge ausgeblendet" }));
      if (!trees.length && !hiddenN) host.appendChild(el("div", { class: "muted", text: "keine Orchestrator-Aufträge — alles ruhig" }));
      const sess = fl.sessions || [];
      if (sess.length) {
        const bad = sess.filter(s => s.health && s.health.state && s.health.state !== "ok").length;
        host.appendChild(el("div", { class: "muted", text: "Sessions: " + sess.length + (bad ? " · " + bad + " auffällig" : " · alle ok") }));
        if (bad) host.appendChild(el("div", { style: "font-size:11px;opacity:.78;margin:0 0 3px 2px", text: "🟡 „Auffällig“ heißt meist: die Box startet die Session gerade neu. Handlungsbedarf: nein — erst wenn daraus „failed“ wird." }));
      }

      let dmc = null; try { dmc = await jget("/api/admin/watchdog/deadman"); } catch (e) {}
      if (dmc && dmc.ok !== false) {
        const urls = ((dmc.config || {}).urls) || [];
        const inp = el("input", { class: "dj-in", placeholder: "externe Ping-URL(s), Komma-getrennt", value: urls.join(", "),
          title: "Externer Dead-Man: diese URL(s) werden regelmäßig gepingt — bleibt der Ping aus, schlägt die Gegenstelle Alarm." });
        const btn = el("button", { class: "btn xs", text: "✓", title: "Dead-Man-Ziel(e) speichern", onclick: async () => {
          btn.disabled = true;
          const list = (inp.value || "").split(",").map(x => x.trim()).filter(Boolean);
          try { const r = await jpost("/api/admin/watchdog/deadman", { urls: list }); toast(r && r.ok ? "Dead-Man-Ziel(e) gespeichert" : "Fehler"); }
          catch (e) { toast("Fehler"); }
          btn.disabled = false;
        } });
        host.appendChild(el("div", { class: "rw-row", style: "margin-top:5px" }, [
          el("span", { class: "muted", text: "🔗 Dead-Man:" }), inp, btn]));
        if (!urls.length) inp.style.borderColor = "rgba(234,179,8,.5)";
      }
    }
  },
  a2a: {
    title: "🔔 Web-Wächter",
    async fill(host) {

      if (host.childElementCount && host.matches && host.matches(":hover")) return;
      host.textContent = "";
      host.appendChild(el("div", { style: "margin:0 0 6px" }, [
        el("button", { class: "rw-btn", text: "＋ Neuer Wächter",
          title: "Beobachtet eine Webseite auf ein Signal; meldet sich oder bereitet (nach Freigabe) eine Agent-Session vor.",
          onclick: () => Software.a2aForm(null) })]));
      const list = el("div");
      host.appendChild(list);
      Software._a2aHost = list;
      await Software.loadA2A();
    }
  },
  dauerjobs: {
    title: "♾ Dauerjobs",
    async fill(host) {
      host.textContent = ""; host.onclick = null; host.classList.remove("bp-click");
      let d; try { d = await jget("/api/metasessions"); } catch (e) { host.appendChild(el("div", { class: "empty", text: "nicht erreichbar" })); return; }
      const jobs = (d && d.metasessions) || [];
      if (!jobs.length) host.appendChild(el("div", { class: "empty", text: "keine Dauerjobs" }));
      jobs.slice(0, 8).forEach(jb => {
        const c = jb.counts || {};
        host.appendChild(el("div", { class: "rw-row bp-click", title: "Details & Verwaltung öffnen", onclick: () => Work.djDetail(jb.id) }, [
          el("span", { class: "ellipsis", text: (jb.state === "running" ? "● " : "❚❚ ") + (jb.title || jb.id) }),
          el("span", { class: "muted tnum", text: (c.running || 0) + "/" + (c.pending || 0) })
        ]));
      });
      host.appendChild(el("div", { style: "margin-top:6px" }, [
        el("button", { class: "rw-btn", text: "➕ Neuer Dauerjob",
          onclick: async () => { const node = await Work.djForm(); Overlay.open("Neuer Dauerjob", node); } })
      ]));
    }
  },
  hpcguard: {
    title: "🛡 HPC-Safeguard",
    async fill(host) {
      host.textContent = "";
      let r; try { r = await jget("/api/hpc-safeguard"); } catch (e) { host.appendChild(el("div", { class: "empty", text: "nicht erreichbar" })); return; }
      if (!r || r.ok === false) { host.appendChild(el("div", { class: "empty", text: (r && r.error) || "nur für Admins" })); return; }
      host.appendChild(el("div", { class: "rw-row" }, [
        el("span", { text: (r.enabled ? "🟢 aktiv" : "⚪ aus") + " · Tunnel " + (r.tunnel_now ? "steht" : "getrennt") + " · alle " + (r.interval_min || 12) + " min" }),
      ]));
      if (r.last_action) host.appendChild(el("div", { class: "muted ellipsis", title: r.last_action, text: r.last_action }));
      (r.recent_events || []).slice(-3).forEach(ev => host.appendChild(
        el("div", { class: "muted ellipsis", text: "· " + (typeof ev === "string" ? ev : JSON.stringify(ev).slice(0, 90)) })));
      const btn = el("button", { class: "rw-btn", text: r.enabled ? "Ausschalten" : "Einschalten",
        title: r.enabled ? "Safeguard stoppen (Session ruht)" : "Safeguard starten: prüft HPC regelmäßig auf Login-Knoten-Jobs, killt + mailt (Owner-Opt-in)",
        onclick: async () => { try { await jpost("/api/hpc-safeguard", { enabled: !r.enabled }); } catch (e) {} Rail.render(); } });
      host.appendChild(el("div", { style: "margin-top:6px" }, [btn]));
    }
  },
  alert: {
    title: "🚨 Meldungen",
    async fill(host) {
      host.textContent = "";
      let b = null, fl = null;
      try { b = await jget("/api/session/board"); } catch (e) {}
      try { fl = await jget("/api/watchdog/fleet"); } catch (e) {}
      const rows = [];
      ((b && b.sessions) || []).forEach(s => {
        const h = s.health || {};
        if (h.state === "failed" || h.state === "restarting")
          rows.push({ ic: "🛑", t: (s.title || s.sid) + ": " + (h.reason || h.state), sid: s.sid });
        if (s.observer && s.observer.problem)
          rows.push({ ic: "❗", t: (s.title || s.sid) + ": " + (s.observer.text || "Beobachter meldet ein Problem"), sid: s.sid });
        if (s.unread) rows.push({ ic: "💬", t: (s.title || s.sid) + ": " + s.unread + " neue Nachricht(en)", sid: s.sid });
      });
      const inc = (fl && fl.ok !== false && fl.heartbeat && fl.heartbeat.incidents) || null;
      if (inc) Object.keys(inc).forEach(k => rows.push({ ic: "🚨", t: "Rotalarm: " + k }));
      if (!rows.length) { host.appendChild(el("div", { class: "empty", text: "keine Meldungen — alles ruhig" })); return; }
      rows.slice(0, 10).forEach(r => {
        const d = el("div", { class: "rw-row", title: r.t }, [
          el("span", { class: "ellipsis", text: r.ic + " " + r.t }),
        ]);
        if (r.sid) { d.style.cursor = "pointer"; d.onclick = () => { try { Router.go("sessions"); } catch (e) {} }; }
        host.appendChild(d);
      });
    }
  },
  gedanken: {
    title: "💭 Gedanken",
    async fill(host) {
      host.textContent = "";
      const row = el("div", { class: "rw-row" });
      const inp = el("input", { class: "dj-in", placeholder: "Gedanke festhalten …" });
      const b = el("button", { class: "btn xs", text: "merken" });
      const save = async () => {
        const t = (inp.value || "").trim(); if (!t) return;
        b.disabled = true;
        try { await jpost("/api/thoughts", { text: t }); inp.value = ""; toast("Gedanke gemerkt"); this.fill(host); } catch (e) { toast("Fehler"); }
        b.disabled = false;
      };
      b.addEventListener("click", save);
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
      row.appendChild(inp); row.appendChild(b);
      host.appendChild(row);
      let d; try { d = await jget("/api/thoughts"); } catch (e) { return; }
      const notes = (d && (d.notes || d.thoughts)) || [];
      if (!notes.length) { host.appendChild(el("div", { class: "muted", text: "noch keine Gedanken" })); return; }
      notes.slice(0, 8).forEach(n => {
        host.appendChild(el("div", { class: "rw-row" }, [
          el("span", { class: "ellipsis", text: (n.raw_text || "").slice(0, 60) }),
          el("span", { class: "muted", text: n.state === "refined" ? "✓" : n.state === "spawned" ? "▶" : "" })
        ]));
      });
    }
  },
};

const WD_STATE_INFO = {
  tree: {
    failed:   { sev: "act",   mean: "Dieser Auftrags-Baum ist überwiegend fehlgeschlagen.", act: "ja — Ergebnis prüfen, ggf. neu starten" },
    degraded: { sev: "watch", mean: "Ein Teil hakt, der Rest des Baums läuft weiter.",       act: "meist nein — beobachten" },
    running:  { sev: "calm",  mean: "Der Baum arbeitet gerade.",                              act: "nein" },
    done:     { sev: "good",  mean: "Alle Teilaufgaben sind fertig.",                          act: "nein" },
    idle:     { sev: "calm",  mean: "Baum wartet oder ruht.",                                  act: "nein" },
  },
  child: {
    error:   { sev: "act",   mean: "Diese Teilaufgabe ist mit Fehler gestoppt.", act: "nur falls die Aufgabe für dich wichtig ist" },
    done:    { sev: "good",  mean: "Teilaufgabe erfolgreich beendet.",           act: "nein" },
    running: { sev: "calm",  mean: "Teilaufgabe läuft gerade.",                   act: "nein" },
    pending: { sev: "calm",  mean: "Teilaufgabe wartet auf einen freien Platz.",  act: "nein" },
  },
  health: {
    ok:         { sev: "good",  mean: "Session ist gesund.",                                    act: "nein" },
    restarting: { sev: "watch", mean: "Die Box startet die Session gerade automatisch neu.",    act: "nein — die Box regelt das selbst" },
    failed:     { sev: "act",   mean: "Automatischer Neustart hat mehrfach nicht geklappt.",    act: "ja — Session bitte kurz prüfen" },
    unknown:    { sev: "calm",  mean: "Zustand ließ sich im Moment nicht ermitteln.",           act: "nein — beobachten" },
    "n/a":      { sev: "calm",  mean: "Für diese Aufgabe gibt es (noch) keine eigene Session.", act: "nein" },
  },
};
const WD_SEV = {
  good:  { dot: "🟢", col: "#22c55e", label: "alles gut" },
  calm:  { dot: "⚪", col: "#9aa",    label: "kein Handeln nötig" },
  watch: { dot: "🟡", col: "#eab308", label: "die Box kümmert sich" },
  act:   { dot: "🔴", col: "#f87171", label: "braucht dich" },
};
function wdExplain(kind, state) {
  const info = ((WD_STATE_INFO[kind] || {})[state]) ||
    { sev: "calm", mean: "Technischer Zustand — i. d. R. kein Handeln nötig, nur beobachten.", act: "nein" };
  const sev = WD_SEV[info.sev] || WD_SEV.calm;
  return { sev: info.sev, dot: sev.dot, col: sev.col, sevLabel: sev.label, mean: info.mean, act: info.act };
}
function wdNoteStyle(sev) {
  const c = sev === "act" ? "248,113,113" : sev === "watch" ? "234,179,8" : "34,197,94";
  return "padding:3px 8px;border-radius:7px;margin:3px 0;font-size:11.5px;line-height:1.35;" +
    "background:rgba(" + c + ",.12);border:1px solid rgba(" + c + ",.32)";
}
function wdVisibleTrees(fl) {

  const trees = (fl && fl.trees) || [];
  if (fl && fl.compact) return trees;
  return trees.filter(t => (((t.counts || {}).running || 0) + ((t.counts || {}).pending || 0)) > 0);
}
function wdActSummary(fl) {

  const trees = (fl && fl.trees) || [], sess = (fl && fl.sessions) || [];
  const inc = (fl && fl.heartbeat && fl.heartbeat.incidents && Object.keys(fl.heartbeat.incidents).length) || 0;
  const act = trees.filter(t => wdExplain("tree", t.aggregate).sev === "act").length +
    sess.filter(s => wdExplain("health", (s.health || {}).state || "ok").sev === "act").length + inc;
  const watch = trees.filter(t => wdExplain("tree", t.aggregate).sev === "watch").length +
    sess.filter(s => wdExplain("health", (s.health || {}).state || "ok").sev === "watch").length;
  return { act: act, watch: watch };
}
let _BRAINS = null, _BRAINS_AT = 0;
function fmtWhen(ts) {

  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date(), p = (n) => String(n).padStart(2, "0");
  const hm = p(d.getHours()) + ":" + p(d.getMinutes());
  if (d.toDateString() === now.toDateString()) return hm;
  const dm = p(d.getDate()) + "." + p(d.getMonth() + 1) + ".";
  return (d.getFullYear() === now.getFullYear() ? dm : dm + d.getFullYear()) + " " + hm;
}
function _brainLabel(id, meta) {
  const m = (meta || {})[id] || {};
  return (m.label || id) + (m.tested === false ? " · ungetestet" : "");
}
function railSessionBrain(head, brain) {

  if (!brain || !brain.provider) return;
  const txt = brain.provider + (brain.model ? " · " + brain.model : "");
  head.appendChild(el("span", { class: "rw-brain rw-brain-static muted",
    title: "Gehirn dieser Session (die Runtime bestimmt es)", text: "🧠 " + txt }));
}
async function railBrainSelect(head, kind) {
  try {
    if (!_BRAINS || Date.now() - _BRAINS_AT > 60000) {
      _BRAINS = await jget("/api/llm/brains"); _BRAINS_AT = Date.now();
    }
  } catch (e) { return; }
  const d = _BRAINS;
  if (!d || d.ok === false || !(d.providers || []).length) return;
  if ((d.providers || []).length < 2 && !d.admin) return;
  const meta = d.provider_meta || {};
  const sel = el("select", { class: "rw-brain", title: "Welches Gehirn fasst hier zusammen (· ungetestet = kein first-class-Anbieter)" },
    d.providers.map(p => el("option", { value: p, text: _brainLabel(p, meta),
                                        selected: ((d.assign || {})[kind] === p) || null })));
  sel.disabled = !d.admin;
  sel.onchange = async () => {
    const r = await apost("/api/admin/board/brain", { kind, provider: sel.value });
    if (r && r.ok) { toast("Gehirn für " + (kind === "board" ? "Überblick" : "Betriebs-Log") + ": " + sel.value); _BRAINS = null; }
    else toast((r && r.error) || "Fehler", true);
  };
  head.appendChild(sel);
}
async function railSessionFill(host, sid) {

  host.textContent = "";
  let d; try { d = await jget("/api/transcript?sid=" + encodeURIComponent(sid) + "&since=0"); } catch (e) { d = null; }
  const turns = ((d && d.turns) || []).filter(t => (t.text || "").trim()).slice(-14);
  if (!turns.length) { host.appendChild(el("div", { class: "empty", text: "noch kein Verlauf" })); return; }
  const p = (n) => String(n).padStart(2, "0");
  turns.forEach(t => {
    const who = t.role === "user" ? "Du" : (t.role === "observer" ? "🔍" : "🤖");
    const row = el("div", { class: "rw-cmt" });
    const tx = el("div", { class: "rw-cmt-tx" });
    tx.innerHTML = "<b>" + who + "</b> " + mdRender(t.text || "");
    row.appendChild(tx);
    if (t.ts || (t.role !== "user" && t.model)) {
      row.appendChild(el("div", { class: "rw-cmt-ts muted tnum" }, [
        t.ts ? el("span", { text: fmtWhen(t.ts) }) : null,
        (t.role !== "user" && t.model) ? el("span", { class: "msgr-model", title: t.model, text: fmtModel(t.model) }) : null,
      ].filter(Boolean)));
    }
    host.appendChild(row);
  });
  host.scrollTop = host.scrollHeight;
}
const RAIL_CADS = [["echtzeit", "Echtzeit"], ["haeufig", "Häufig"], ["selten", "Selten"], ["nie", "Nie"]];
function railCad(kind) { return localStorage.getItem("pp-rail-cad-" + kind) || "haeufig"; }
function railCadSelect(head, kind) {

  const cur = railCad(kind);
  const sel = el("select", { class: "rw-brain", title: "Wie oft der KI-Kommentar aktualisiert wird" },
    RAIL_CADS.map(([v, l]) => el("option", { value: v, text: l, selected: (v === cur) || null })));
  sel.onchange = () => { localStorage.setItem("pp-rail-cad-" + kind, sel.value); Rail.render(); };
  head.appendChild(sel);
}
async function railChannelFill(host, kind) {
  host.textContent = "";
  const cad = railCad(kind);
  let d; try { d = await jget("/api/board/channel?kind=" + kind + "&cad=" + cad); } catch (e) { d = null; }
  const entries = (d && d.entries) || [];
  if (!entries.length) {
    host.appendChild(el("div", { class: "empty", text: cad === "nie" ? "Kommentar pausiert (Nie)." : "noch keine Einträge" }));
    return;
  }
  entries.slice(-6).forEach(e2 => {
    const when = e2.ts ? new Date(e2.ts * 1000) : null;
    const p = (n) => String(n).padStart(2, "0");
    host.appendChild(el("div", { class: "rw-cmt" }, [
      e2.headline ? el("div", { class: "rw-cmt-hl", text: e2.headline }) : null,
      el("div", { class: "rw-cmt-tx", text: e2.text || "" }),
      when ? el("div", { class: "rw-cmt-ts muted tnum", text: fmtWhen(e2.ts) }) : null,
    ]));
  });
  host.scrollTop = host.scrollHeight;
}
const LENS_WIDGET = { work: ["opslog", "sessions"], sessions: ["boardcmt"] };

window.BBW = {
  register(id, def) {
    if (!id || !def || typeof def.fill !== "function") return;
    def.addon = true;
    WIDGETS[id] = def;
    try { Rail.render(); } catch (e) {}
  },
  el, jget, jpost,
  overlay(title, node) { try { Overlay.open(title, node); } catch (e) {} },
  toast(m) { try { toast(m); } catch (e) {} },
};
const WA = {
  cat: null,
  _loaded: {},
  async refresh() {
    try { this.cat = await jget("/api/widgets/catalog"); } catch (e) { this.cat = null; }
    return this.cat;
  },
  inject(a) {
    if (this._loaded[a.id]) { try { Rail.render(); } catch (e) {} return; }
    this._loaded[a.id] = true;
    const s = document.createElement("script");
    s.src = "/widget-addons/" + encodeURIComponent(a.id) + "/widget.js?v=" + encodeURIComponent((a.manifest && a.manifest.version) || "0");
    document.head.appendChild(s);
  },
  async boot() {
    const d = await this.refresh();
    (((d && d.installed) || [])).filter(x => x.enabled).forEach(x => this.inject(x));
  },
  openCatalog() {
    const wrap = el("div", { class: "stack" }, [el("div", { class: "muted", text: "lädt …" })]);
    Overlay.open("Widget-Katalog", wrap);
    this.refresh().then((d) => {
      wrap.textContent = "";
      const btn = (txt, title, fn) => el("button", { class: "rw-btn", text: txt, title: title || "", onclick: fn });
      const row = (title, sub, btns) => {
        const r = el("div", { class: "rw-row" }, [el("span", { class: "ellipsis", title: sub || "", text: title })]);
        btns.forEach(b => r.appendChild(b));
        return r;
      };
      const order = Rail.order();
      wrap.appendChild(el("div", { class: "subhead", text: "Vorinstalliert (im ISO enthalten)" }));
      Object.keys(WIDGETS).filter(w => !WIDGETS[w].addon).forEach(wid => {
        const on = order.indexOf(wid) >= 0;
        wrap.appendChild(row(WIDGETS[wid].title, wid, [
          btn(on ? "abgelegt ✓" : "＋ ablegen", on ? "aus der Leiste nehmen" : "in die Leiste legen", () => {
            Rail.setOrder(on ? Rail.order().filter(x => x !== wid) : Rail.order().concat([wid]));
            this.openCatalog();
          }),
        ]));
      });
      const inst = (d && d.installed) || [], avail = (d && d.available) || [];
      wrap.appendChild(el("div", { class: "subhead", text: "Add-ons (installiert)" }));
      if (!inst.length) wrap.appendChild(el("div", { class: "empty", text: "keine Add-ons installiert" }));
      inst.forEach(a => {
        const on = order.indexOf(a.id) >= 0;
        const btns = [];
        if (a.enabled) btns.push(btn(on ? "abgelegt ✓" : "＋ ablegen", "", () => {
          this.inject(a);
          Rail.setOrder(on ? Rail.order().filter(x => x !== a.id) : Rail.order().concat([a.id]));
          this.openCatalog();
        }));
        btns.push(btn(a.enabled ? "deaktivieren" : "aktivieren", "", async () => {
          const r = await jpost("/api/widgets/enable", { id: a.id, on: !a.enabled });
          if (r && r.ok === false) { toast(r.error || "nicht erlaubt"); return; }
          if (a.enabled) { Rail.setOrder(Rail.order().filter(x => x !== a.id)); this.openCatalog(); }
          else location.reload();
        }));
        btns.push(btn("🗑", "deinstallieren", async () => {
          const r = await jpost("/api/widgets/uninstall", { id: a.id });
          if (r && r.ok === false) { toast(r.error || "nicht erlaubt"); return; }
          Rail.setOrder(Rail.order().filter(x => x !== a.id));
          this.openCatalog();
        }));
        wrap.appendChild(row((a.manifest && a.manifest.title) || a.id, (a.manifest && a.manifest.description) || "", btns));
      });
      wrap.appendChild(el("div", { class: "subhead", text: "Verfügbar (on demand installieren)" }));
      if (!avail.length) wrap.appendChild(el("div", { class: "empty", text: "nichts weiter verfügbar" }));
      avail.forEach(a => {
        wrap.appendChild(row((a.manifest && a.manifest.title) || a.id, (a.manifest && a.manifest.description) || "", [
          btn("installieren", "Add-on installieren (Owner/Admin)", async () => {
            const r = await jpost("/api/widgets/install", { id: a.id });
            if (r && r.ok) {
              this.inject({ id: a.id, manifest: a.manifest });
              Rail.setOrder(Rail.order().concat([a.id]));
              toast("installiert: " + ((a.manifest && a.manifest.title) || a.id));
            } else toast((r && r.error) || "Installation nicht erlaubt");
            this.openCatalog();
          }),
        ]));
      });
      wrap.appendChild(el("div", { class: "muted", text: "Add-ons laufen mit Portal-Rechten — Installation ist Owner-/Admin-Sache." }));
    });
  },
};

const Rail = {
  mq: window.matchMedia("(min-width: 1101px)"),
  openSid: null,
  init() {
    try { this.mq.addEventListener("change", () => this.apply()); }
    catch (e) { this.mq.addListener(() => this.apply()); }
    window.addEventListener("message", (e) => {
      const d = e.data;
      if (d && d.pp === "open-sid") { this.openSid = d.sid || null; this.openBrain = d.sid ? { provider: d.provider || "claude", model: d.model || "" } : null; this.render(); }
    });
    this.apply();
  },
  order() {
    try { const o = JSON.parse(localStorage.getItem("pp-rail-widgets") || "null"); if (Array.isArray(o)) return o.filter(w => WIDGETS[w]); } catch (e) {}
    return ["msgr"];
  },
  setOrder(o) { localStorage.setItem("pp-rail-widgets", JSON.stringify(o)); this.render(); },
  apply() {
    const app = $("#app"); if (!app) return;
    const on = UIP.get("msgr", true);
    app.classList.toggle("rail-off", !on);

    { const n = document.querySelector('.nav-item[data-lens="msgr"]'); if (n) n.hidden = !on; }
    if (!on && Router.cur === "msgr") { try { Router.go("start"); } catch (e) {} }
    if (this.mq.matches && on) {
      this.render();
    } else {
      Messenger.rail = false;

      const ownTab = isMobile();
      const s = $("#startSide"); if (s) s.hidden = !on || ownTab;
      if (on && !ownTab && Router.cur === "start") Messenger.mount($("#msgrHost"));
      if (on && ownTab && Router.cur === "msgr") Messenger.mount($("#msgrTabHost"));
    }
  },
  render() {
    if (!(this.mq.matches && UIP.get("msgr", true))) return;
    const host = $("#railWidgets"); if (!host) return;
    host.textContent = "";
    this._live = [];
    const order = this.order();

    const autos = (LENS_WIDGET[Router.cur] || []).filter(w => WIDGETS[w] && order.indexOf(w) < 0
      && localStorage.getItem("pp-rail-auto-" + Router.cur) !== "0"
      && localStorage.getItem("pp-rail-auto-" + Router.cur + "-" + w) !== "0");
    const seq = autos.concat(order);
    seq.forEach((wid) => {
      const w = WIDGETS[wid]; if (!w) return;
      const isAuto = autos.indexOf(wid) >= 0;
      const idx = order.indexOf(wid);
      const box = el("div", { class: "rw-box" + (w.grow ? " grow" : "") });
      const head = el("div", { class: "rw-head" }, [
        el("span", { class: "rw-title", text: (typeof w.titleFn === "function" ? w.titleFn() : w.title) + (isAuto ? " · Reiter" : "") }),
      ]);

      const brainKind = { boardcmt: "board", opslog: "work" }[wid];
      if (brainKind) {
        if (wid === "boardcmt" && this.openSid) railSessionBrain(head, this.openBrain);
        else railBrainSelect(head, brainKind);
      }
      const cadKind = wid === "opslog" ? "work" : (wid === "boardcmt" && !this.openSid) ? "board" : null;
      if (cadKind) railCadSelect(head, cadKind);
      if (!isAuto && idx > 0) head.appendChild(el("button", { class: "rw-btn", text: "▲", title: "nach oben",
        onclick: () => { const o = this.order(); [o[idx - 1], o[idx]] = [o[idx], o[idx - 1]]; this.setOrder(o); } }));
      if (!isAuto && idx >= 0 && idx < order.length - 1) head.appendChild(el("button", { class: "rw-btn", text: "▼", title: "nach unten",
        onclick: () => { const o = this.order(); [o[idx + 1], o[idx]] = [o[idx], o[idx + 1]]; this.setOrder(o); } }));
      head.appendChild(el("button", { class: "rw-btn", text: "✕",
        title: isAuto ? "Reiter-Widget für diesen Reiter ausblenden" : "Widget entfernen",
        onclick: () => {
          if (isAuto) { localStorage.setItem("pp-rail-auto-" + Router.cur + "-" + wid, "0"); this.render(); }
          else this.setOrder(this.order().filter(x => x !== wid));
        } }));
      box.appendChild(head);
      const body = el("div", { class: "rw-body" });

      body.classList.add("resizable");
      const saved = parseInt(localStorage.getItem("pp-rw-h-" + wid) || "0", 10);
      if (saved > 60) body.style.height = saved + "px";
      body.addEventListener("mouseup", () => {
        const h = body.offsetHeight;
        if (h > 60 && Math.abs(h - (saved || 0)) > 2) localStorage.setItem("pp-rw-h-" + wid, String(h));
      });
      head.title = "Doppelklick: Automatikhöhe";
      head.addEventListener("dblclick", () => { localStorage.removeItem("pp-rw-h-" + wid); this.render(); });
      box.appendChild(body);
      host.appendChild(box);
      (this._live = this._live || []).push({ w, body, wid });
      try { w.fill(body); } catch (e) {}
    });
    host.appendChild(el("div", { class: "rw-spacer" }));
    if (this._tick) clearInterval(this._tick);
    this._tick = setInterval(() => {
      if (!(this.mq.matches && UIP.get("msgr", true))) return;
      (this._live || []).forEach(({ w, body, wid }) => {
        if (w.grow) return;
        const ck = wid === "opslog" ? "work" : (wid === "boardcmt" && !this.openSid) ? "board" : null;
        if (ck && railCad(ck) === "nie") return;
        try { w.fill(body); } catch (e) {}
      });
    }, 15000);

    const missing = Object.keys(WIDGETS).filter(w => order.indexOf(w) < 0);
    const addWrap = el("div", { class: "rw-add" });
    if (missing.length) {
      const menu = el("div", { class: "rw-add-menu", hidden: true },
        missing.map(wid => el("button", { class: "rw-add-item", text: WIDGETS[wid].title,
          onclick: () => this.setOrder(this.order().concat([wid])) })));
      addWrap.appendChild(el("button", { class: "rw-btn rw-plus", text: "＋ Widget", onclick: () => { menu.hidden = !menu.hidden; } }));
      addWrap.appendChild(menu);
    }
    addWrap.appendChild(el("button", { class: "rw-btn rw-plus", text: "\u{1F9E9} Add-ons", title: "Widget-Katalog: vorinstalliert + on demand", onclick: () => WA.openCatalog() }));
    host.appendChild(addWrap);
    if (order.indexOf("msgr") < 0 && autos.indexOf("msgr") < 0) Messenger.rail = false;
  },
};

async function ckFillNeedsYou(host, force) {

  if (!force && host.childElementCount && host.matches && host.matches(":hover")) return;

  host.textContent = "";
  const cards = [];
  const S = (v, dflt) => { const t = (v == null ? "" : String(v)).trim(); return t || (dflt || ""); };

  try {
    if (typeof Ceremony !== "undefined" && Ceremony.active) {
      const c = Ceremony.active, rb = c.readback || {};
      const wrap = el("div", { class: "hitl-card ceremony" });
      wrap.appendChild(el("div", { class: "hitl-q" }, [
        el("span", { class: "hitl-ico", text: "⏸" }),
        el("span", { text: "Bestätigung nötig: " + S(c.verb || rb.subject, "Aktion") + (rb.recipient ? " → " + rb.recipient : "") }),
      ]));
      const act = el("div", { class: "hitl-act" });
      const inp = el("input", { class: "hitl-in", placeholder: "Bestätigungswort" });
      act.appendChild(inp);
      act.appendChild(el("button", { class: "btn sm pri", text: "Bestätigen", onclick: () => { try { Ceremony.confirm(inp.value.trim()); } catch (e) {} } }));
      act.appendChild(el("button", { class: "btn sm", text: "Abbrechen", onclick: () => { try { Ceremony.cancel(); } catch (e) {} } }));
      wrap.appendChild(act);
      cards.push(wrap);
    }
  } catch (e) {}

  try {
    const ap = await jget("/api/decisions?state=pending");
    ((ap && ap.approvals) || []).forEach(q => {
      const question = S(q.question);
      if (!question) return;
      const isAppr = q.kind === "approval";
      const who = S(q.source, S(q.sid, "Eine Session"));
      const when = fmtWhen(q.created);
      const wrap = el("div", { class: "hitl-card" + (isAppr ? " approval" : "") + (q.urgent ? " urgent" : "") });
      const srcRow = el("div", { class: "hitl-src" }, [
        el("span", { class: "hitl-ico", text: isAppr ? "🔐" : (q.urgent ? "🚨" : "❓") }),
        el("span", { class: "hitl-who ellipsis", text: who + (isAppr ? " bittet um Freigabe" : " fragt") }),
        when ? el("span", { class: "hitl-when muted tnum", style: "margin-left:auto;white-space:nowrap", text: when }) : null,
      ]);

      srcRow.appendChild(el("button", { class: "hitl-x", title: "Weglegen, ohne zu entscheiden",
        style: (when ? "" : "margin-left:auto;") + "background:none;border:0;cursor:pointer;opacity:.55;font-size:14px;padding:0 2px",
        text: "✕",
        onclick: async () => {
          try { await jpost("/api/decisions/dismiss", { aid: q.id }); } catch (e) {}
          ckFillNeedsYou(host, true);
        } }));
      wrap.appendChild(srcRow);
      const qEl = el("div", { class: "hitl-q" }); qEl.textContent = question; wrap.appendChild(qEl);
      const act = el("div", { class: "hitl-act" });
      let totpIn = null;
      const send = async (body) => {
        try {
          const r = await jpost("/api/decisions/answer", Object.assign({ aid: q.id }, body));
          if (r && r.ok) toast("✓ entschieden" + (r.delivered ? " + an Session zugestellt" : ""));
          else {
            toast((r && r.error) || "Fehler");

            if (r && (r.undecided || r.need_2fa) && totpIn) { totpIn.value = ""; totpIn.focus(); return; }
          }
        } catch (e) { toast("Fehler beim Entscheiden"); }
        ckFillNeedsYou(host, true);
      };
      if (isAppr) {
        const totp = el("input", { class: "hitl-in totp", placeholder: "2FA-Code vom Handy", inputmode: "numeric", maxlength: "6" });
        totpIn = totp;
        act.appendChild(totp);
        const needCode = (fn) => { const c = totp.value.trim(); if (c.length < 6) { toast("6-stelligen 2FA-Code vom Handy eingeben"); totp.focus(); return; } fn(c); };
        act.appendChild(el("button", { class: "btn sm pri ok", text: "✓ Ja, genehmigen", onclick: () => needCode(c => send({ decision: "approve", totp: c })) }));
        act.appendChild(el("button", { class: "btn sm danger", text: "✗ Nein, ablehnen", onclick: () => needCode(c => send({ decision: "deny", totp: c })) }));
      } else {
        const opts = Array.isArray(q.options) ? q.options.filter(o => S(o)) : [];
        opts.forEach((o, i) => act.appendChild(el("button", { class: "btn sm pri", text: (i + 1) + " · " + S(o), onclick: () => send({ answer: String(o) }) })));
        const inp = el("input", { class: "hitl-in", placeholder: opts.length ? "…oder eigene Antwort" : "Deine Antwort" });
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && inp.value.trim()) send({ answer: inp.value.trim() }); });
        act.appendChild(inp);
        act.appendChild(el("button", { class: "btn sm", text: "Antworten", onclick: () => { if (inp.value.trim()) send({ answer: inp.value.trim() }); } }));
      }
      wrap.appendChild(act);
      cards.push(wrap);
    });
  } catch (e) {}

  if (cards.length) {
    host.appendChild(el("div", { class: "hitl-intro", text: "Hier entscheidest du direkt — lies die Frage, klick die Antwort bzw. gib den 2FA-Code ein." }));
    cards.forEach(c => host.appendChild(c));
  } else {
    host.appendChild(el("div", { class: "ck-quiet", text: "Nichts zu entscheiden. ✓" }));
  }
}

async function ckFillQueue(host) {
  host.textContent = "";
  let d; try { d = await jget("/api/queue"); } catch (e) { d = null; }
  if (!d || d.ok === false) { host.appendChild(el("div", { class: "ck-quiet", text: "Queue nicht lesbar" })); return; }
  const jobs = (d.jobs || []).filter(j => j.source !== "session");
  const run = jobs.filter(j => /run|activ/i.test(String(j.state || j.status || ""))).length;
  const wait = jobs.filter(j => /queue|pend|wait/i.test(String(j.state || j.status || ""))).length;
  const c = d.counts || {};
  const done = c.done != null ? c.done : jobs.filter(j => /done|succ/i.test(String(j.state || j.status || ""))).length;
  const m = el("div", { class: "ck-metric" }, [
    el("span", {}, [el("b", { text: String(wait) }), document.createTextNode(" "), el("span", { text: "wartend" })]),
    el("span", {}, [el("b", { text: String(run) }), document.createTextNode(" "), el("span", { text: "läuft" })]),
    el("span", {}, [el("b", { text: String(done) }), document.createTextNode(" "), el("span", { text: "fertig" })]),
  ]);
  host.appendChild(m);
  if (d.queued_total != null) host.appendChild(el("div", { class: "ck-sub", text: "gesamt in der Queue: " + d.queued_total + (d.mine != null ? " · davon meine: " + d.mine : "") }));
  jobs.filter(j => /run|activ/i.test(String(j.state || j.status || ""))).slice(0, 3).forEach(j => {
    host.appendChild(el("div", { class: "ck-row", title: j.label || j.task_type || "" }, [
      el("span", { class: "ico", text: "▸" }),
      el("span", { class: "ellipsis", text: j.label || j.task_type || j.client_tag || ("Job " + j.id) }),
    ]));
  });
  host.appendChild(el("div", { class: "ck-act" }, [
    el("button", { class: "btn sm", text: "Work öffnen →", onclick: () => { try { Router.go("work"); } catch (e) {} } }),
  ]));
}

async function ckFillMessages(host) {
  host.textContent = "";
  let ss = (typeof Notify !== "undefined" && Notify.lastSessions) || null;
  if (!ss || !Object.keys(ss).length) {
    try { const d = await jget("/api/session/unread"); ss = (d && d.sessions) || {}; } catch (e) { ss = {}; }
  }
  const ids = Object.keys(ss).filter(sid => (ss[sid].unread || 0) > 0)
    .sort((a, b) => ((ss[b].alert || 0) - (ss[a].alert || 0)) || ((ss[b].ts || 0) - (ss[a].ts || 0)));
  if (!ids.length) { host.appendChild(el("div", { class: "ck-quiet", text: "Keine ungelesenen Nachrichten." })); }
  ids.slice(0, 8).forEach(sid => {
    const s = ss[sid];
    host.appendChild(el("div", { class: "ck-row click", title: s.preview || "", onclick: () => (sid === "meldungen" ? Messenger.openInStart(sid) : openSessionTerminal(sid)) }, [
      el("span", { class: "muted tnum", text: (s.unread > 50 ? "50+" : s.unread) }),
      el("span", { class: "ellipsis", text: ((s.alert || 0) > 0 ? "🚨 " : "") + (s.title || sid) }),
    ]));
  });
  host.appendChild(el("div", { class: "ck-act" }, [
    el("button", { class: "btn sm", text: "Alle gelesen", onclick: async () => { try { await jpost("/api/session/seen", { all: true }); } catch (e) {} toast("✓ als gelesen markiert"); ckFillMessages(host); } }),
  ]));
}

async function ckFillConverse(host) {

  host.textContent = "";
  let d = null; try { d = await jget("/api/conversations"); } catch (e) {}
  const rows = (d && d.conversations) || [];
  if (!rows.length) host.appendChild(el("div", { class: "ck-quiet", text: "Keine Konversationen. Zwei Sessions wählen und Auftrag starten — der Moderator übernimmt Turn-Taking und Stops." }));
  rows.slice(0, 6).forEach(c => {
    const st = c.state === "working" ? "🤝" : (c.state === "completed" ? "✓" : (c.state === "failed" ? "✗" : "■"));
    const line = el("div", { class: "ck-row", title: c.task || "" }, [
      el("span", { class: "ico", text: st }),
      el("span", { class: "ellipsis", text: (c.title || c.id) + " · Turn " + (c.turn || 0) + "/" + c.max_turns + " · " + c.state }),
      el("button", { class: "btn xs", text: "Mithören", onclick: () => { try { Messenger.openInStart(c.id); } catch (e) {} } }),
    ]);
    if (c.state === "working") line.appendChild(el("button", { class: "btn xs", text: "■ STOP", onclick: async () => { try { await jpost("/api/conversation/stop", { id: c.id }); } catch (e) {} ckFillConverse(host); } }));
    host.appendChild(line);
  });
  const selA = el("select", { style: "flex:1 1 110px;min-width:90px" });
  const selB = el("select", { style: "flex:1 1 110px;min-width:90px" });
  try {
    const b = await jget("/api/session/board");
    ((b && b.sessions) || []).slice(0, 50).forEach(r => {
      const ttl = ((r.title || r.sid) + "").slice(0, 36);
      selA.appendChild(el("option", { value: r.sid, text: ttl }));
      selB.appendChild(el("option", { value: r.sid, text: ttl }));
    });
    if (selB.options.length > 1) selB.selectedIndex = 1;
  } catch (e) {}
  const task = el("input", { placeholder: "Auftrag der Konversation …", style: "flex:2 1 160px;min-width:120px" });
  host.appendChild(el("div", { class: "ck-act" }, [selA, selB, task,
    el("button", { class: "btn sm pri", text: "Start", onclick: async () => {
      const t = task.value.trim();
      if (!t || selA.value === selB.value) { toast("Zwei VERSCHIEDENE Sessions + Auftrag nötig"); return; }
      try {
        const r = await jpost("/api/conversation", { a_sid: selA.value, b_sid: selB.value, task: t });
        if (r && r.ok) { toast("🤝 Konversation gestartet"); task.value = ""; }
        else toast((r && r.error) || "Start fehlgeschlagen");
      } catch (e) { toast("Start fehlgeschlagen"); }
      ckFillConverse(host);
    } }),
  ]));
}

function _relB64uToBuf(s){ s=String(s).replace(/-/g,"+").replace(/_/g,"/"); const p=s.length%4; if(p) s+="=".repeat(4-p); const bin=atob(s), b=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) b[i]=bin.charCodeAt(i); return b.buffer; }
function _relBufToB64u(buf){ const b=new Uint8Array(buf); let s=""; for(let i=0;i<b.length;i++) s+=String.fromCharCode(b[i]); return btoa(s).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""); }
async function relayArmPasskey(host) {
  try {
    const begin = await jpost("/api/relay/arm", { factor: "passkey" });
    if (!begin || !begin.webauthn) { toast((begin && begin.error) || "Fingerabdruck nicht möglich"); return; }
    const w = begin.webauthn;
    const cred = await navigator.credentials.get({ publicKey: {
      challenge: _relB64uToBuf(w.challenge), rpId: w.rpId, userVerification: w.userVerification || "required",
      allowCredentials: (w.allowCredentials || []).map(x => ({ type: "public-key", id: _relB64uToBuf(x.id) })) } });
    const passkey = { id: cred.id, rawId: _relBufToB64u(cred.rawId), type: cred.type, response: {
      authenticatorData: _relBufToB64u(cred.response.authenticatorData),
      clientDataJSON: _relBufToB64u(cred.response.clientDataJSON),
      signature: _relBufToB64u(cred.response.signature) } };
    const r = await jpost("/api/relay/arm", { factor: "passkey", passkey, re: begin.re });
    toast(r && r.ok ? "✓ Relay scharf (Fingerabdruck)" : ((r && r.error) || "Scharfschalten fehlgeschlagen"));
  } catch (e) {
    toast("Fingerabdruck abgebrochen — nichts wurde scharfgeschaltet");
  }
  ckFillRelay(host);
}
async function ckFillRelay(host) {

  let d = null; try { d = await jget("/api/approvals"); } catch (e) {}
  host.textContent = "";
  if (!d || !d.ok) { host.appendChild(el("div", { class: "ck-quiet", text: "Relay-Status nur für Owner/Admin sichtbar." })); return; }
  const arm = ((d.approvals || []).find(a => a.id === "relay-arm")) || {};
  const known = arm.known !== false;
  const on = arm.armed === true;
  const stateTxt = !known ? "unbekannt" : (on ? "SCHARF — Fernzugriff aktiv" : "dunkel — kein Fernzugriff");
  const head = el("div", { class: "ck-relay-head" }, [
    el("span", { class: "ck-relay-dot " + (!known ? "unk" : (on ? "on" : "off")) }),
    el("span", { class: "ellipsis", text: "Relay: " + stateTxt }),
  ]);
  host.appendChild(head);
  if (arm.detail) { const dt = el("div", { class: "muted", style: "font-size:.85em;margin:2px 0 6px" }); dt.textContent = arm.detail; host.appendChild(dt); }

  const act = el("div", { class: "ck-act" });
  if (!on) {
    const totp = el("input", { placeholder: "2FA-Code", inputmode: "numeric", maxlength: "6", style: "flex:0 1 120px;min-width:100px" });
    act.appendChild(totp);
    act.appendChild(el("button", { class: "btn sm pri", text: "▶ Scharfschalten", onclick: async () => {
      const c = totp.value.trim(); if (!c) { toast("2FA-Code nötig"); return; }
      try { const r = await jpost("/api/approvals/relay-arm/approve", { totp: c });
            toast(r && r.ok ? "✓ Relay scharf" : ((r && r.error) || "Fehler")); } catch (e) { toast("Fehler"); }
      ckFillRelay(host);
    } }));

    if (window.PublicKeyCredential) {
      act.appendChild(el("button", { class: "btn sm", title: "Mit Fingerabdruck/Passkey scharfschalten",
        text: "🔐 Fingerabdruck", onclick: () => relayArmPasskey(host) }));
    }
  } else {
    act.appendChild(el("button", { class: "btn sm", text: "■ Abschalten (dunkel)", onclick: async () => {
      try { const r = await jpost("/api/approvals/relay-arm/revoke", {});
            toast(r && r.ok ? "✓ Relay dunkel" : ((r && r.error) || "Fehler")); } catch (e) { toast("Fehler"); }
      ckFillRelay(host);
    } }));
  }
  host.appendChild(act);

  if (arm.qr || arm.qr_url) {
    const det = el("details", { class: "ck-relay-pair" });
    det.appendChild(el("summary", { text: "📱 Handy für 2FA dieser Box koppeln" }));
    const wrap = el("div", { style: "padding:8px 2px" });
    wrap.appendChild(el("div", { class: "muted", style: "font-size:.82em;margin-bottom:6px", text: "Scanne diesen Code mit deiner Authenticator-App (Google Authenticator, Aegis, 1Password …). Erst danach passen die 2FA-Codes zu DIESER Box." }));
    const img = el("img", { alt: "2FA-QR dieser Box", style: "max-width:180px;width:100%;border-radius:8px;background:#fff;padding:6px" });
    if (arm.qr) img.src = arm.qr;
    else det.addEventListener("toggle", async () => {
      if (!det.open || img.src) return;
      try { const q = await jget(arm.qr_url); if (q && q.ok && q.qr) img.src = q.qr; else toast((q && q.error) || "QR nicht verfügbar"); } catch (e) { toast("QR nicht ladbar"); }
    });
    wrap.appendChild(img);
    det.appendChild(wrap);
    host.appendChild(det);
  }

  const pend = (d.pending || []).filter(p => (p.status === "pending"));
  if (pend.length) {
    host.appendChild(el("div", { class: "ck-relay-sub", text: "Eingehende Freigaben (" + pend.length + "):" }));
    pend.slice(0, 4).forEach(p => {
      const pwhen = fmtWhen(p.created || p.at || p.ts);
      const row = el("div", { class: "ck-row", title: (p.detail || p.command || p.desc || "") }, [
        el("span", { class: "ico", text: "📥" }),
        el("span", { class: "ellipsis", text: (p.title || p.action || p.command || "Off-LAN-Aktion") }),
        pwhen ? el("span", { class: "muted tnum", style: "margin-left:auto;white-space:nowrap", text: pwhen }) : null,
      ]);
      host.appendChild(row);
      const a2 = el("div", { class: "ck-act" });
      const tc = el("input", { placeholder: "2FA", inputmode: "numeric", maxlength: "6", style: "flex:0 1 90px;min-width:70px" });
      a2.appendChild(tc);
      a2.appendChild(el("button", { class: "btn sm pri", text: "✓", onclick: async () => {
        try { await jpost("/api/approvals/action/decide", { id: p.id, decision: "approve", totp: tc.value.trim() }); } catch (e) {}
        ckFillRelay(host);
      } }));
      a2.appendChild(el("button", { class: "btn sm", text: "✗", onclick: async () => {
        try { await jpost("/api/approvals/action/decide", { id: p.id, decision: "deny" }); } catch (e) {}
        ckFillRelay(host);
      } }));
      host.appendChild(a2);
    });
  } else {
    host.appendChild(el("div", { class: "ck-quiet", text: "Keine eingehenden Relay-Aufgaben." }));
  }
}

async function ckFillBackup(host) {

  host.textContent = "";
  let d = null; try { d = await jget("/api/backup"); } catch (e) {}
  if (!d || !d.ok) { host.appendChild(el("div", { class: "ck-quiet", text: "Sicherung nur für Owner/Admin." })); return; }
  const last = (d.last && d.last.result) || null;
  const when = d.last && d.last.ts ? fmtWhen(d.last.ts) : "—";
  const head = el("div", { class: "ck-bak-head" }, [
    el("span", { class: "ck-relay-dot " + (last && last.ok ? "on" : (last ? "unk" : "off")) }),
    el("span", { text: last && last.ok ? ("Letzte Sicherung: " + when) : (last ? "Letzte Sicherung FEHLGESCHLAGEN" : "Noch keine Sicherung") }),
  ]);
  host.appendChild(head);
  if (last && last.ok) host.appendChild(el("div", { class: "muted", style: "font-size:.82em;margin:2px 0 6px", text: ((last.bytes/1e6).toFixed(1)) + " MB · " + (last.n_files || 0) + " Dateien · DBs konsistent" }));

  const act = el("div", { class: "ck-act" });
  act.appendChild(el("button", { class: "btn sm pri", text: "⤓ Jetzt sichern", onclick: async () => {
    try { const r = await jpost("/api/backup/now", { label: "manuell" });
          toast(r && r.ok ? "Sicherung läuft …" : "Fehler"); } catch (e) { toast("Fehler"); }
    setTimeout(() => ckFillBackup(host), 6000);
  } }));
  host.appendChild(act);

  const bundles = d.bundles || [];
  if (bundles.length) {
    host.appendChild(el("div", { class: "ck-relay-sub", text: "Sicherungen (" + bundles.length + "):" }));
    bundles.slice(0, 5).forEach(b => {
      host.appendChild(el("div", { class: "ck-row", title: b.path }, [
        el("span", { class: "ico", text: "🗄" }),
        el("span", { class: "ellipsis", text: (b.name || "").replace("brainbox-state-", "").replace(".tar.zst", "") }),
        el("span", { class: "muted tnum", text: ((b.bytes/1e6).toFixed(1)) + " MB" }),
      ]));
    });
  }

  const s = d.schedule || {};
  const sched = el("div", { class: "ck-bak-sched" });
  const toggle = el("input", { type: "checkbox" }); toggle.checked = !!s.enabled;
  const hh = el("input", { class: "hitl-in", style: "flex:0 1 54px;min-width:48px", inputmode: "numeric", value: String(s.hour != null ? s.hour : 3) });
  const mm = el("input", { class: "hitl-in", style: "flex:0 1 54px;min-width:48px", inputmode: "numeric", value: String(s.minute != null ? s.minute : 30) });
  const saveSched = async () => {
    try { await jpost("/api/backup/schedule", { enabled: toggle.checked, hour: parseInt(hh.value, 10), minute: parseInt(mm.value, 10) }); toast("Zeitplan gespeichert"); }
    catch (e) { toast("Fehler"); }
  };
  toggle.addEventListener("change", saveSched);
  const lbl = el("label", { class: "ck-bak-schedrow" }, [toggle, el("span", { text: "Nächtlich um" }), hh, el("span", { text: ":" }), mm, el("button", { class: "btn xs", text: "✓", onclick: saveSched })]);
  sched.appendChild(lbl);
  host.appendChild(sched);
}

const CK_PANELS = {
  alert:    { title: "🚨 Meldungen",   span: 3, fill: (h) => WIDGETS.alert.fill(h) },
  needsyou: { title: "🙋 Braucht dich", span: 2, fill: (h) => ckFillNeedsYou(h) },
  pulse:    { title: "🫀 Ressourcen",  span: 1, fill: (h) => WIDGETS.pulse.fill(h) },
  fleet:    { title: "🛰 Flotte",       span: 3, fill: (h) => WIDGETS.sessions.fill(h) },
  queue:    { title: "📋 Queue",        span: 1, fill: (h) => ckFillQueue(h) },
  converse: { title: "🤝 Konversationen", span: 2, fill: (h) => ckFillConverse(h) },
  relay:    { title: "📡 Relay (Fernzugriff)", span: 1, fill: (h) => ckFillRelay(h) },
  backup:   { title: "🗄 Sicherung", span: 1, fill: (h) => ckFillBackup(h) },
  messages: { title: "💬 Nachrichten",  span: 2, fill: (h) => ckFillMessages(h) },
  watchdog: { title: "🐕 Watchdog",     span: 2, fill: (h) => WIDGETS.watchdog.fill(h) },
  a2a:      { title: "🔔 Web-Wächter",  span: 1, fill: (h) => WIDGETS.a2a.fill(h) },
  dauerjobs:{ title: "♾ Dauerjobs",     span: 2, fill: (h) => WIDGETS.dauerjobs.fill(h) },
  llmtank:  { title: "⛽ LLM-Tank",     span: 1, fill: (h) => WIDGETS.llmtank.fill(h) },
  gedanken: { title: "💭 Gedanken",     span: 1, fill: (h) => WIDGETS.gedanken.fill(h) },
  boardcmt: { title: "🧭 Sessions-Kommentar", span: 2, fill: (h) => railChannelFill(h, "board") },
  opslog:   { title: "📊 Betriebs-Log", span: 2, fill: (h) => railChannelFill(h, "work") },
};
const CK_DEFAULT = ["alert", "needsyou", "pulse", "fleet", "queue", "messages"];

const Cockpit = {
  host: null, _tick: null, _drag: null,
  LK: "pp-cockpit-v1",
  layout() {
    let o = null;
    try { o = JSON.parse(localStorage.getItem(this.LK) || "null"); } catch (e) {}
    if (!o || !Array.isArray(o.order)) o = { order: CK_DEFAULT.slice(), hidden: [], span: {}, h: {} };
    o.order = o.order.filter(id => CK_PANELS[id]);
    if (!o.order.length) o.order = CK_DEFAULT.slice();
    o.hidden = (o.hidden || []).filter(id => CK_PANELS[id]);
    o.span = o.span || {}; o.h = o.h || {};
    return o;
  },
  save(o) { try { localStorage.setItem(this.LK, JSON.stringify(o)); } catch (e) {} this.render(); },
  reset() { try { localStorage.removeItem(this.LK); } catch (e) {} toast("Cockpit auf Standard zurückgesetzt"); this.render(); },

  mount(host) {
    this.host = host; host.textContent = "";

    const bar = el("div", { class: "ck-input" });
    const sel = el("select", { id: "ckTarget", title: "Wohin geht deine Eingabe?" });
    sel.appendChild(el("option", { value: "__voice__", text: "🎙 Sprachassistent" }));
    sel.appendChild(el("option", { value: "__job__", text: "＋ Neuer Auftrag" }));
    const inp = el("input", { id: "ckInput", placeholder: "Sag oder tippe, was zu tun ist …",
      onkeydown: (e) => { if (e.key === "Enter") this.deliver(); } });
    bar.appendChild(sel); bar.appendChild(inp);
    bar.appendChild(el("button", { class: "btn pri", text: "▸ Senden", onclick: () => this.deliver() }));
    host.appendChild(bar);
    this.loadTargets();

    const toolbar = el("div", { class: "ck-bar" });
    toolbar.appendChild(el("span", { class: "sp" }));
    this._addWrap = el("div", { style: "position:relative" });
    toolbar.appendChild(this._addWrap);
    toolbar.appendChild(el("button", { class: "ck-btn", text: "↺ Standard", title: "Panel-Anordnung auf Standard zurücksetzen", onclick: () => this.reset() }));
    host.appendChild(toolbar);

    this.grid = el("div", { class: "ck-grid" });
    host.appendChild(this.grid);
    this.render();

    if (this._tick) clearInterval(this._tick);
    this._tick = setInterval(() => {
      if (Router.cur !== "start" || document.hidden) return;
      this._refresh();
    }, 15000);
  },
  teardown() { if (this._tick) { clearInterval(this._tick); this._tick = null; } this.host = null; },

  async loadTargets() {
    let d; try { d = await jget("/api/sessions"); } catch (e) { return; }
    const sel = $("#ckTarget"); if (!sel || !d || !d.sessions) return;
    const keep = sel.value;
    d.sessions.filter(s => !s.archived).slice(0, 40).forEach(s => {
      sel.appendChild(el("option", { value: s.sid, text: (s.title || s.sid).slice(0, 40) }));
    });
    if (keep) sel.value = keep;
  },
  async deliver() {
    const sel = $("#ckTarget"), inp = $("#ckInput");
    if (!sel || !inp) return;
    const text = inp.value.trim(); if (!text) return;
    const tgt = sel.value;
    inp.value = "";
    try {
      if (tgt === "__voice__") {
        const r = await jpost("/api/voice", { text });
        if (r && r.speak) toast(r.speak.slice(0, 120));
        else toast("an den Sprachassistenten gesendet");
      } else if (tgt === "__job__") {
        const r = await jpost("/api/jobs", { prompt: text });
        toast(r && r.id ? "Auftrag angelegt" : ((r && r.error) || "Fehler"));
      } else {
        const r = await jpost("/api/session/say", { sid: tgt, text });
        toast(r && r.ok !== false ? "an die Session gesendet" : ((r && r.error) || "Fehler"));
      }
    } catch (e) { toast("Senden fehlgeschlagen"); }
  },

  render() {
    if (!this.grid) return;
    const o = this.layout();
    this.grid.textContent = "";
    this._live = [];
    o.order.filter(id => o.hidden.indexOf(id) < 0).forEach((id) => {
      const p = CK_PANELS[id]; if (!p) return;
      const span = o.span[id] || p.span || 1;
      const panel = el("div", { class: "ck-panel", "data-span": span, "data-id": id });

      const head = el("div", { class: "ck-head", draggable: "true" }, [ el("span", { class: "ck-title", text: p.title }) ]);
      head.appendChild(el("button", { class: "ck-btn", text: "⟷", title: "Breite ändern (schmal · breit · voll)",
        onclick: (e) => { e.stopPropagation(); const cur = o.span[id] || p.span || 1; o.span[id] = (cur % 3) + 1; this.save(o); } }));
      head.appendChild(el("button", { class: "ck-btn", text: "✕", title: "Panel ausblenden",
        onclick: (e) => { e.stopPropagation(); o.hidden = o.hidden.concat([id]); this.save(o); } }));

      head.addEventListener("dragstart", (e) => { this._drag = id; panel.classList.add("ck-dragging"); try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", id); } catch (x) {} });
      head.addEventListener("dragend", () => { this._drag = null; panel.classList.remove("ck-dragging"); this.grid.querySelectorAll(".ck-dragover").forEach(n => n.classList.remove("ck-dragover")); });
      panel.addEventListener("dragover", (e) => { if (this._drag && this._drag !== id) { e.preventDefault(); panel.classList.add("ck-dragover"); } });
      panel.addEventListener("dragleave", () => panel.classList.remove("ck-dragover"));
      panel.addEventListener("drop", (e) => {
        e.preventDefault(); panel.classList.remove("ck-dragover");
        const from = this._drag; if (!from || from === id) return;
        const arr = o.order.slice(); const fi = arr.indexOf(from); if (fi >= 0) arr.splice(fi, 1);
        const ti = arr.indexOf(id); arr.splice(ti < 0 ? arr.length : ti, 0, from);
        o.order = arr; this.save(o);
      });
      panel.appendChild(head);
      const body = el("div", { class: "ck-body resizable" });
      const sh = parseInt(o.h[id] || "0", 10); if (sh > 60) body.style.height = sh + "px";
      body.addEventListener("mouseup", () => { const hh = body.offsetHeight; if (hh > 60 && Math.abs(hh - (sh || 0)) > 2) { o.h[id] = hh; try { localStorage.setItem(this.LK, JSON.stringify(o)); } catch (x) {} } });
      head.title = "Ziehen zum Umordnen · Doppelklick: Automatikhöhe";
      head.addEventListener("dblclick", () => { delete o.h[id]; this.save(o); });
      panel.appendChild(body);
      this.grid.appendChild(panel);
      this._live.push({ id, body, fill: p.fill });
      try { p.fill(body); } catch (x) {}
    });

    if (this._addWrap) {
      this._addWrap.textContent = "";
      const shown = o.order.filter(id => o.hidden.indexOf(id) < 0);
      const missing = Object.keys(CK_PANELS).filter(id => shown.indexOf(id) < 0);
      if (missing.length) {
        const menu = el("div", { class: "ck-add-menu", hidden: true },
          missing.map(id => el("button", { class: "ck-add-item", text: CK_PANELS[id].title,
            onclick: () => { const L = this.layout(); L.hidden = L.hidden.filter(x => x !== id); if (L.order.indexOf(id) < 0) L.order = L.order.concat([id]); this.save(L); } })));
        this._addWrap.appendChild(el("button", { class: "ck-btn", text: "＋ Panel", onclick: () => { menu.hidden = !menu.hidden; } }));
        this._addWrap.appendChild(menu);
      }
    }
  },
  _refresh() { (this._live || []).forEach(({ body, fill }) => { try { fill(body); } catch (e) {} }); },
};

const Start = {
  show() {
    this.loadGreeting();
    try { Cockpit.mount($("#cockpitHost")); } catch (e) {}

    const on = UIP.get("msgr", true);
    const ownTab = isMobile();
    { const s = $("#startSide"); if (s) s.hidden = !on || Messenger.rail || ownTab; }
    if (on && !Messenger.rail && !ownTab) Messenger.mount($("#msgrHost"));
  },
  hide() { try { Cockpit.teardown(); } catch (e) {} if (!Messenger.rail) Messenger.unmount(); },
  init() {

  },
  async loadOverview() {
    this.loadGreeting();
    let d; try { d = await jget("/api/overview"); } catch (e) { d = null; }
    const st = $("#ovStats");
    if (st) {
      st.textContent = "";
      if (!d || !d.ok) { st.appendChild(el("div", { class: "empty", text: "—" })); }
      else {
        const mem = d.mem || {};
        const chip = (ico, big, lab) => el("div", { class: "ov-stat" }, [
          el("span", { class: "ov-ico", text: ico }),
          el("span", { class: "ov-big", text: String(big) }),
          el("span", { class: "ov-lab", text: lab })]);
        st.appendChild(chip("🖥", d.active_vms != null ? d.active_vms : "–", "aktive VMs"));
        st.appendChild(chip("⚙", (d.load_pct != null ? d.load_pct + "%" : "–"), "Auslastung · " + (d.load1 != null ? d.load1 : "–") + "/" + (d.ncpu || "?") + " Kerne"));
        if (mem.used_pct != null) st.appendChild(chip("🧠", mem.used_pct + "%", "Speicher · " + (Math.round((mem.total_mb || 0) / 102.4) / 10) + " GB"));
      }
    }
    const box = $("#continueList"); if (!box) return;
    const rec = (d && Array.isArray(d.recent)) ? d.recent.filter(r => r.sid) : [];
    if (!rec.length) { return this.loadJobs(); }
    box.textContent = "";
    rec.slice(0, 6).forEach(r => {
      box.appendChild(el("a", { class: "list-row", href: "#sessions", onclick: (e) => { e.preventDefault(); openSessionTerminal(r.sid); } }, [
        el("span", { class: "pill", text: r.warm ? "● läuft" : "○ aus" }),
        el("span", { class: "ellipsis", text: r.title || r.sid })]));
    });
  },
  async loadGreeting() {

    const host = $("#biGreeting"); if (!host) return;
    if (localStorage.getItem("pp-bi-overview") === "0") { host.hidden = true; return; }
    let d; try { d = await jget("/api/board/overview"); } catch (e) { d = null; }
    if (!d || !d.ok || d.state === "off") { host.hidden = true; return; }
    host.hidden = false; host.textContent = "";
    const line = (cls, txt) => { if (!txt) return; const e = el("div", { class: cls }); e.textContent = txt; host.appendChild(e); };
    if (d.state === "pending" && !(d.headline || d.greeting)) { line("bi-sit muted", "Lage wird zusammengestellt …"); return; }

    line("bi-greet", d.headline || d.greeting || "Lagebild");
    line("bi-sit", d.situation || "");

    const recs = Array.isArray(d.recommendations) ? d.recommendations.filter(x => (x || "").trim())
                 : (d.next ? [d.next] : []);
    if (recs.length) {
      const ul = el("div", { class: "bi-recs" });
      recs.slice(0, 4).forEach(r => {
        const row = el("div", { class: "bi-rec" });
        row.appendChild(el("span", { class: "bi-rec-mark", text: "▸" }));
        const t = el("span", { class: "bi-rec-txt" }); t.textContent = r; row.appendChild(t);
        ul.appendChild(row);
      });
      host.appendChild(ul);
    }
    const pro = d.proactive || d.tip;
    if (pro) line("bi-tip muted", "💡 " + pro);
    if (d.ts) { const age = Math.max(0, Math.round((Date.now() / 1000 - d.ts) / 60)); line("bi-stamp muted", age < 1 ? "gerade aktualisiert" : ("Stand: vor " + age + " min")); }
  },
  async loadJobs() {
    const box = $("#continueList"); const jobs = await jget("/api/jobs");
    box.textContent = "";
    if (!Array.isArray(jobs) || !jobs.length) { box.appendChild(el("div", { class: "empty", text: "noch keine Aufträge" })); return; }
    jobs.slice(0, 6).forEach(j => {
      const a = el("a", { class: "list-row", href: "/job/" + j.id, target: "_blank" }, [
        el("span", { class: "badge s-" + j.status, text: j.status }),
        el("span", { class: "ellipsis", text: (j.prompt || "").slice(0, 70) })
      ]);
      box.appendChild(a);
    });
  }

};

const UIP = {
  key(k) { return "pp-ui-" + k; },
  get(k, dflt) { const v = localStorage.getItem(this.key(k)); return v === null ? dflt : v === "1"; },
  set(k, on) { localStorage.setItem(this.key(k), on ? "1" : "0"); },
  applyGlobal() { document.body.classList.toggle("ui-compact", this.get("compact", false)); },
};

const Settings = {
  _tab: null,
  show(sub) {
    this.switchTab(sub || this._tab || localStorage.getItem("pp-stg-tab") || "konto");
    this.loadVpn();
    this.loadChannels();
    this.loadProviders();
    this.loadProfile();
    this.loadApprovals();
    this.loadPasskeys();
    this.loadAppPair();
    this.myLlmStatus();
    if (typeof Secrets !== "undefined") Secrets.load();

    if (IS_ADMIN) {

      const rh = $("#stRelayHost"); if (rh && typeof ckFillRelay === "function") ckFillRelay(rh);
      const d = $("#devFrame");   if (d && !d.getAttribute("src")) d.setAttribute("src", "/devices");
      const sf = $("#stSetupFrame"); if (sf && !sf.getAttribute("src")) sf.setAttribute("src", "/einrichtung?embed=1");
      this.brainStatus();
      this.loadUpdate();
      this.loadVoice();
    }
  },
  switchTab(t) {
    if (!document.querySelector('.stg-panel[data-spanel="' + t + '"]')) t = "konto";
    if (t === "box" && !IS_ADMIN) t = "konto";
    this._tab = t; try { localStorage.setItem("pp-stg-tab", t); } catch (e) {}
    $$(".stg-tab").forEach(b => b.classList.toggle("on", b.getAttribute("data-stab") === t));
    $$(".stg-panel").forEach(p => { p.hidden = p.getAttribute("data-spanel") !== t; });
  },
  async loadProviders() {

    const host = $("#stProviders"), card = $("#stProvidersCard");
    if (!host || !card) return;

    const gen = (this._provGen = (this._provGen || 0) + 1);
    let d; try { d = await jget("/api/llm/providers"); } catch (e) { return; }
    if (gen !== this._provGen || !(d && d.ok)) return;
    card.hidden = false; host.textContent = "";
    const FIRST = ["claude", "codex", "ollama", "gemini"];
    const badge = (id) => FIRST.indexOf(String(id || "").toLowerCase()) < 0
      ? el("span", { class: "pill", title: "kein first-class-Anbieter — als ungetestet gekennzeichnet", text: "· ungetestet" }) : null;
    (d.providers || []).forEach(p => {
      if (String(p.id) === "gemini") return;
      host.appendChild(el("div", { class: "prov-row" }, [
        el("b", { text: p.label }),
        el("span", { class: "muted", text: p.installed ? "installiert ✓" : "nicht installiert" }),
        el("span", { class: "muted", text: p.box_account ? "Box-Konto verbunden ✓" : "kein Box-Konto" }),
        p.default ? el("span", { class: "pill role-user", text: "aktiv für Sessions" }) : el("span", { class: "muted", text: "Sessions: folgt" }),
        (IS_ADMIN && p.installed && !p.default) ? el("button", { class: "btn sm ghost", text: "Verbinden (Admin-Reiter)",
          onclick: () => { try { Router.go("admin"); Admin.switchTab("llm"); Admin.oauthStart("primary", p.id); } catch (e) {} } }) : null,
      ]));
    });
    if (!IS_ADMIN) return;

    try {
      const g = await jget("/api/admin/llm/gemini");
      if (gen !== this._provGen) return;
      if (g && g.ok) {
        const box = el("div", { class: "prov-row col" });
        box.appendChild(el("div", { class: "prov-head" }, [el("b", { text: "Gemini (Google)" }),
          el("span", { class: g.connected ? "pill role-user" : "muted", text: g.connected ? "verbunden ✓" : "nicht verbunden" }),
          el("span", { class: "muted", text: g.installed ? "CLI ✓" : "CLI fehlt" }),
          (g.models || []).length ? el("span", { class: "muted", text: g.models.length + " Modelle" }) : null]));
        const gi = el("input", { type: "password", placeholder: "API-Key aus Google AI Studio", autocomplete: "off", spellcheck: "false" });
        box.appendChild(el("div", { class: "prov-form" }, [gi,
          el("button", { class: "btn sm", text: "Verbinden", onclick: async () => {
            const key = (gi.value || "").trim(); if (!key) { toast("API-Key fehlt"); return; }
            const r = await apost("/api/admin/llm/gemini", { action: "set_key", api_key: key });
            if (r && r.ok) { toast(r.connected ? "Gemini verbunden ✓" : "gespeichert"); gi.value = ""; this.loadProviders(); }
            else toast((r && r.error) || "Fehler", true);
          } }),
          g.connected ? el("button", { class: "btn sm ghost", text: "Trennen", onclick: async () => {
            const r = await apost("/api/admin/llm/gemini", { action: "clear" }); if (r && r.ok) { toast("Gemini getrennt"); this.loadProviders(); }
          } }) : null]));
        host.appendChild(box);
      }
    } catch (e) {}

    try {
      const e = await jget("/api/admin/llm/endpoints");
      if (gen !== this._provGen) return;
      if (e && e.ok) { this._epPresets = e.presets || []; (e.providers || []).forEach(p => host.appendChild(this.endpointRow(p, badge))); }
    } catch (er) {}

    const add = el("details", { class: "prov-add" });
    add.appendChild(el("summary", { text: "＋ Weiterer Anbieter (OpenAI-kompatibel)" }));
    add.appendChild(el("div", { class: "muted", text: "Getestet sind nur Claude, Codex, Ollama und Gemini — jeder weitere Anbieter ist ausdrücklich als ungetestet markiert." }));
    const ni = el("input", { placeholder: "id (klein, z. B. mistral)", spellcheck: "false" });
    const nn = el("input", { placeholder: "Anzeigename (z. B. Mistral)", spellcheck: "false" });
    const nu = el("input", { placeholder: "Base-URL (https://… oder http://host:port)", spellcheck: "false" });
    const nk = el("input", { type: "password", placeholder: "API-Key (optional, für kommerzielle)", autocomplete: "off" });

    const pHint = el("div", { class: "muted" });
    const pre = el("select", { title: "Vorlage: bekannte Anbieter mit fertiger Base-URL" },
      [el("option", { value: "", text: "Vorlage wählen … (oder Felder selbst füllen)" })]);
    ((this._epPresets) || []).forEach(t => pre.appendChild(el("option", { value: t.id, text: t.name + " · ungetestet" })));
    pre.addEventListener("change", () => {
      const t = ((this._epPresets) || []).find(x => x.id === pre.value);
      pHint.textContent = "";
      if (!t) return;
      ni.value = t.id; nn.value = t.name; nu.value = t.base_url;
      pHint.textContent = "API-Key erstellen: ";
      pHint.appendChild(el("a", { href: t.key_console, target: "_blank", rel: "noopener", text: t.key_console }));
    });
    add.appendChild(el("div", { class: "prov-form" }, [pre]));
    add.appendChild(pHint);
    add.appendChild(el("div", { class: "prov-form" }, [ni, nn, nu, nk,
      el("button", { class: "btn sm", text: "Anlegen", onclick: async () => {
        const entry = { id: (ni.value || "").trim().toLowerCase(), name: (nn.value || "").trim(),
          base_url: (nu.value || "").trim(), api_key: (nk.value || "").trim(), discovery: "openai" };
        const r = await apost("/api/admin/llm/endpoints", { action: "save", entry });
        if (r && r.ok) { toast("Anbieter angelegt — als ungetestet gekennzeichnet"); this.loadProviders(); }
        else toast((r && r.error) || "Fehler", true);
      } })]));
    host.appendChild(add);
  },
  endpointRow(p, badge) {

    const box = el("div", { class: "prov-row col" });
    box.appendChild(el("div", { class: "prov-head" }, [el("b", { text: p.name || p.id }), badge(p.id),
      el("span", { class: "muted", text: p.discovery === "ollama" ? "ollama" : "OpenAI-kompatibel" })]));
    const url = el("input", { value: p.base_url || "", spellcheck: "false", title: "Endpoint Base-URL" });
    const key = el("input", { type: "password", autocomplete: "off",
      placeholder: p.has_key ? "API-Key gesetzt (leer = unverändert)" : "API-Key (optional)" });
    const sel = el("select", { title: "Modell fürs Gehirn (leer = Server-Standard)" },
      [el("option", { value: "", text: "Server-Standard / automatisch" })]);
    if (p.model) sel.appendChild(el("option", { value: p.model, text: p.model, selected: true }));

    sel.appendChild(el("option", { value: "__manuell__", text: "Modell manuell eingeben …" }));
    const manu = el("input", { placeholder: "Modell-ID (z. B. mistral-large-latest)", spellcheck: "false", hidden: true });
    sel.addEventListener("change", () => { manu.hidden = sel.value !== "__manuell__"; if (!manu.hidden) manu.focus(); });
    const status = el("span", { class: "muted", text: "" });
    const test = async () => {
      status.textContent = "teste …";
      let qs = "discovery=" + encodeURIComponent(p.discovery || "");
      if (key.value) qs += "&base_url=" + encodeURIComponent(url.value.trim()) + "&api_key=" + encodeURIComponent(key.value);
      else if (p.has_key) qs += "&id=" + encodeURIComponent(p.id);
      else qs += "&base_url=" + encodeURIComponent(url.value.trim());
      let r; try { r = await jget("/api/admin/llm/endpoints/models?" + qs); } catch (e) { r = null; }
      if (r && r.ok && (r.models || []).length) {
        status.textContent = "erreichbar ✓ · " + r.models.length + " Modelle";
        const cur = sel.value; sel.textContent = "";
        sel.appendChild(el("option", { value: "", text: "Server-Standard / automatisch" }));
        r.models.forEach(m => sel.appendChild(el("option", { value: m.id, text: m.label || m.id, selected: m.id === cur })));
      } else status.textContent = (r && r.error) ? r.error : "nicht erreichbar";
    };
    const save = async () => {
      const mdl = sel.value === "__manuell__" ? (manu.value || "").trim() : sel.value;
      const entry = { id: p.id, name: p.name, base_url: url.value.trim(), discovery: p.discovery, model: mdl };
      if (key.value) entry.api_key = key.value;
      const r = await apost("/api/admin/llm/endpoints", { action: "save", entry });
      if (r && r.ok) { toast("gespeichert ✓"); this.loadProviders(); } else toast((r && r.error) || "Fehler", true);
    };
    const form = [url];
    if (p.discovery !== "ollama") form.push(key);
    form.push(sel, manu,
      el("button", { class: "btn sm ghost", text: "Verbindung testen", onclick: test }),
      el("button", { class: "btn sm", text: "Speichern", onclick: save }), status);
    box.appendChild(el("div", { class: "prov-form" }, form));
    return box;
  },
  async loadChannels() {

    const s = $("#tgStatus"); if (!s) return;
    let d; try { d = await jget("/api/channels/status"); } catch (e) { d = null; }
    const ch = (d && d.ok !== false && d.channels && d.channels.telegram) || null;
    const on = !!(ch && ch.enrolled);
    s.textContent = on
      ? ("✅ Bot verbunden" + (ch.username ? " (@" + ch.username + ")" : "")
         + (ch.chat_bound ? " · Chat gebunden" : " · jetzt dem Bot eine Nachricht schicken, dann bindet er deinen Chat")
         + (ch.live_token ? "" : " · ⚠️ Token nach Box-Neustart neu eingeben"))
      : "Kein Bot verbunden.";
    { const b = $("#tgDisable"); if (b) b.hidden = !on; }

    { const old = $("#tgVerboseRow"); if (old) old.remove(); }
    if (on && ch && ch.chat_bound) {
      const row = el("label", { id: "tgVerboseRow", style: "display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer" });
      const cb = el("input", { type: "checkbox" }); cb.checked = !!ch.verbose;
      cb.addEventListener("change", async () => {
        const r = await jpost("/api/channels/verbose", { channel: "telegram", on: cb.checked });
        if (r && r.ok !== false) toast(cb.checked ? "Telegram spiegelt die Channels" : "Telegram nur noch Ergebnisse & Rückfragen");
        else { cb.checked = !cb.checked; toast("Konnte nicht speichern", true); }
      });
      row.appendChild(cb);
      row.appendChild(el("span", { html: "<b>Telegram spiegelt die Channels</b> — voller Verlauf wie im tmux (abschalten = nur Ergebnisse &amp; Rückfragen)" }));
      s.parentNode.appendChild(row);
    }
  },
  async tgEnroll() {
    const inp = $("#tgToken"); const token = (inp && inp.value || "").trim();
    if (!token) { toast("Bot-Token fehlt"); return; }
    const r = await jpost("/api/channels/enroll", { channel: "telegram", token });
    if (r && r.ok !== false) { toast("Bot verbunden ✓"); if (inp) inp.value = ""; }
    else toast("Verbinden fehlgeschlagen: " + ((r && (r.error || r.detail)) || "Token geprüft?"));
    this.loadChannels();
  },
  async tgDisable() {
    const r = await jpost("/api/channels/disable", { channel: "telegram" });
    toast(r && r.ok !== false ? "Bot getrennt" : "Fehler");
    this.loadChannels();
  },
  async loadProfile() {
    let d; try { d = await jget("/api/me/profile"); } catch (e) { return; }
    if (!(d && d.ok)) return;
    { const i = $("#meName"); if (i && !i.matches(":focus")) i.value = d.name || ""; }
    { const i = $("#meEmail"); if (i && !i.matches(":focus")) i.value = d.email || ""; }
    { const c = $("#meMailCopy"); if (c) c.checked = !d.email_optout; }
    { const s = $("#meStatus"); if (!s) return;
      s.textContent = "";
      if (!d.email) { s.textContent = "Ohne E-Mail: kein Passwort-Reset, keine Mail-Kopien von Box-Nachrichten."; }
      else if (d.email_verified) { s.textContent = "E-Mail bestätigt ✓"; }
      else {
        s.appendChild(el("span", { text: "E-Mail unbestätigt — " }));
        s.appendChild(el("button", { class: "btn sm ghost", text: "📧 Bestätigungs-Mail senden", onclick: async () => {
          const r = await jpost("/api/auth/send-verification", {});
          toast(r && r.ok !== false ? "Bestätigungs-Mail verschickt — Posteingang prüfen" : "Versand fehlgeschlagen: " + ((r && (r.detail || r.error)) || "?"));
        } }));
      } }
  },
  async saveProfile() {
    const r = await jpost("/api/me/profile", {
      name: ($("#meName") && $("#meName").value || "").trim(),
      email: ($("#meEmail") && $("#meEmail").value || "").trim(),
    });
    toast(r && r.ok ? "Profil gespeichert" : "Fehler: " + ((r && r.error) || "?"));
    this.loadProfile();
  },

  async changePassword() {
    const st = $("#pwStatus");
    const say = (m) => { if (st) st.textContent = m; };
    const old = ($("#pwOld") && $("#pwOld").value) || "";
    const nw  = ($("#pwNew") && $("#pwNew").value) || "";
    const nw2 = ($("#pwNew2") && $("#pwNew2").value) || "";
    if (!old || !nw) { say("Bitte aktuelles und neues Passwort eingeben."); return; }
    if (nw !== nw2) { say("Die neuen Passwörter stimmen nicht überein."); return; }
    say("…");
    let r; try { r = await jpost("/api/user/password", { old, "new": nw }); } catch (e) { r = null; }
    if (r && r.ok) {
      ["pwOld", "pwNew", "pwNew2"].forEach(id => { const i = $("#" + id); if (i) i.value = ""; });
      say("✓ Passwort geändert."); toast("✓ Passwort geändert");
    } else {
      say((r && r.error) || "Konnte das Passwort nicht ändern.");
    }
  },

  _tierLabel(t) { return ({ auto: "Keine", confirm: "Bestätigung", twofa: "2FA" })[t] || t; },
  _segControl(current, choices, onPick) {

    const all = ["auto", "confirm", "twofa"];
    const wrap = el("span", { class: "seg", style: "display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden" });
    all.forEach(t => {
      const allowed = choices.indexOf(t) >= 0;
      const on = current === t;
      const b = el("button", {
        class: "btn sm" + (on ? "" : " ghost"),
        style: "border:0;border-radius:0;padding:5px 10px" + (allowed ? "" : ";opacity:.35;cursor:not-allowed"),
        text: this._tierLabel(t),
        title: allowed ? "" : "Für diese Art gesperrt (Systemvorgabe, nur höher einstellbar)",
      });
      if (allowed && !on) b.addEventListener("click", () => onPick(t));
      wrap.appendChild(b);
    });
    return wrap;
  },
  _matrixRow(label, hint, current, choices, onPick) {
    const row = el("div", { class: "row", style: "display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:8px 0;border-top:1px solid var(--line);padding-top:8px" });
    const txt = el("span", { style: "flex:1;min-width:180px" });
    txt.appendChild(el("b", { text: label }));
    if (hint) txt.appendChild(el("span", { class: "card-hint", style: "display:block", text: hint }));
    row.appendChild(txt);
    row.appendChild(this._segControl(current, choices, onPick));
    return row;
  },
  async loadApprovals() {
    const preHost = $("#stFreigabenPreset"), mHost = $("#stFreigabenMatrix");
    if (!mHost) return;
    let d; try { d = await jget("/api/freigaben"); } catch (e) { d = null; }
    if (preHost) preHost.textContent = "";
    mHost.textContent = "";
    if (!(d && d.ok)) {
      mHost.appendChild(el("div", { class: "empty", text: (d && d.error) || "Freigabe-Einstellungen gerade nicht erreichbar." }));
      return;
    }

    if (preHost) {
      preHost.appendChild(el("span", { class: "card-hint", style: "align-self:center", text: "Schnellwahl:" }));
      (d.presets || []).forEach(p => {
        const on = p.level === d.level;
        const b = el("button", { class: "btn sm" + (on ? "" : " ghost"), text: p.short, title: p.experience });
        if (!on) b.addEventListener("click", async () => {
          const r = await jpost("/api/freigaben", { level: p.level });
          if (r && r.ok) { toast("Grundton: " + p.short); this.loadApprovals(); }
          else toast("Konnte nicht speichern: " + ((r && r.error) || "?"), true);
        });
        preHost.appendChild(b);
      });
    }

    (d.matrix || []).forEach(m => {
      mHost.appendChild(this._matrixRow(m.label, m.hint, m.effective, m.choices, async (tier) => {
        const r = await jpost("/api/freigaben", { action_class: m.action_class, tier });
        if (r && r.ok) { toast(m.label + " → " + this._tierLabel(tier)); this.loadApprovals(); }
        else toast("Konnte nicht speichern: " + ((r && r.error) || "?"), true);
      }));
    });

    let a; try { a = await jget("/api/approval-prefs"); } catch (e) { a = null; }
    if (a && a.ok && (a.groups || []).length) {
      const g = a.groups.find(x => x.key === "commission") || a.groups[0];
      const cur = g.needs_approval ? "confirm" : "auto";
      mHost.appendChild(this._matrixRow("Aufträge ausführen",
        "Aufgaben, die die Box mehrstufig für dich baut (Housekeeping)", cur, ["auto", "confirm"],
        async (tier) => {
          const r = await jpost("/api/approval-prefs", { task_types: g.task_types, needs_approval: tier === "confirm" });
          if (r && r.ok) { toast("Aufträge → " + this._tierLabel(tier)); this.loadApprovals(); }
          else toast("Konnte nicht speichern: " + ((r && r.error) || "?"), true);
        }));
    }
  },

  _b64uToBuf(s) { s = String(s).replace(/-/g, "+").replace(/_/g, "/"); const p = s.length % 4; if (p) s += "=".repeat(4 - p); const bin = atob(s), b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return b.buffer; },
  _bufToB64u(buf) { const b = new Uint8Array(buf); let s = ""; for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); },
  async _passkeyRegister() {
    try {
      const opt = await jpost("/api/passkey/register/begin", {});
      if (!(opt && opt.ok && opt.publicKey)) throw new Error((opt && opt.error) || "Start fehlgeschlagen");
      const pk = opt.publicKey;
      const label = (prompt("Name für diesen Passkey (z. B. „iPhone“):", "Mein Gerät") || "Passkey").slice(0, 48);
      const cred = await navigator.credentials.create({ publicKey: {
        rp: pk.rp, user: { id: this._b64uToBuf(pk.user.id), name: pk.user.name, displayName: pk.user.displayName },
        challenge: this._b64uToBuf(pk.challenge), pubKeyCredParams: pk.pubKeyCredParams, timeout: pk.timeout,
        attestation: pk.attestation || "none", authenticatorSelection: pk.authenticatorSelection,
        excludeCredentials: (pk.excludeCredentials || []).map(c => ({ type: "public-key", id: this._b64uToBuf(c.id) })) } });
      const resp = { id: cred.id, rawId: this._bufToB64u(cred.rawId), type: cred.type, response: {
        attestationObject: this._bufToB64u(cred.response.attestationObject),
        clientDataJSON: this._bufToB64u(cred.response.clientDataJSON) } };
      const r = await jpost("/api/passkey/register/finish", { cid: opt.cid, response: resp, label });
      if (r && r.ok) { toast("Passkey angelegt: " + label); this.loadPasskeys(); }
      else toast("Registrierung fehlgeschlagen: " + ((r && r.error) || "?"), true);
    } catch (e) { toast("Passkey: " + (e && e.message ? e.message : e), true); }
  },
  async loadAppPair(){
    const host = $("#stAppPairHost"); if(!host) return;
    const self=this;
    const render = (d) => {
      host.textContent = "";
      if(!(d && d.ok)){ host.appendChild(el("div",{class:"card-hint",style:"margin-top:8px",text:(d&&d.error)||"gerade nicht verfügbar"})); return; }
      self._appPairCode = d.code;
      const img = el("img",{alt:"Kopplungs-QR",style:"width:220px;max-width:78%;display:block;margin:10px auto 6px;border-radius:12px;background:#fff;padding:8px"});
      if(d.qr){ img.src = d.qr; host.appendChild(img); }
      host.appendChild(el("div",{class:"card-hint",style:"text-align:center",text:"mit dem Handy scannen \u2192 "+(d.app||"app.brainarbeit.com")}));
      const det = el("details",{style:"margin-top:10px"});
      det.appendChild(el("summary",{class:"card-hint",style:"cursor:pointer",text:"Keine Kamera? Code manuell eingeben"}));
      const box = el("div",{style:"margin-top:8px;font-size:12px;line-height:1.7"});
      box.appendChild(el("div",{html:"1. \u00d6ffne <b>app.brainarbeit.com</b> auf dem Handy"}));
      box.appendChild(el("div",{html:"2. Tippe \u201eGer\u00e4t koppeln\u201c und gib den Code ein:"}));
      box.appendChild(el("div",{style:"margin-top:6px",html:"Code: <b style='font-family:monospace;letter-spacing:1px'>"+(d.code||"")+"</b>"}));
      box.appendChild(el("a",{href:d.url,style:"display:inline-block;margin-top:8px",text:"\u2026 oder diesen Link auf dem Handy \u00f6ffnen"}));
      det.appendChild(box); host.appendChild(det);
    };
    const pull = async () => {
      if(host.offsetParent===null) return;
      try{ const d = await jpost("/api/pair/app-qr", self._appPairCode?{code:self._appPairCode}:{}); render(d); }catch(e){}
    };
    if(this._appPairTimer){ clearInterval(this._appPairTimer); }
    await pull();
    this._appPairTimer = setInterval(pull, 25000);
  },
  async loadPasskeys() {
    const card = $("#stPasskeyCard"), host = $("#stPasskeyHost");
    if (!card || !host) return;
    if (!window.PublicKeyCredential) { card.hidden = true; return; }
    let a; try { a = await jget("/api/passkey/available"); } catch (e) { a = null; }
    if (!(a && a.available)) { card.hidden = true; return; }
    let d; try { d = await jget("/api/passkey/list"); } catch (e) { d = null; }
    card.hidden = false; host.textContent = "";
    const add = el("button", { class: "btn sm", text: "➕ Passkey hinzufügen" });
    add.addEventListener("click", () => this._passkeyRegister());
    host.appendChild(add);
    if (!(d && d.ok)) { host.appendChild(el("div", { class: "card-hint", style: "margin-top:8px", text: "Liste gerade nicht erreichbar." })); return; }
    const creds = d.credentials || [];
    if (!creds.length) host.appendChild(el("div", { class: "card-hint", style: "margin-top:8px", text: "Noch kein Passkey hinterlegt." }));
    creds.forEach(c => {
      const row = el("div", { class: "row", style: "display:flex;align-items:center;gap:10px;margin:8px 0;border-top:1px solid var(--line);padding-top:8px" });
      const t = el("span", { style: "flex:1;min-width:140px" });
      t.appendChild(el("b", { text: c.label || "Passkey" }));
      t.appendChild(el("span", { class: "card-hint", style: "display:block", text: (c.kind || "") + (c.last_used ? " · schon genutzt" : " · noch nicht genutzt") }));
      row.appendChild(t);
      const rm = el("button", { class: "btn sm ghost", text: "entfernen" });
      rm.addEventListener("click", async () => { const r = await jpost("/api/passkey/remove", { id: c.id }); if (r && r.ok) { toast("Passkey entfernt"); this.loadPasskeys(); } else toast("Konnte nicht entfernen", true); });
      row.appendChild(rm);
      host.appendChild(row);
    });
    const bioRow = el("div", { class: "row", style: "display:flex;align-items:center;gap:10px;margin:10px 0;border-top:1px solid var(--line);padding-top:10px" });
    const lbl = el("label", { style: "flex:1;display:flex;align-items:center;gap:8px;cursor:pointer" });
    const cb = el("input", { type: "checkbox" });
    cb.checked = !!d.biometric_confirm;
    if (!creds.length) cb.disabled = true;
    cb.addEventListener("change", async () => {
      const r = await jpost("/api/passkey/biometric", { on: cb.checked });
      if (r && r.ok) toast(cb.checked ? "Bestätigungen per Fingerabdruck: an" : "Bestätigungen per Fingerabdruck: aus");
      else { cb.checked = !cb.checked; toast("Konnte nicht speichern", true); }
    });
    lbl.appendChild(cb);
    lbl.appendChild(el("span", { html: "<b>Bestätigungen per Fingerabdruck</b> — statt Handy-Code (der Code bleibt als Rückweg)" }));
    bioRow.appendChild(lbl);
    host.appendChild(bioRow);
  },
  init() {

    $$(".stg-tab").forEach(b => b.addEventListener("click", () => this.switchTab(b.getAttribute("data-stab"))));

    { const c = $("#uiMsgrStart"); if (c) { c.checked = UIP.get("msgr", true);
        c.addEventListener("change", () => { UIP.set("msgr", c.checked); Rail.apply(); toast(c.checked ? "Messenger-Leiste: an" : "Messenger-Leiste: aus"); }); } }
    { const c = $("#uiCompact"); if (c) { c.checked = UIP.get("compact", false);
        c.addEventListener("change", () => { UIP.set("compact", c.checked); UIP.applyGlobal(); }); } }
    { const b = $("#stVault"); if (b) b.addEventListener("click", () => (typeof Vault !== "undefined" ? Vault.toggle() : location.href = "/settings")); }
    { const b = $("#stLogout"); if (b) b.addEventListener("click", () => logout()); }

    { const b = $("#tgEnroll"); if (b) b.addEventListener("click", () => this.tgEnroll()); }
    { const b = $("#tgDisable"); if (b) b.addEventListener("click", () => this.tgDisable()); }

    { const b = $("#meSave"); if (b) b.addEventListener("click", () => this.saveProfile()); }
    { const b = $("#pwChange"); if (b) b.addEventListener("click", () => this.changePassword()); }
    { const c = $("#meMailCopy"); if (c) c.addEventListener("change", async () => {
        const r = await jpost("/api/me/profile", { email_optout: c.checked ? 0 : 1 });
        toast(r && r.ok ? (c.checked ? "E-Mail-Kopien: an" : "E-Mail-Kopien: aus") : "Fehler");
      }); }
    { const b = $("#stVpnReload"); if (b) b.addEventListener("click", () => this.loadVpn()); }
    { const w = $("#stWhoami"); if (w) w.textContent = USER ? ("angemeldet als: " + USER + (ROLE && ROLE !== "user" ? " · " + ROLE : "")) : ""; }

    { const b = $("#stBrainRenew"); if (b) b.addEventListener("click", () => this.brainStart()); }
    { const b = $("#stBrainSubmit"); if (b) b.addEventListener("click", () => this.brainCode()); }
    { const c = $("#stBrainCode"); if (c) c.addEventListener("keydown", e => { if (e.key === "Enter") this.brainCode(); }); }
    { const b = $("#stBrainCopy"); if (b) b.addEventListener("click", () => { try { navigator.clipboard.writeText(this._brainUrl || ""); toast("Link kopiert"); } catch (e) {} }); }
    { const b = $("#stBrainCancel"); if (b) b.addEventListener("click", () => this.brainCancel()); }
    { const b = $("#stBrainLogout"); if (b) b.addEventListener("click", () => this.brainLogout()); }

    { const b = $("#myLlmRenew"); if (b) b.addEventListener("click", () => this.myLlmStart()); }
    { const b = $("#myLlmSubmit"); if (b) b.addEventListener("click", () => this.myLlmCode()); }
    { const c = $("#myLlmCode"); if (c) c.addEventListener("keydown", e => { if (e.key === "Enter") this.myLlmCode(); }); }
    { const b = $("#myLlmCopy"); if (b) b.addEventListener("click", () => { try { navigator.clipboard.writeText(this._myUrl || ""); toast("Link kopiert"); } catch (e) {} }); }
    { const b = $("#myLlmCancel"); if (b) b.addEventListener("click", () => this.myLlmCancel()); }
    { const b = $("#myLlmLogout"); if (b) b.addEventListener("click", () => this.myLlmLogout()); }

    if (IS_ADMIN) ["#stgBoxHead", "#stgBoxCard", "#stDevCard", "#stFrgCard", "#stShellCard", "#stUpdateCard", "#stVoiceCard", "#stSetupCard", "#stgTabBox"].forEach(id => { const n = $(id); if (n) n.hidden = false; });
    { const b = $("#shellAdd"); if (b) b.addEventListener("click", () => this.addShellKey()); }
    if (IS_ADMIN) this.loadShellKeys();

    { const b = $("#stUpdCheck"); if (b) b.addEventListener("click", () => this.updateCheck()); }
    { const b = $("#stUpdApply"); if (b) b.addEventListener("click", () => this.updateApply()); }
    { const b = $("#stUpdRollback"); if (b) b.addEventListener("click", () => this.updateRollback()); }
    { const b = $("#stVoiceInstall"); if (b) b.addEventListener("click", () => this.voiceInstall()); }
    { const b = $("#stSetupOpen"); if (b) b.addEventListener("click", () => window.open("/einrichtung", "_blank", "noopener")); }

    { const b = $("#rightsReqSend"); if (b) b.addEventListener("click", async () => {
        const t = ($("#rightsReqText") || {}).value || "";
        if (!t.trim()) { toast("Bitte kurz sagen, was du möchtest."); return; }
        let r; try { r = await jpost("/api/rights/request", { text: t.trim() }); } catch (e) { r = null; }
        if (r && r.ok) { toast("✅ Anfrage ist beim Besitzer"); $("#rightsReqText").value = ""; }
        else toast((r && r.error) || "Anfrage nicht angekommen");
      }); }
    { const b = $("#devScanSave"); if (b) b.addEventListener("click", () => this.saveScanCfg({ interval_min: parseInt(($("#devScanIv") || {}).value, 10) || 15 })); }
    { const c = $("#devScanAktiv"); if (c) c.addEventListener("change", () => this.saveScanCfg({ aktiv_suchen: !!c.checked })); }
    { const b = $("#devScanPause2"); if (b) b.addEventListener("click", () => this.saveScanCfg({ pause_hours: 2 })); }
    { const b = $("#devScanPause24"); if (b) b.addEventListener("click", () => this.saveScanCfg({ pause_hours: 24 })); }
    { const b = $("#devScanResume"); if (b) b.addEventListener("click", () => this.saveScanCfg({ pause_hours: 0 })); }
    if (IS_ADMIN) this.loadScanCfg();
  },

  async loadShellKeys() {
    const box = $("#shellKeys"), st = $("#shellState");
    if (!box || !st) return;
    let r; try { r = await jget("/api/admin/shell-keys"); } catch (e) { r = null; }
    if (!r || !r.ok) { st.textContent = "Status nicht abrufbar."; box.innerHTML = ""; return; }
    this._shellPw = r.password_auth;
    this._shellFactor = r.stepup_factor;
    {

      const f = $("#shellTotp");
      if (f) {
        if (r.stepup_factor === "totp") {
          f.type = "text"; f.placeholder = "2FA-Code vom Handy";
          f.inputMode = "numeric"; f.maxLength = 6;
        } else {
          f.type = "password"; f.placeholder = "eigenes Portal-Passwort";
          f.removeAttribute("inputmode"); f.removeAttribute("maxlength");
        }
      }
    }
    const pw = r.password_auth === true ? "Passwortanmeldung über SSH: an"
      : r.password_auth === false ? "Passwortanmeldung über SSH: aus (nur Schlüssel)"
      : "Passwortanmeldung über SSH: nicht ermittelbar";
    st.textContent = "Benutzer " + r.user + " · " + r.count + " Schlüssel · " + pw;
    box.innerHTML = "";
    if (!r.keys.length) {
      box.appendChild(el("div", { class: "empty", text: "Kein Schlüssel eingetragen — SSH ist zu." }));
      return;
    }
    r.keys.forEach(k => {
      const row = el("div", { class: "shell-key" });
      if (!k.parsed) {

        row.appendChild(el("div", { class: "shell-key-main" }, [
          el("b", { text: "unbekannte Zeile" }),
          el("div", { class: "card-hint", text: k.comment })]));
        row.appendChild(el("span", { class: "card-hint", text: "nur von Hand änderbar" }));
      } else {
        row.appendChild(el("div", { class: "shell-key-main" }, [
          el("b", { text: (k.comment || "ohne Bezeichnung") }),
          el("div", { class: "card-hint", text: k.type + " · " + k.fp })]));
        row.appendChild(el("button", {
          class: "btn sm ghost danger", text: "Entfernen",
          onclick: () => this.removeShellKey(k)
        }));
      }
      box.appendChild(row);
    });
  },
  async addShellKey() {
    const ta = $("#shellNew"), tc = $("#shellTotp"), m = $("#shellMsg");
    if (!ta || !m) return;
    const keys = (ta.value || "").trim();
    if (!keys) { m.textContent = "Bitte den öffentlichen Schlüssel einfügen."; return; }
    if (/PRIVATE KEY/i.test(keys)) {

      m.textContent = "Das ist der GEHEIME Schlüssel — der bleibt bei dir. Gebraucht wird die Datei mit .pub am Ende.";
      return;
    }
    const code = (tc && tc.value || "").trim();
    const totp = this._shellFactor === "totp";
    if (!code || (totp && code.length < 6)) {
      m.textContent = totp ? "6-stelligen 2FA-Code vom Handy eingeben."
                           : "Zur Bestätigung das eigene Portal-Passwort eingeben.";
      if (tc) tc.focus(); return;
    }
    m.textContent = "…";
    const proof = totp ? { totp: code } : { password: code };
    let r; try { r = await jpost("/api/admin/shell-keys/add", Object.assign({ keys }, proof)); } catch (e) { r = null; }
    if (tc) tc.value = "";
    if (!r || !r.ok) { m.textContent = this.shellErr(r); return; }
    ta.value = "";
    m.textContent = r.added ? (r.added + " Schlüssel eingetragen.") : "Schlüssel war schon eingetragen.";
    toast(r.added ? "🚪 Schlüssel eingetragen" : "🚪 Schlüssel war schon da");
    this.loadShellKeys();
  },
  async removeShellKey(k, force) {
    const label = k.comment || k.fp;
    if (!force && !confirm("Schlüssel entfernen?\n\n" + label + "\n" + k.fp +
        "\n\nWer nur diesen Schlüssel hat, kommt danach nicht mehr per SSH auf die Box.")) return;
    const totp = this._shellFactor === "totp";
    const code = window.prompt(totp ? "2FA-Code vom Handy — Entfernen bestätigen:"
                                    : "Eigenes Portal-Passwort — Entfernen bestätigen:");
    if (code === null || !code.trim()) { toast("Abgebrochen — nichts passiert."); return; }
    const proof = totp ? { totp: code.trim() } : { password: code.trim() };
    let r; try {
      r = await jpost("/api/admin/shell-keys/remove",
                      Object.assign({ fp: k.fp, force: !!force }, proof));
    } catch (e) { r = null; }
    if (r && r.would_lock_out) {

      if (confirm(r.error + "\n\nTrotzdem entfernen?")) return this.removeShellKey(k, true);
      toast("Abgebrochen — nichts passiert.");
      return;
    }
    if (!r || !r.ok) { toast(this.shellErr(r)); return; }
    toast("🚪 Schlüssel entfernt");
    this.loadShellKeys();
  },
  shellErr(r) {
    if (!r) return "Keine Antwort von der Box.";

    const T = {
      ssh_too_long: "Eingabe zu lang.", ssh_too_many: "Zu viele Schlüssel auf einmal.",
      ssh_bad_line: "Zeile %N%: das sieht nicht nach einem öffentlichen Schlüssel aus.",
      ssh_bad_type: "Zeile %N%: unbekannter Schlüsseltyp — die Zeile muss mit ssh-ed25519, ssh-rsa oder ecdsa-… beginnen.",
      ssh_bad_b64: "Zeile %N%: der Schlüssel ist beschädigt.",
      ssh_bad_body: "Zeile %N%: der Schlüssel ist unvollständig — vermutlich beim Kopieren abgeschnitten.",
    };
    if (r.error_key) {
      const p = String(r.error_key).split("|");
      const t = T[p[0]] || String(r.error_key);
      return p[1] ? t.replace("%N%", p[1]) : t;
    }
    if (r.need_2fa_enrollment) return "2FA ist nicht eingerichtet — erst unter „Off-LAN Freigaben & 2FA“ scharfschalten.";
    if (r.need_2fa || r.need_stepup) return r.error || "Bestätigung stimmt nicht.";
    return r.error || "Fehler.";
  },
  async loadScanCfg() {
    const st = $("#devScanState"); if (!st) return;
    let r; try { r = await jget("/api/devices/scan-config"); } catch (e) { r = null; }
    const c = (r && r.ok && r.config) || null;
    if (!c) { st.textContent = "Status nicht abrufbar"; return; }
    { const iv = $("#devScanIv"); if (iv && !iv.value) iv.value = c.interval_min; }
    { const ck = $("#devScanAktiv"); if (ck) ck.checked = !!c.aktiv_suchen; }

    st.textContent = !c.active
      ? (c.paused_remaining_s ? ("pausiert · noch " + Math.ceil(c.paused_remaining_s / 60) + " min") : "aus")
      : (c.aktiv_suchen ? ("sucht selbst · alle " + c.interval_min + " min")
                        : "still · antwortet nur, liest mit");
  },
  async saveScanCfg(body) {
    let r; try { r = await jpost("/api/devices/scan-config", body); } catch (e) { r = null; }
    toast(r && r.ok ? "🔌 Geräte-Scan aktualisiert" : (r && r.error) || "Fehler");
    this.loadScanCfg();
  },

  BRAIN_ID: "primary", _brainUrl: "", _brainPoll: null,
  async brainStatus() {
    const s = $("#stBrainStatus"); if (!s || !IS_ADMIN) return;
    let r; try { r = await jget("/api/admin/llm/oauth/status?id=" + encodeURIComponent(this.BRAIN_ID)); } catch (e) { r = null; }
    if (!r || r.ok === false) { s.textContent = "Status nicht abrufbar."; return; }
    if (r.logged_in) s.textContent = "✅ verbunden" + (r.email ? " als " + r.email : "");
    else s.textContent = "⚠️ nicht verbunden — Box-Gehirn braucht eine Anmeldung.";
    { const lo = $("#stBrainLogout"); if (lo) lo.hidden = !r.logged_in; }
  },
  async brainStart() {
    if (!IS_ADMIN) return;

    const msg = $("#stBrainMsg"); const flow = $("#stBrainFlow");
    if (msg) msg.textContent = "";
    if (flow) flow.hidden = false;
    let r; try { r = await jpost("/api/admin/llm/login/start", { id: this.BRAIN_ID }); } catch (e) { r = null; }
    if (!r || !r.ok) { if (msg) msg.textContent = "✗ " + ((r && (r.detail || r.error || r.msg)) || "Anmeldung konnte nicht gestartet werden."); return; }
    this._brainLane = r.session;
    Settings._laneRenderBrain(r);
    if (this._brainPoll) clearInterval(this._brainPoll);
    this._brainPoll = setInterval(() => this.brainPoll(), 1000);
  },
  _laneRenderBrain(st) {
    const term = $("#stBrainTerm");
    if (term) { term.hidden = false; term.textContent = (st.lines || []).join("\n"); term.scrollTop = term.scrollHeight; }
    if (st.url) {
      this._brainUrl = st.url;
      const a = $("#stBrainUrl"); if (a) { a.href = st.url; }
      const msg = $("#stBrainMsg");
      if (msg && !msg.textContent) msg.textContent = "Link öffnen (falsches Konto? → Inkognito-Tab), im gewünschten Konto anmelden, Code unten einfügen.";
    }
  },
  async brainPoll() {
    let p; try { p = await jpost("/api/admin/llm/login/poll", { session: this._brainLane }); } catch (e) { return; }
    if (!p || !p.ok) return;
    Settings._laneRenderBrain(p);
    const msg = $("#stBrainMsg");
    if (p.connected === true) {
      clearInterval(this._brainPoll); this._brainPoll = null;
      if (p.usable === false) { if (msg) msg.textContent = "✗ Anmeldung angenommen, aber das Konto kann NICHT antworten" + (p.verify_detail ? " — " + p.verify_detail : "") + ". Bitte neu anmelden (ggf. anderes Konto)."; }
      else if (p.switched) { this.brainDone(p.email); }
      else if (msg) msg.textContent = "⚠️ Kein NEUER Login — verbunden ist weiterhin " + (p.email || "das bisherige Konto") + ". Erst „Konto trennen“, dann neu anmelden.";
    } else if (p.connected === false) {
      clearInterval(this._brainPoll); this._brainPoll = null;
      if (msg) msg.textContent = "✗ Anmeldung nicht abgeschlossen" + (p.verify_detail ? " — " + p.verify_detail : "") + ". Details stehen im Terminal oben.";
    }
  },
  async brainCode() {
    if (!IS_ADMIN) return;
    const ci = $("#stBrainCode"); const msg = $("#stBrainMsg");
    const code = (ci && ci.value || "").trim();
    if (!code) { if (msg) msg.textContent = "Bitte den Code aus dem Anmelde-Fenster einfügen."; return; }
    let r; try { r = await jpost("/api/admin/llm/login/input", { session: this._brainLane, text: code, key: "enter" }); } catch (e) { r = null; }
    if (r && r.ok) { if (ci) ci.value = ""; if (msg) msg.textContent = "Code gesendet — die Antwort erscheint im Terminal."; }
    else if (msg) msg.textContent = "✗ " + ((r && (r.detail || r.msg)) || "Eingabe fehlgeschlagen.");
  },
  brainDone(email) {
    if (this._brainPoll) { clearInterval(this._brainPoll); this._brainPoll = null; }
    const flow = $("#stBrainFlow"); if (flow) flow.hidden = true;
    const s = $("#stBrainStatus"); if (s) s.textContent = "✅ verbunden" + (email ? " als " + email : "");
    try { toast("🧠 Box-Gehirn verbunden" + (email ? " als " + email : "")); } catch (e) {}
  },
  async brainLogout() {
    if (!IS_ADMIN) return;
    if (!confirm("Box-Gehirn wirklich trennen?\n\nDas aktuell verbundene Claude-Konto wird von der Box abgemeldet (Zugangsdaten werden archiviert). Bis ein neues Konto verbunden ist, haben Sessions ohne eigenes Konto KEIN LLM.")) return;
    const msg = $("#stBrainMsg");
    let r; try { r = await jpost("/api/admin/llm/oauth/logout", { id: this.BRAIN_ID }); } catch (e) { r = null; }
    if (r && r.ok) {
      if (this._brainPoll) { clearInterval(this._brainPoll); this._brainPoll = null; }
      const flow = $("#stBrainFlow"); if (flow) flow.hidden = true;
      if (msg) msg.textContent = "";
      try { toast("🧠 Box-Gehirn getrennt — jetzt neu verbinden"); } catch (e) {}
      this.brainStatus();
    } else if (msg) {
      msg.textContent = "\u2717 " + ((r && (r.msg || r.error)) || "Trennen fehlgeschlagen.");
    }
  },
  async brainCancel() {
    if (this._brainPoll) { clearInterval(this._brainPoll); this._brainPoll = null; }
    if (this._brainLane) { try { await jpost("/api/admin/llm/login/cancel", { session: this._brainLane }); } catch (e) {} this._brainLane = null; }
    try { await jpost("/api/admin/llm/oauth/cancel", { id: this.BRAIN_ID }); } catch (e) {}
    const flow = $("#stBrainFlow"); if (flow) flow.hidden = true;
    this.brainStatus();
  },

  _myUrl: "", _myPoll: null,
  async myLlmStatus() {
    const s = $("#myLlmStatus"); if (!s) return;
    let r; try { r = await jget("/api/llm/oauth/status"); } catch (e) { r = null; }
    if (!r || r.ok === false) { s.textContent = "Status nicht abrufbar."; return; }
    if (r.logged_in) s.textContent = "✅ eigenes Konto verbunden" + (r.email ? " als " + r.email : "");
    else s.textContent = "kein eigenes Konto verbunden — du nutzt das zentrale Box-Gehirn.";
    { const lo = $("#myLlmLogout"); if (lo) lo.hidden = !r.logged_in; }
  },
  async myLlmStart() {

    const msg = $("#myLlmMsg"); const flow = $("#myLlmFlow");
    const a = $("#myLlmUrl"); const wait = $("#myLlmUrlWait"); const copy = $("#myLlmCopy");
    if (a) { a.hidden = true; a.href = "#"; } if (copy) copy.hidden = true;
    if (wait) { wait.hidden = false; wait.textContent = "🔄 Anmeldung wird gestartet …"; }
    if (msg) msg.textContent = "";
    if (flow) flow.hidden = false;
    let r; try { r = await jpost("/api/llm/login/start", {}); } catch (e) { r = null; }
    if (!r || !r.ok) {
      if (wait) wait.textContent = "✗ Start fehlgeschlagen.";
      if (msg) msg.textContent = "✗ " + ((r && (r.detail || r.error || r.msg)) || "Anmeldung konnte nicht gestartet werden.");
      return;
    }
    this._laneSid = r.session;
    this._laneRender("myLlm", r);
    if (this._myPoll) clearInterval(this._myPoll);
    this._myPoll = setInterval(async () => {
      let p; try { p = await jpost("/api/llm/login/poll", { session: this._laneSid }); } catch (e) { return; }
      if (!p || !p.ok) return;
      this._laneRender("myLlm", p);
      if (p.connected === true) {
        clearInterval(this._myPoll); this._myPoll = null;
        if (p.usable === false) { if (msg) msg.textContent = "✗ Anmeldung angenommen, aber das Konto kann NICHT antworten" + (p.verify_detail ? " — " + p.verify_detail : "") + ". Bitte neu anmelden (ggf. anderes Konto)."; }
        else if (p.switched) { this.myLlmDone(p.email); }
        else if (msg) msg.textContent = "⚠️ Kein NEUER Login — verbunden ist weiterhin " + (p.email || "das bisherige Konto") + ". Erst „Konto trennen“, dann neu anmelden.";
      } else if (p.connected === false) {
        clearInterval(this._myPoll); this._myPoll = null;
        if (msg) msg.textContent = "✗ Anmeldung nicht abgeschlossen" + (p.verify_detail ? " — " + p.verify_detail : "") + ". Details stehen im Terminal oben.";
      }
    }, 1000);
  },

  _laneRender(prefix, st) {
    const term = $("#" + prefix + "Term");
    if (term) { term.hidden = false; term.textContent = (st.lines || []).join("\n"); term.scrollTop = term.scrollHeight; }
    const a = $("#" + prefix + "Url"); const wait = $("#" + prefix + "UrlWait"); const copy = $("#" + prefix + "Copy");
    if (st.url) {
      if (prefix === "myLlm") this._myUrl = st.url; else this._brainUrl = st.url;
      if (a) { a.href = st.url; a.hidden = false; }
      if (copy) copy.hidden = false;
      if (wait) wait.hidden = true;
      const msg = $("#" + prefix + "Msg");
      if (msg && !msg.textContent) msg.textContent = "Link öffnen (falsches Konto? → Inkognito-Tab), anmelden, Code unten einfügen.";
    } else if (st.running && wait && !wait.hidden) {
      wait.textContent = "🔄 Warte auf den Anmelde-Link …";
    }
  },
  async myLlmCode() {
    const ci = $("#myLlmCode"); const msg = $("#myLlmMsg");
    const code = (ci && ci.value || "").trim();
    if (!code) { if (msg) msg.textContent = "Bitte den Code aus dem Anmelde-Fenster einfügen."; return; }
    let r; try { r = await jpost("/api/llm/login/input", { session: this._laneSid, text: code, key: "enter" }); } catch (e) { r = null; }
    if (r && r.ok) { if (ci) ci.value = ""; if (msg) msg.textContent = "Code gesendet — die Antwort erscheint im Terminal."; }
    else if (msg) msg.textContent = "✗ " + ((r && (r.detail || r.msg)) || "Eingabe fehlgeschlagen.");
  },
  async myLlmLogout() {
    if (!confirm("Eigenes Claude-Konto von der Box trennen? Deine Sessions nutzen danach wieder das zentrale Box-Gehirn.")) return;
    const msg = $("#myLlmMsg");
    let r; try { r = await jpost("/api/llm/oauth/logout", {}); } catch (e) { r = null; }
    if (r && r.ok) {
      if (this._myPoll) { clearInterval(this._myPoll); this._myPoll = null; }
      const flow = $("#myLlmFlow"); if (flow) flow.hidden = true;
      if (msg) msg.textContent = "";
      try { toast("Konto getrennt"); } catch (e) {}
      this.myLlmStatus();
    } else if (msg) {
      msg.textContent = "\u2717 " + ((r && (r.msg || r.error)) || "Trennen fehlgeschlagen.");
    }
  },
  myLlmDone(email) {
    if (this._myPoll) { clearInterval(this._myPoll); this._myPoll = null; }
    const flow = $("#myLlmFlow"); if (flow) flow.hidden = true;
    const s = $("#myLlmStatus"); if (s) s.textContent = "✅ eigenes Konto verbunden" + (email ? " als " + email : "");
    try { toast("🤖 Eigenes LLM-Konto verbunden" + (email ? " als " + email : "")); } catch (e) {}
  },
  async myLlmCancel() {
    if (this._myPoll) { clearInterval(this._myPoll); this._myPoll = null; }
    if (this._laneSid) { try { await jpost("/api/llm/login/cancel", { session: this._laneSid }); } catch (e) {} this._laneSid = null; }
    try { await jpost("/api/llm/oauth/cancel", {}); } catch (e) {}
    const flow = $("#myLlmFlow"); if (flow) flow.hidden = true;
    this.myLlmStatus();
  },

  async loadUpdate() {
    const now = $("#stUpdNow"); if (!now) return;
    let s; try { s = await jget("/api/update/status"); } catch (e) { s = null; }
    const cur = (s && s.current) || "";
    now.textContent = cur ? ("Installierte Version: " + cur) : "Version unbekannt";
    this._updRender(s && s.progress);
  },
  async updateCheck() {
    const msg = $("#stUpdMsg"), apply = $("#stUpdApply");
    if (msg) msg.textContent = "sucht …";
    let r; try { r = await jget("/api/update/check"); } catch (e) { r = null; }
    if (!r || !r.ok) { if (msg) msg.textContent = "✗ " + ((r && r.error) || "Update-Kanal nicht erreichbar"); if (apply) apply.hidden = true; return; }
    const now = $("#stUpdNow"); if (now && r.current) now.textContent = "Installierte Version: " + r.current;
    if (r.available) { if (msg) msg.textContent = "✓ Neue Version: " + (r.version || r.latest) + (r.notes ? (" — " + r.notes) : ""); if (apply) apply.hidden = false; }
    else { if (msg) msg.textContent = "✓ Bereits aktuell."; if (apply) apply.hidden = true; }
  },
  async updateApply() {
    const msg = $("#stUpdMsg"), apply = $("#stUpdApply");
    if (apply) apply.disabled = true; if (msg) msg.textContent = "Installiere …";
    let r; try { r = await jpost("/api/update/apply", {}); } catch (e) { r = null; }
    if (!r || !r.ok) { if (msg) msg.textContent = "✗ " + ((r && r.error) || "Start fehlgeschlagen"); if (apply) apply.disabled = false; return; }
    if (this._updTimer) clearInterval(this._updTimer);
    this._updTimer = setInterval(() => this._updPoll(), 2000); this._updPoll();
  },
  async _updPoll() {
    let s; try { s = await jget("/api/update/status"); } catch (e) { return; }
    if (this._updRender(s && s.progress) && this._updTimer) { clearInterval(this._updTimer); this._updTimer = null; const a = $("#stUpdApply"); if (a) a.disabled = false; }
  },
  _updRender(p) {
    const log = $("#stUpdLog"), msg = $("#stUpdMsg"), rb = $("#stUpdRollback");
    if (!p || !p.state || p.state === "idle") return true;
    if (log) { log.hidden = false; log.textContent = (p.phase || "") + (p.msg ? (" — " + p.msg) : ""); }
    if (p.state === "done") { if (msg) msg.textContent = "✓ Aktualisiert — Portal startet neu."; if (rb) rb.hidden = false; return true; }
    if (p.state === "error") { if (msg) msg.textContent = "✗ " + (p.msg || p.phase || "Fehler"); if (rb) rb.hidden = false; return true; }
    if (msg) msg.textContent = "… " + (p.phase || p.state);
    return false;
  },
  async updateRollback() {
    const msg = $("#stUpdMsg"); if (msg) msg.textContent = "rolle zurück …";
    let r; try { r = await jpost("/api/update/rollback", {}); } catch (e) { r = null; }
    if (msg) msg.textContent = (r && r.ok) ? "✓ Zurückgerollt — Portal startet neu." : ("✗ " + ((r && r.error) || "Fehlgeschlagen"));
  },

  async loadVoice() {
    const st = $("#stVoiceState"); if (!st) return;
    let cat; try { cat = await jget("/api/voice/install/options"); } catch (e) { cat = null; }
    if (cat && cat.ok) this._voiceCatalog(cat);
    this._voicePoll();
  },
  _voiceCatalog(cat) {
    const hw = cat.hardware || {}, lsel = $("#stVoiceLang"), msel = $("#stVoiceModel"), hint = $("#stVoiceModelHint");
    const hwEl = $("#stVoiceHw");
    if (hwEl) hwEl.textContent = "Box: " + (hw.ram_avail_mb || 0) + " MB frei / " + (hw.ram_total_mb || 0) + " MB · " + (hw.cores || "?") + " Kerne" + (hw.gpu ? (" · GPU " + (hw.gpu_name || "")) : " · keine GPU") + " · Empfehlung: " + cat.recommended;
    if (lsel && !lsel.dataset.filled) {
      lsel.textContent = "";
      (cat.languages || []).forEach(l => lsel.appendChild(el("option", { value: l.code, text: l.name + " (" + l.code + ")" })));
      const de = (cat.languages || []).find(l => (l.code || "").indexOf("de") === 0); if (de) lsel.value = de.code;
      lsel.dataset.filled = "1";
    }
    if (msel) {
      msel.textContent = "";
      (cat.models || []).forEach(m => msel.appendChild(el("option", { value: m.name, text: m.name + " (~" + (Math.round((m.approx_ram_mb || 0) / 100) / 10) + " GB)" + (m.recommended ? " — empfohlen" : "") })));
      msel.value = cat.recommended;
    }
    if (hint) hint.textContent = cat.live ? "" : "Offline-Liste — für die neuesten Modelle die Box online bringen.";
  },
  async voiceInstall() {
    const b = $("#stVoiceInstall"), msg = $("#stVoiceMsg");
    if (b) b.disabled = true; if (msg) msg.textContent = "Starte …";
    const model = ($("#stVoiceModel") || {}).value, lang = ($("#stVoiceLang") || {}).value;
    try { await jpost("/api/voice/install", { model: model, lang: lang }); } catch (e) {}
    if (this._voiceTimer) clearInterval(this._voiceTimer);
    this._voiceTimer = setInterval(() => this._voicePoll(), 2500); this._voicePoll();
  },
  async _voicePoll() {
    let s; try { s = await jget("/api/voice/install/status"); } catch (e) { return; }
    if (!s) return;
    const st = $("#stVoiceState"), log = $("#stVoiceLog"), b = $("#stVoiceInstall"), msg = $("#stVoiceMsg");
    if (s.installed && s.state === "done") { if (st) st.textContent = "✓ Installiert."; if (msg) msg.textContent = "Aktiv nach Portal-Neustart."; if (b) { b.disabled = false; b.textContent = "Neu installieren / ändern"; } if (this._voiceTimer) { clearInterval(this._voiceTimer); this._voiceTimer = null; } }
    else if (s.state === "running") { if (st) st.textContent = "Installiere … (lädt Modell + Stimme, kann Minuten dauern)"; if (b) b.disabled = true; }
    else if (s.state === "error") { if (st) st.textContent = "✗ Fehler bei der Installation."; if (b) b.disabled = false; if (this._voiceTimer) { clearInterval(this._voiceTimer); this._voiceTimer = null; } }
    else { if (st) st.textContent = "Nicht installiert."; if (b) b.disabled = false; }
    if (s.log && log) { log.hidden = false; log.textContent = s.log; log.scrollTop = log.scrollHeight; }
  },

  async loadVpn() {
    this.loadVpnProfiles();
    const box = $("#stVpn"); if (!box) return;
    let d; try { d = await jget("/api/vpn"); } catch (e) { d = null; }
    const vpns = (d && d.vpns) || [];
    box.textContent = "";
    if (!vpns.length) { box.appendChild(el("div", { class: "empty", text: "Keine VPN-Zugänge konfiguriert." })); return; }
    vpns.forEach(v => {
      const twofa = (v.type && /cisco|anyconnect|saml/i.test(v.type)) || v.operator_gated;
      const st = v.active ? el("span", { class: "pill on", text: "● aktiv" }) : el("span", { class: "pill", text: "○ " + (v.status || "aus") });
      const rows = [];
      const kv = (k, val) => rows.push(el("div", { class: "stvpn-kv" }, [el("span", { class: "stvpn-k", text: k }), el("span", { class: "stvpn-v", text: val })]));
      if (v.endpoint || v.gateway) kv("Wohin", v.endpoint || v.gateway);
      if (v.type) kv("Typ", v.type);
      if (v.user) kv("Login", v.user);
      if (v.purpose) kv("Zweck", v.purpose);
      kv("Anmeldung", twofa ? "2FA am Handy · interaktiv · nicht gespeichert" : "Zugangsdaten (client-Tresor · JIT-injiziert)");
      const card = el("div", { class: "stvpn-card" }, [
        el("div", { class: "stvpn-head" }, [el("span", { class: "stvpn-name", text: v.name || v.id }), st]),
        el("div", { class: "stvpn-body" }, rows)]);

      if (twofa || v.operator_gated) {
        const act = el("div", { class: "stvpn-act" });
        if (v.active) act.appendChild(el("button", { class: "btn sm ghost", text: "Trennen", onclick: () => this.vpnCancel(v.id) }));
        else {
          act.appendChild(el("button", { class: "btn sm", text: "🔐 Verbinden (2FA am Handy)", onclick: (ev) => this.vpnConnect(v.id, ev.currentTarget) }));

          act.appendChild(el("button", { class: "btn sm ghost", text: "🔄 Neuen 2FA-Link",
            title: "Erzeugt eine frische Anmelde-Sitzung samt neuem 2FA-Link — nutze das, wenn der alte Link „Validation failure\" zeigt.",
            onclick: (ev) => this.vpnNewLink(v.id, ev.currentTarget) }));
        }
        const flow = el("div", { class: "stvpn-flow", id: "vpnFlow-" + v.id }); flow.hidden = true;
        card.appendChild(act); card.appendChild(flow);
      }
      box.appendChild(card);
    });
  },

  async vpnConnect(id, btn) {
    const flow = $("#vpnFlow-" + id);
    if (flow) {
      flow.hidden = false; flow.textContent = "";
      flow.appendChild(el("div", { class: "stvpn-poll", id: "nlWait-" + id, text: "Verbindung wird aufgebaut … 0 s" }));
    }
    let r; try { r = await jpost("/api/vpn/request", { vpn: id }); } catch (e) { r = null; }
    if (!r || !r.ok) {
      if (flow) flow.textContent = "✗ " + ((r && r.error) || "Konnte nicht starten — nur der Owner am LAN-Client darf verbinden.");
      return;
    }
    if (r.state === "connected") { if (flow) flow.hidden = true; toast("✅ " + id + " verbunden"); this.loadVpn(); return; }
    if (r.state === "generating") { this._vpnJobWatch(id, flow, btn, "Verbindung wird aufgebaut", r); return; }
    if (r.stream_cell) {

      if (flow) {
        flow.textContent = "";
        flow.appendChild(el("div", { class: "stvpn-2fa", text: "Login noetig (2FA) - bitte IM gestreamten Anmelde-Fenster anmelden, NICHT in einem eigenen Tab:" }));
        flow.appendChild(el("a", { class: "stvpn-link", href: "/vnc3?cell=" + encodeURIComponent(r.stream_cell), target: "_blank", rel: "noopener", text: "Anmelde-Fenster oeffnen" }));
        flow.appendChild(el("div", { class: "stvpn-poll", text: "warte auf Bestaetigung im Anmelde-Fenster ..." }));
      }
      this._vpnPoll(id, flow);
    } else if (r.state === "auth_pending" && r.auth_url) {
      if (flow) {
        flow.textContent = "";
        flow.appendChild(el("div", { class: "stvpn-2fa", text: "🔐 Diesen Link auf dem Handy öffnen und mit 2FA bestätigen:" }));
        flow.appendChild(el("a", { class: "stvpn-link", href: r.auth_url, target: "_blank", rel: "noopener", text: "Anmelde-Link öffnen" }));
        flow.appendChild(el("button", { class: "btn xs ghost", text: "Link kopieren", onclick: () => { try { navigator.clipboard.writeText(r.auth_url); toast("Link kopiert"); } catch (e) {} } }));
        flow.appendChild(el("div", { class: "stvpn-poll", text: "… warte auf Bestätigung am Handy" }));
      }
      this._vpnPoll(id, flow);
    }
  },

  async vpnNewLink(id, btn) {
    const flow = $("#vpnFlow-" + id);
    if (flow) {
      flow.hidden = false; flow.textContent = "";
      flow.appendChild(el("div", { class: "stvpn-poll", id: "nlWait-" + id, text: "Neue Anmelde-Sitzung wird gebaut … 0 s (Handy bereithalten)" }));
    }
    let r; try { r = await jpost("/api/vpn/newlink", { vpn: id }); } catch (e) { r = null; }
    if (!r || !r.ok) {
      if (flow) flow.textContent = "✗ " + ((r && r.error) || "Konnte nicht starten — nur der Owner am LAN-Client darf das.");
      return;
    }
    this._vpnJobWatch(id, flow, btn, "Neue Anmelde-Sitzung wird gebaut", r);
  },

  _vpnJobWatch(id, flow, btn, what, first) {
    if (this._vpnTimer) { clearInterval(this._vpnTimer); this._vpnTimer = null; }
    if (this._nlTimer) { clearInterval(this._nlTimer); this._nlTimer = null; }
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "… läuft"; }
    const done = () => { if (this._nlTimer) { clearInterval(this._nlTimer); this._nlTimer = null; } if (btn) { btn.disabled = false; btn.textContent = label; } };
    const fail = (msg, cell) => {
      done();
      if (!flow) return;
      flow.textContent = "";
      flow.appendChild(el("div", { class: "stvpn-2fa", text: "✗ " + msg }));
      if (cell) flow.appendChild(el("a", { class: "stvpn-link", href: "/vnc3?cell=" + encodeURIComponent(cell), target: "_blank", rel: "noopener", text: "Anmelde-Fenster öffnen" }));
    };
    const ok = () => { done(); if (flow) flow.hidden = true; toast("✅ " + id + " verbunden"); this.loadVpn(); };

    const show = (url, cell, kind) => {
      done();
      if (!flow) return;
      flow.textContent = "";
      if (kind === "saml") {
        flow.appendChild(el("div", { class: "stvpn-2fa", text: "🔐 Anmeldung nötig — bitte IM Anmelde-Fenster der Box anmelden, NICHT in einem eigenen Tab (der Rückkanal läuft in der Netz-Kammer):" }));
        if (cell) flow.appendChild(el("a", { class: "stvpn-link", href: "/vnc3?cell=" + encodeURIComponent(cell), target: "_blank", rel: "noopener", text: "Anmelde-Fenster öffnen" }));
        flow.appendChild(el("div", { class: "card-hint", text: "Nur den 2FA-Code aufs Handy? Dann „🔄 Neuen 2FA-Link\" — der holt den 2FA-Portal-Link aus der Seite." }));
        flow.appendChild(el("div", { class: "stvpn-poll", text: "… warte auf die Anmeldung — danach steht der Tunnel von selbst" }));
        this._vpnPoll(id, flow);
        return;
      }
      flow.appendChild(el("div", { class: "stvpn-2fa", text: "🔐 Frischer Link — JETZT auf dem Handy öffnen und bestätigen (er altert mit der Anmelde-Sitzung):" }));
      flow.appendChild(el("a", { class: "stvpn-link", href: url, target: "_blank", rel: "noopener", text: url }));
      flow.appendChild(el("button", { class: "btn xs ghost", text: "Link kopieren", onclick: () => { try { navigator.clipboard.writeText(url); toast("Link kopiert"); } catch (e) {} } }));
      if (cell) flow.appendChild(el("a", { class: "stvpn-link", href: "/vnc3?cell=" + encodeURIComponent(cell), target: "_blank", rel: "noopener", text: "Anmelde-Fenster öffnen" }));
      flow.appendChild(el("div", { class: "stvpn-poll", text: "… warte auf Bestätigung am Handy — danach steht der Tunnel von selbst" }));
      this._vpnPoll(id, flow);
    };
    if (first && first.state === "connected") { ok(); return; }
    if (first && first.auth_url) { show(first.auth_url, first.stream_cell, first.link_kind); return; }
    let s = (first && first.elapsed) || 0;
    let phase = (first && first.phase) || "";
    this._nlTimer = setInterval(async () => {
      s += 2;
      const w = $("#nlWait-" + id);
      if (w) w.textContent = what + " … " + s + " s" + (phase ? " — " + phase : "") + " (Handy bereithalten)";
      let d; try { d = await jget("/api/vpn/job?vpn=" + encodeURIComponent(id)); } catch (e) { return; }
      if (!d || !d.ok) return;
      if (d.phase) phase = d.phase;
      if (d.auth_url) { show(d.auth_url, d.stream_cell, d.link_kind); return; }
      if (d.state === "connected") { ok(); return; }
      if (d.state === "error") { fail(d.error || "Es kam kein 2FA-Link zurück.", d.stream_cell); return; }

      if (s > 900) fail("Zeitüberschreitung — bitte „Neuen 2FA-Link\" versuchen.", d.stream_cell);
    }, 2000);
  },
  _vpnPoll(id, flow) {
    if (this._vpnTimer) clearInterval(this._vpnTimer);
    let n = 0;
    this._vpnTimer = setInterval(async () => {
      n++;
      let d; try { d = await jget("/api/vpn"); } catch (e) { return; }
      const v = (d && d.vpns || []).find(x => x.id === id);
      if (v && v.active) { clearInterval(this._vpnTimer); this._vpnTimer = null; toast("✅ " + id + " verbunden"); this.loadVpn(); }

      else if (n > 300) { clearInterval(this._vpnTimer); this._vpnTimer = null; const p = flow && flow.querySelector(".stvpn-poll"); if (p) p.textContent = "noch nicht bestätigt — Link erneut öffnen oder neu starten."; }
    }, 3000);
  },
  async vpnCancel(id) {
    if (this._vpnTimer) { clearInterval(this._vpnTimer); this._vpnTimer = null; }
    try { await jpost("/api/vpn/cancel", { vpn: id }); } catch (e) {}
    toast("Trennen angefordert"); setTimeout(() => this.loadVpn(), 800);
  },

  async loadVpnProfiles() {
    const host = $("#stVpnMine"); if (!host) return;
    let d; try { d = await jget("/api/vpn/profiles"); } catch (e) { d = null; }
    host.textContent = "";
    host.appendChild(el("div", { class: "stvpn-mine-head" }, [el("b", { text: "Meine VPN-Verbindungen" })]));
    host.appendChild(el("div", { class: "card-hint", text: "Profile laufen in einer eigenen Netz-Kammer pro Nutzer; Sessions können sie unter Ausstattung wählen." }));
    const profs = (d && d.ok && d.profiles) || [];
    if (!profs.length) host.appendChild(el("div", { class: "empty", text: "Noch keine eigenen VPN-Verbindungen." }));
    else profs.forEach(p => host.appendChild(this.vpnProfileRow(p)));
    host.appendChild(this.vpnProfileAdd());
  },
  vpnProfileRow(p) {

    const TYPES = { wireguard: "WireGuard", openvpn: "OpenVPN", openconnect: "AnyConnect-kompatibel" };
    const st = (p.status && typeof p.status === "object") ? p.status : {};
    const connected = !!(st.connected || p.connected || p.status === "connected");
    const box = el("div", { class: "prov-row col" });
    box.appendChild(el("div", { class: "prov-head" }, [
      el("b", { text: p.name || p.id }),
      el("span", { class: "pill", text: TYPES[p.type] || p.type || "?" }),
      el("span", { class: connected ? "pill on" : "pill", text: connected ? "● verbunden" : "○ getrennt" }),
      p.shared ? el("span", { class: "muted", text: "geteilt" }) : null,
      p.gateway ? el("span", { class: "muted", text: p.gateway }) : null]));
    const status = el("span", { class: "muted", text: "" });
    const askPw = !(p.auth && p.auth.mode === "saved");
    const needOtp = !!(p.auth && p.auth.otp);
    let pending = null;
    const pw = el("input", { type: "password", placeholder: "Passwort", autocomplete: "off",
      onkeydown: (e) => { if (e.key === "Enter" && pending) doRun(pending); } });
    const otp = el("input", { placeholder: "2FA-Code (OTP)", autocomplete: "off", spellcheck: "false",
      onkeydown: (e) => { if (e.key === "Enter" && pending) doRun(pending); } });
    const authRow = el("div", { class: "prov-form", hidden: true }, needOtp ? [pw, otp] : [pw]);
    const doRun = async (action) => {

      if (askPw && authRow.hidden) {
        authRow.hidden = false; pending = action;
        status.textContent = "Zugangsdaten eingeben, dann erneut klicken (oder Enter).";
        try { pw.focus(); } catch (e) {}
        return;
      }
      const body = { id: p.id };
      if (askPw) { if (pw.value) body.password = pw.value; if (needOtp && otp.value) body.otp = otp.value; }
      status.textContent = action === "test" ? "teste …" : "verbinde …";
      let r; try { r = await jpost("/api/vpn/profiles/" + action, body); } catch (e) { r = null; }
      pw.value = ""; otp.value = ""; pending = null;
      if (askPw) authRow.hidden = true;
      if (action === "test") {
        if (r && r.ok) status.textContent = "Egress-IP: " + (r.egress_ip || "?") + (r.seconds != null ? " · " + r.seconds + "s" : "");
        else status.textContent = "✗ " + ((r && r.error) || "Test fehlgeschlagen");
      } else if (r && r.ok) { toast("VPN verbunden: " + (p.name || p.id)); this.loadVpnProfiles(); }
      else status.textContent = "✗ " + ((r && r.error) || "Verbinden fehlgeschlagen");
    };
    const disconnect = async () => {
      status.textContent = "trenne …";
      let r; try { r = await jpost("/api/vpn/profiles/disconnect", { id: p.id }); } catch (e) { r = null; }
      if (r && r.ok) { toast("VPN getrennt: " + (p.name || p.id)); this.loadVpnProfiles(); }
      else status.textContent = "✗ " + ((r && r.error) || "Trennen fehlgeschlagen");
    };
    const del = async () => {
      if (!confirm("VPN-Verbindung \"" + (p.name || p.id) + "\" wirklich löschen?")) return;
      let r; try { r = await jpost("/api/vpn/profiles/delete", { id: p.id }); } catch (e) { r = null; }
      if (r && r.ok) { toast("VPN-Verbindung gelöscht"); this.loadVpnProfiles(); }
      else status.textContent = "✗ " + ((r && r.error) || "Löschen fehlgeschlagen");
    };
    box.appendChild(el("div", { class: "prov-form" }, [
      el("button", { class: "btn sm ghost", text: "Testen", onclick: () => doRun("test") }),
      connected ? el("button", { class: "btn sm", text: "Trennen", onclick: disconnect })
                : el("button", { class: "btn sm", text: "Verbinden", onclick: () => doRun("connect") }),
      el("button", { class: "btn sm ghost", text: "Löschen", onclick: del }),
      status]));
    box.appendChild(authRow);
    return box;
  },
  vpnProfileAdd() {

    const add = el("details", { class: "prov-add" });
    add.appendChild(el("summary", { text: "＋ Eigene VPN-Verbindung" }));
    const msg = el("div", { class: "muted", text: "" });
    const tySel = el("select", { title: "VPN-Typ" }, [
      el("option", { value: "wireguard", text: "WireGuard" }),
      el("option", { value: "openvpn", text: "OpenVPN" }),
      el("option", { value: "openconnect", text: "AnyConnect-kompatibel (openconnect)" })]);
    const nName = el("input", { placeholder: "Name (z. B. Uni-VPN)", spellcheck: "false" });
    const nFile = el("input", { type: "file", title: "Konfigurationsdatei (.conf / .ovpn)" });
    const fileRow = el("div", { class: "prov-form" }, [nFile]);
    const nGw = el("input", { placeholder: "Gateway-URL (https://vpn.example.org)", spellcheck: "false" });
    const nProto = el("select", { title: "openconnect-Protokoll" },
      ["anyconnect", "gp", "pulse", "nc", "f5", "fortinet", "array"].map(x => el("option", { value: x, text: x })));
    const nUser = el("input", { placeholder: "Benutzername", spellcheck: "false", autocomplete: "off" });
    const nOtp = el("input", { type: "checkbox" });
    const ocRow1 = el("div", { class: "prov-form", hidden: true }, [nGw, nProto]);
    const ocRow2 = el("div", { class: "prov-form", hidden: true }, [nUser, el("label", {}, [nOtp, " 2FA-Code (OTP) beim Verbinden"])]);
    const syncType = () => { const oc = tySel.value === "openconnect"; fileRow.hidden = oc; ocRow1.hidden = !oc; ocRow2.hidden = !oc; };
    tySel.addEventListener("change", syncType);
    const askR = el("input", { type: "radio", name: "stVpnAuthMode", checked: true });
    const savR = el("input", { type: "radio", name: "stVpnAuthMode" });
    const nPw = el("input", { type: "password", placeholder: "Passwort", autocomplete: "off" });
    const pwRow = el("div", { class: "prov-form", hidden: true }, [nPw]);
    const syncAuth = () => { pwRow.hidden = !savR.checked; };
    askR.addEventListener("change", syncAuth); savR.addEventListener("change", syncAuth);
    const save = async () => {
      const type = tySel.value;
      const body = { name: (nName.value || "").trim(), type: type, auth_mode: savR.checked ? "saved" : "ask" };
      if (!body.name) { msg.textContent = "Bitte einen Namen angeben."; return; }
      if (type === "openconnect") {
        body.gateway = (nGw.value || "").trim();
        body.protocol = nProto.value;
        body.user = (nUser.value || "").trim();
        body.otp = !!nOtp.checked;
        if (!body.gateway) { msg.textContent = "Bitte die Gateway-URL angeben."; return; }
      } else {
        const f = nFile.files && nFile.files[0];
        if (!f) { msg.textContent = "Bitte eine Konfigurationsdatei auswählen (.conf / .ovpn)."; return; }
        try {
          body.config_b64 = await new Promise((res, rej) => {
            const fr = new FileReader();
            fr.onload = () => { const s = String(fr.result || ""); res(s.slice(s.indexOf(",") + 1)); };
            fr.onerror = () => rej(new Error("Datei konnte nicht gelesen werden"));
            fr.readAsDataURL(f);
          });
        } catch (e) { msg.textContent = "✗ " + e.message; return; }
      }
      if (body.auth_mode === "saved" && nPw.value) body.password = nPw.value;
      msg.textContent = "speichere …";
      let r; try { r = await jpost("/api/vpn/profiles", body); } catch (e) { r = null; }
      if (r && r.ok) { nPw.value = ""; nName.value = ""; msg.textContent = ""; toast("VPN-Verbindung angelegt"); this.loadVpnProfiles(); }
      else msg.textContent = "✗ " + ((r && r.error) || "Anlegen fehlgeschlagen");
    };
    add.appendChild(el("div", { class: "prov-form" }, [tySel, nName]));
    add.appendChild(fileRow); add.appendChild(ocRow1); add.appendChild(ocRow2);
    add.appendChild(el("div", { class: "prov-form" }, [
      el("label", {}, [askR, " bei jedem Verbinden fragen"]),
      el("label", {}, [savR, " auf der Box speichern"])]));
    add.appendChild(pwRow);
    add.appendChild(el("div", { class: "prov-form" }, [el("button", { class: "btn sm", text: "Speichern", onclick: save }), msg]));
    return add;
  }
};

const Screen = {
  started: false, lane: "vnc", W: 960, H: 600, apps: null, trackpad: false, target: "", targets: {},
  init() {
    this.img = $("#scImg"); this.canvas = $("#scCanvas"); this.frame = $("#scFrame");
    this.stage = $("#scStage"); this.hint = $("#scHint");
    $("#scStop").addEventListener("click", () => this.stop());
    { const ct = $("#scCastTarget"); if (ct) ct.addEventListener("change", () => { const v = ct.value; ct.value = ""; if (v) this.mirror(v); }); }
    { const cs = $("#scCastStop"); if (cs) cs.addEventListener("click", () => this.stopCast()); }
    this.initRemote();
    $("#scFfx").addEventListener("click", () => { jpost("/api/screen/firefox-restart"); toast("Firefox wird neu gestartet …"); });
    $("#scVnc").setAttribute("href", "/vnc3");
    { const ts = $("#scTarget"); if (ts) ts.addEventListener("change", () => this.setTarget(ts.value)); }
    this.loadTargets();
    $$("#laneSeg button").forEach(b => b.addEventListener("click", () => this.setLane(b.getAttribute("data-lane"))));
    $("#scTrackpad").addEventListener("click", () => this.toggleTrackpad());
    $$("#lens-screen [data-app]").forEach(b => b.addEventListener("click", () => this.launch(b.getAttribute("data-app"))));

    $("#scApps").addEventListener("click", () => this.toggleDrawer());
    $("#appsClose").addEventListener("click", () => $("#appsDrawer").hidden = true);
    $("#appsQ").addEventListener("input", () => this.renderApps($("#appsQ").value));

    this.bindEl(this.img); this.bindEl(this.canvas);

    this.initLocalType();

    this.initTrackpad();
  },
  show() { if (!this.started) this.start(); setTimeout(() => { const f = $("#scLocal"); if (f) f.focus(); }, 120); this.loadTargets(); this.loadCastTargets(); },

  async loadCastTargets() {
    let d; try { d = await jget("/api/cast/targets"); } catch (e) { return; }
    this.castTargets = (d && d.devices) || [];
    const sel = $("#scCastTarget");
    if (sel) { const cur = sel.value;
      sel.innerHTML = '<option value="">📺 Auf Gerät spiegeln…</option>' +
        this.castTargets.map(x => `<option value="${esc(x.id || x.addr)}">📺 ${esc(x.name || x.addr)}</option>`).join("");
      sel.value = cur; }
    const live = ((d && d.casts) || []).some(c => c.alive);
    const stop = $("#scCastStop"); if (stop) stop.hidden = !live;
  },
  async mirror(deviceId) {
    if (!deviceId) return;
    const dev = (this.castTargets || []).find(d => d.id === deviceId || d.addr === deviceId);
    const tgtLabel = (dev && dev.name) || deviceId;
    const meta = this.targets && this.targets[this.target];
    let source, srcLabel;
    if (meta && meta.kind === "session") {

      if (!confirm("„" + (meta.name || this.target) + "“ ist ein headless Terminal-Agent — er hat keinen grafischen Bildschirm zum Spiegeln.\n(Sein „Bildschirm“ ist das Terminal im Reiter Sessions.)\n\nStattdessen den ganzen Box-Bildschirm auf „" + tgtLabel + "“ spiegeln?")) return;
      source = { type: "portal" }; srcLabel = "Box-Bildschirm";
    } else {
      source = this.target ? { type: "cell", id: this.target } : { type: "seat" };
      srcLabel = this.target ? ((meta && meta.name) || this.target) : "Box-Bildschirm";
      if (!confirm("Auf Gerät spiegeln?\n\nQuelle:  " + srcLabel + "\nZiel:      " + tgtLabel + "\n\nDer Bildschirm wird auf dem Gerät angezeigt (Stopp jederzeit über ⏹ Cast).")) return;
    }
    const r = await jpost("/api/cast/start", { device: deviceId, source });
    if (r && r.ok) { toast("📺 Spiegeln (" + srcLabel + ") → " + tgtLabel); this.loadCastTargets(); }
    else toast("Spiegeln fehlgeschlagen: " + ((r && r.error) || "?"));
  },
  async stopCast() { await jpost("/api/cast/stop", {}); toast("Spiegeln beendet."); this.loadCastTargets(); },
  initRemote() {
    const open = $("#scRemote"), dlg = $("#remoteDlg");
    if (!open || !dlg) return;
    open.addEventListener("click", () => this.openRemote());
    const cl = $("#remoteClose"); if (cl) cl.addEventListener("click", () => { dlg.hidden = true; });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.hidden = true; });
    const rs = $("#riStart"); if (rs) rs.addEventListener("click", () => this.startIngest());
    const rc = $("#riCopy"); if (rc) rc.addEventListener("click", () => { const t = $("#riCmd"); if (t) { t.select(); document.execCommand("copy"); toast("Befehl kopiert"); } });
    const ri = $("#riStop"); if (ri) ri.addEventListener("click", () => this.stopIngest());
    const ra = $("#raRefresh"); if (ra) ra.addEventListener("click", () => this.loadAgents());
  },
  async openRemote() {
    const dlg = $("#remoteDlg"); if (!dlg) return; dlg.hidden = false;
    await this.loadCastTargets();
    const sel = $("#riDevice");
    if (sel) sel.innerHTML = '<option value="">Ziel-Fernseher…</option>' +
      (this.castTargets || []).map(x => `<option value="${esc(x.id || x.addr)}">${esc(x.name || x.addr)}</option>`).join("");
    this.loadAgents();
  },
  async loadAgents() {
    const box = $("#raList"); if (!box) return;
    let d; try { d = await jget("/api/devinput/agents"); } catch (e) { box.innerHTML = '<span class="remote-hint">Fehler</span>'; return; }
    const ags = (d && d.agents) || [];
    box.innerHTML = ags.length
      ? ags.map(a => `<span class="remote-agent"><span class="dot"></span>${esc(a)} <button class="btn sm" data-testag="${esc(a)}">Testklick</button></span>`).join("")
      : '<span class="remote-hint">Kein Eingabe-Agent verbunden. Auf dem Gerät: <code>python win_input_agent.py --box wss://' + esc(location.host) + '/ --agent NAME --key KEY --insecure</code></span>';
    box.querySelectorAll("[data-testag]").forEach(b => b.addEventListener("click", () => this.testAgent(b.getAttribute("data-testag"))));
  },
  async testAgent(name) {
    const r = await jpost("/api/devinput/send", { agent: name, events: [{ t: "moverel", dx: 8, dy: 0 }, { t: "moverel", dx: -8, dy: 0 }] });
    toast(r && r.ok ? ("Testklick → " + name) : ("Fehlgeschlagen: " + ((r && r.error) || "?")));
  },
  async stopIngest() { const r = await jpost("/api/cast/ingest/stop", {}); toast(r && r.ok ? ("Ingest gestoppt (" + (r.stopped || 0) + ")") : "Fehler"); const o = $("#riOut"); if (o) o.hidden = true; },
  async startIngest() {
    const dev = ($("#riDevice") || {}).value, name = (($("#riName") || {}).value || "Laptop");
    if (!dev) { toast("Bitte einen Fernseher wählen."); return; }
    const btn = $("#riStart");
    if (btn) { if (btn.disabled) return; btn.disabled = true; }
    try {
      const r = await jpost("/api/cast/ingest/start", { device: dev, name });
      if (r && r.ok && r.push_cmd) {
        const out = $("#riOut"), cmd = $("#riCmd"); if (out) out.hidden = false; if (cmd) cmd.value = r.push_cmd;
        toast("Ingest bereit — Befehl auf dem Laptop ausführen.");
      } else toast("Ingest fehlgeschlagen: " + ((r && r.error) || "?"));
    } finally {
      if (btn) btn.disabled = false;
    }
  },

  async loadTargets() {
    const sel = $("#scTarget"); if (!sel) return;
    let cells = [];
    try { const d = await jget("/api/vmcells"); cells = (d && d.cells) || []; } catch (e) {}
    this.targets = {}; cells.forEach(c => { this.targets[c.id] = c; });
    const cur = sel.value;
    const opt = c => `<option value="${encodeURIComponent(c.id)}">${c.kind === "session" ? "🖳" : "🖥"} ${esc(c.name || c.id)}${c.kind === "session" ? " · Terminal" : ""}</option>`;
    sel.innerHTML = '<option value="">🖥 Box-Browser (geteilt)</option>' + cells.map(opt).join("");
    sel.value = cur;
  },
  async selectTarget(sid) {
    await this.loadTargets();
    const meta = this.targets && this.targets[sid], sel = $("#scTarget");
    if (meta) { if (sel) sel.value = sid; this.setTarget(sid); return; }
    if (sel) sel.value = "";
    this.target = ""; this.frame.hidden = true; this.frame.removeAttribute("src");
    this.img.hidden = true; this.canvas.hidden = true; this.hint.hidden = false;
    this.hint.textContent = "Diese Session hat gerade keinen grafischen Screen (Terminal-Agent bzw. nicht warm). Ihr Terminal siehst du im Reiter Sessions.";
    const s2 = $("#scStatus"); if (s2) s2.textContent = "";
  },
  vncSrc() { return this.target ? ("/vnc3?cell=" + this.target) : "/vnc3"; },
  setTarget(t) {
    this.target = t || "";
    const sel = $("#scTarget"), meta = this.targets && this.targets[this.target];
    $("#scVnc").setAttribute("href", this.vncSrc());
    if (meta && meta.kind === "session") {
      this.frame.hidden = true; this.frame.removeAttribute("src");
      this.img.hidden = true; this.img.removeAttribute("src"); this.canvas.hidden = true;
      this.hint.hidden = false;
      this.hint.textContent = "Diese Session läuft headless (Terminal-Agent) — ihr Bildschirm ist das Terminal. ";
      this.hint.appendChild(el("button", { class: "btn sm primary", text: "▟ Terminal öffnen", onclick: () => openSessionTerminal(meta.id) }));
      const s2 = $("#scStatus"); if (s2) s2.textContent = "Session (Hintergrund): " + (meta.name || this.target);
      return;
    }
    const cell = !!this.target;
    $$("#laneSeg button").forEach(b => { b.disabled = cell && b.getAttribute("data-lane") === "mjpeg"; });
    this.lane = "vnc"; this.frame.src = this.vncSrc(); this.frame.hidden = false;
    this.img.hidden = true; this.img.removeAttribute("src"); this.canvas.hidden = true; this.hint.hidden = true;
    const s = $("#scStatus");
    if (s) s.textContent = cell
      ? ("zeigt Session: " + ((sel && sel.selectedOptions[0]) ? sel.selectedOptions[0].textContent.replace(/^🖥\s*/, "") : this.target))
      : "zeigt den geteilten Box-Browser (keiner Session zugeordnet)";
  },

  gatherCaps() {
    let gl = false; try { gl = !!document.createElement("canvas").getContext("webgl2"); } catch (e) {}
    return { webgl2: gl ? 1 : 0, webgpu: navigator.gpu ? 1 : 0, cores: navigator.hardwareConcurrency || 4,
      mem_mb: (navigator.deviceMemory || 4) * 1024, hw_decode: window.VideoDecoder ? 1 : 0, native: 0, throttled: document.hidden ? 1 : 0 };
  },
  capsQuery() { const c = this.gatherCaps(); return Object.keys(c).map(k => k + "=" + encodeURIComponent(c[k])).join("&"); },

  async start() {
    const r = await jpost("/api/screen/start");
    if (!(r && r.ok)) { this.hint.textContent = "Bildschirm-Start fehlgeschlagen: " + ((r && r.error) || "?"); this.hint.hidden = false; return; }
    this.started = true;
    const pl = await jget("/api/screen/placement?" + this.capsQuery());
    if (pl && pl.screen) { this.W = pl.screen.w || this.W; this.H = pl.screen.h || this.H; }
    this.openLane(this.lane);

    jpost("/api/screen/launch", { prog: "BROWSER" });
  },
  openLane(lane) {
    this.lane = lane; this.hint.hidden = true;
    $$("#laneSeg button").forEach(b => b.classList.toggle("on", b.getAttribute("data-lane") === lane));
    if (lane === "mjpeg") {
      this.frame.hidden = true; this.frame.removeAttribute("src"); this.canvas.hidden = true;
      this.img.hidden = false; this.img.src = "/screen/stream?token=" + encodeURIComponent(TOKEN) + "&t=" + Date.now();
    } else {
      this.img.hidden = true; this.img.removeAttribute("src"); this.canvas.hidden = true;
      const src = this.vncSrc(); if (this.frame.getAttribute("src") !== src) this.frame.src = src; this.frame.hidden = false;
    }
  },
  setLane(lane) { if (!this.started) { this.lane = lane; $$("#laneSeg button").forEach(b => b.classList.toggle("on", b.getAttribute("data-lane") === lane)); return; } this.openLane(lane); },
  async stop() {
    await jpost("/api/screen/stop"); this.started = false;
    this.frame.hidden = true; this.frame.removeAttribute("src"); this.img.hidden = true; this.img.removeAttribute("src"); this.canvas.hidden = true;
    this.hint.hidden = false; this.hint.textContent = "Bildschirm gestoppt. Screen erneut öffnen zum Starten.";
  },
  async launch(prog) {
    if (!this.started) { await this.start(); setTimeout(() => jpost("/api/screen/launch", { prog }), 600); }
    else jpost("/api/screen/launch", { prog });
  },

  coords(e, node) {
    const rc = node.getBoundingClientRect();
    const nw = node === this.img ? (this.img.naturalWidth || this.W) : this.W;
    const nh = node === this.img ? (this.img.naturalHeight || this.H) : this.H;
    const x = Math.round((e.clientX - rc.left) * nw / (rc.width || 1));
    const y = Math.round((e.clientY - rc.top) * nh / (rc.height || 1));
    return { x: Math.max(0, Math.min(nw - 1, x)), y: Math.max(0, Math.min(nh - 1, y)) };
  },
  reflex(x, y) { const d = el("div", { class: "click-reflex" }); d.style.left = x + "px"; d.style.top = y + "px"; document.body.appendChild(d); setTimeout(() => d.remove(), 460); },
  bindEl(node) {
    node.addEventListener("click", (e) => { this.reflex(e.clientX, e.clientY); const c = this.coords(e, node); jpost("/api/screen/input", { action: "click", x: c.x, y: c.y }); const f = $("#scLocal"); if (f) f.focus(); });
    node.addEventListener("contextmenu", (e) => { e.preventDefault(); const c = this.coords(e, node); jpost("/api/screen/input", { action: "click", x: c.x, y: c.y, btn: "right" }); });
    node.addEventListener("wheel", (e) => { e.preventDefault(); const c = this.coords(e, node); jpost("/api/screen/input", { action: "scroll", x: c.x, y: c.y, n: e.deltaY < 0 ? 1 : -1 }); }, { passive: false });
    node.addEventListener("keydown", (e) => this.onKey(e));
  },
  onKey(e) {
    const k = e.key; if (k == null || e.repeat) return; const f = $("#scLocal"); if (!f) return;
    if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); f.focus(); f.value += k; }
    else if (k === "Enter" || k === "Backspace" || k === "Tab" || k === " ") { e.preventDefault(); f.focus(); }
  },

  initLocalType() {
    const f = $("#scLocal");
    const setStatus = (m, ok) => { const s = $("#scStatus"); if (!s) return; s.textContent = m || ""; s.style.color = ok === true ? "var(--ok)" : (ok === false ? "var(--danger)" : "var(--text-muted)"); };
    const commit = async (withEnter) => {
      const v = f.value.trim();
      if (withEnter) {
        if (!v) return setStatus("leer — nichts zu öffnen", false);
        setStatus("→ öffne " + v + " …", null);
        const r = await jpost("/api/browser/navigate", { url: v });
        if (r && r.ok) { setStatus("✓ geöffnet: " + (r.url || v), true); f.value = ""; f.focus(); }
        else setStatus("✗ Fehler: " + ((r && r.error) || "keine Antwort"), false);
        return;
      }
      if (!v) return setStatus("leer — nichts zu tippen", false);
      setStatus("→ tippe in Feld …", null);
      await jpost("/api/screen/input", { action: "text", text: v }); setStatus("✓ getippt: " + v, true); f.value = ""; f.focus();
    };
    f.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); commit(true); } });
    $("#scGo").addEventListener("click", () => commit(true));
    $("#scType").addEventListener("click", () => commit(false));
    $("#scAddr").addEventListener("click", () => { jpost("/api/screen/input", { action: "keycode", code: 38, mod: 4 }).then(() => f.focus()); });
  },

  toggleDrawer() { const d = $("#appsDrawer"); if (d.hidden) { d.hidden = false; this.loadApps(); if ($("#appsQ")) $("#appsQ").focus(); } else d.hidden = true; },
  async loadApps() { if (this.apps) return this.renderApps(""); const d = await jget("/api/screen/apps"); this.apps = (d && d.apps) || []; this.renderApps($("#appsQ").value); },
  renderApps(filter) {
    const grid = $("#appsGrid"); const f = (filter || "").toLowerCase().trim();
    const list = (this.apps || []).filter(a => !f || a.name.toLowerCase().indexOf(f) >= 0 || (a.comment || "").toLowerCase().indexOf(f) >= 0);
    grid.textContent = "";
    if (!list.length) { grid.appendChild(el("div", { class: "empty", text: this.apps ? "Keine Treffer." : "Lade Apps …" })); return; }
    const hue = (s) => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h % 360; };
    list.forEach(a => {
      const ic = el("div", { class: "ai", text: ((a.name || "?").trim()[0] || "?").toUpperCase() }); ic.style.background = "hsl(" + hue(a.name) + ",55%,62%)";
      const card = el("div", { class: "acard", title: a.comment || a.name }, [ic, el("div", { class: "an", text: a.name })]);
      card.addEventListener("click", () => { this.spawn(a.exec); $("#appsDrawer").hidden = true; });
      grid.appendChild(card);
    });
  },
  async spawn(exec) { const was = this.started; if (!this.started) await this.start(); setTimeout(() => jpost("/api/screen/spawn", { exec }), was ? 0 : 400); },

  toggleTrackpad() { this.trackpad = !this.trackpad; $("#tpOverlay").classList.toggle("on", this.trackpad); $("#scTrackpad").classList.toggle("primary", this.trackpad); if (this.trackpad && !this.started) this.start(); },
  mediaRect() { const m = !this.img.hidden ? this.img : (!this.canvas.hidden ? this.canvas : this.frame); return m.getBoundingClientRect(); },
  seatCoords(cx, cy) { const rc = this.mediaRect(); const x = Math.round((cx - rc.left) * this.W / (rc.width || 1)); const y = Math.round((cy - rc.top) * this.H / (rc.height || 1)); return { x: Math.max(0, Math.min(this.W - 1, x)), y: Math.max(0, Math.min(this.H - 1, y)) }; },
  initTrackpad() {
    const ov = $("#tpOverlay"), cur = $("#tpCursor");
    let cx = 0, cy = 0, last = null, moved = 0, startT = 0, twoFinger = false, scrollAcc = 0;
    const place = () => { const rc = this.stage.getBoundingClientRect(); cur.style.left = (cx - rc.left) + "px"; cur.style.top = (cy - rc.top) + "px"; };
    const initCursor = () => { const rc = this.stage.getBoundingClientRect(); if (!cx) { cx = rc.left + rc.width / 2; cy = rc.top + rc.height / 2; } place(); };
    ov.addEventListener("touchstart", (e) => { initCursor(); last = e.touches[0]; moved = 0; startT = Date.now(); twoFinger = e.touches.length >= 2; scrollAcc = 0; }, { passive: false });
    ov.addEventListener("touchmove", (e) => {
      e.preventDefault();
      if (e.touches.length >= 2) {
        twoFinger = true; const dy = e.touches[0].clientY - last.clientY; scrollAcc += dy; last = e.touches[0];
        if (Math.abs(scrollAcc) > 26) { const c = this.seatCoords(cx, cy); jpost("/api/screen/input", { action: "scroll", x: c.x, y: c.y, n: scrollAcc < 0 ? 1 : -1 }); scrollAcc = 0; }
        return;
      }
      const t = e.touches[0]; const dx = t.clientX - last.clientX, dy = t.clientY - last.clientY; last = t; moved += Math.abs(dx) + Math.abs(dy);
      const rc = this.stage.getBoundingClientRect();
      cx = Math.max(rc.left, Math.min(rc.right, cx + dx * 1.6)); cy = Math.max(rc.top, Math.min(rc.bottom, cy + dy * 1.6)); place();
    }, { passive: false });
    ov.addEventListener("touchend", (e) => {
      const tap = moved < 10 && (Date.now() - startT) < 300;
      if (tap) { const c = this.seatCoords(cx, cy); this.reflex(cx, cy); jpost("/api/screen/input", twoFinger ? { action: "click", x: c.x, y: c.y, btn: "right" } : { action: "click", x: c.x, y: c.y }); }
      twoFinger = false;
    });
  }
};

const Console = {
  term: null, fit: null, ws: null, target: "cockpit", ready: false, connId: 0, sid: null,
  init() {
    $$("#consoleSeg button").forEach(b => b.addEventListener("click", () => this.setTarget(b.getAttribute("data-target"))));

    { const nb = $("#sessNew"); if (nb) nb.addEventListener("click", () => this.newSession()); const hb = $("#sessHist"); if (hb) hb.addEventListener("click", () => this.toggleHistory()); }
    window.addEventListener("resize", () => { if (this.fit && Router.cur === "console") { this.fit.fit(); this.sendResize(); } });
  },

  show(sub) { const tgt = "cockpit"; if (!this.ensure()) return; if (tgt !== this.target || !this.ws) this.setTarget(tgt); setTimeout(() => { if (this.fit) { this.fit.fit(); this.sendResize(); } this.term.focus(); }, 60); },
  ensure() {
    if (this.ready) return true;
    if (typeof Terminal === "undefined") { $("#consoleState").textContent = "xterm nicht geladen"; return false; }
    this.term = new Terminal({ fontSize: 13, fontFamily: "'JetBrains Mono', ui-monospace, monospace", cursorBlink: true });
    this.fit = new FitAddon.FitAddon(); this.term.loadAddon(this.fit); this.term.open($("#term")); this.applyTermTheme(); this.fit.fit();
    this.term.onData(d => { if (this.ws && this.ws.readyState === 1) this.ws.send(d); });
    this.ready = true; return true;
  },
  applyTermTheme() {
    if (!this.term) return;
    const cs = getComputedStyle(document.body);
    const theme = { background: cs.backgroundColor, foreground: cs.color, cursor: "#8a7fff", selectionBackground: "rgba(138,127,255,.35)" };
    try { if (typeof this.term.setOption === "function") this.term.setOption("theme", theme); else this.term.options.theme = theme; } catch (e) {}
  },
  setTarget(t) {
    if (!this.ensure()) return;

    this.target = "cockpit";
    $$("#consoleSeg button").forEach(b => b.classList.toggle("on", b.getAttribute("data-target") === this.target));
    this.ensureSession().then(() => { this._renderSessName(); this.connect(); });
  },
  connect() {
    const myId = ++this.connId;
    if (this.ws) { try { this.ws.onclose = null; this.ws.close(); } catch (e) {} }
    if (this.term) this.term.reset();
    $("#consoleState").textContent = "verbinde…";
    const _q = "/ws/term?target=" + this.target + ((this.target === "cockpit" && this.sid) ? "&sid=" + encodeURIComponent(this.sid) : "");
    const ws = new WebSocket(wsUrl(_q)); ws.binaryType = "arraybuffer"; this.ws = ws;
    const CRLF = String.fromCharCode(13, 10);
    ws.onopen = () => { if (myId !== this.connId) return; this._openAt = Date.now(); $("#consoleState").textContent = "● " + this.target; this.sendResize(); this.term.focus(); setTimeout(() => { if (myId === this.connId && ws.readyState === 1) { this._recoN = 0; this._kickN = 0; } }, 5000); };
    ws.onmessage = (e) => { if (myId !== this.connId) return; if (typeof e.data === "string") this.term.write(e.data); else this.term.write(new Uint8Array(e.data)); };
    ws.onclose = (e) => { if (myId !== this.connId) return;
      const why = (e && e.reason) ? String(e.reason) : "";
      if (e && e.code === 4001) {
        $("#consoleState").textContent = "auf anderem Screen \u00fcbernommen";
        this.term.write(CRLF + "[Ein anderer Screen hat die Session \u00fcbernommen \u2014 erneut \u00f6ffnen, um sie zur\u00fcckzuholen.]" + CRLF); return; }
      if (e && (e.code === 4003 || e.code === 4004)) {
        $("#consoleState").textContent = (e.code === 4004) ? "\u26a0 Terminal nicht verf\u00fcgbar" : "\u26a0 Session konnte nicht starten";
        this.term.write(CRLF + "[" + (why || "Terminal nicht verf\u00fcgbar") + "]" + CRLF + "[\u00c4ndert sich nicht von allein \u2014 erneut \u00f6ffnen, sobald die Ursache behoben ist.]" + CRLF); return; }
      if (e && e.code === 4002) {
        this._kickN = (this._kickN || 0) + 1;
        if (this._kickN >= 4) { $("#consoleState").textContent = "\u21bb Neustart klappt nicht"; this.term.write(CRLF + "[Session-VM startet immer wieder neu \u2014 mit An/Aus neu starten oder Speicher pr\u00fcfen.]" + CRLF); return; }
        const dk = Math.round(Math.min(15000, 1000 * Math.pow(2, this._kickN - 1)) * (0.85 + Math.random() * 0.30));
        $("#consoleState").textContent = "\u21bb Session startet neu \u2026"; this.term.write(CRLF + "[Session startet neu \u2014 neuer Versuch \u2026]" + CRLF);
        setTimeout(() => { if (myId === this.connId) this.connect(); }, dk); return; }
      const fast = (Date.now() - (this._openAt || 0)) < 2500; this._recoN = fast ? ((this._recoN || 0) + 1) : 0;
      if (this._recoN >= 4) { $("#consoleState").textContent = "nicht erreichbar"; this.term.write(CRLF + "[Terminal nicht erreichbar" + (why ? " \u2014 " + why : "") + " \u2014 Session pruefen.]" + CRLF); return; }
      const dl = Math.round(Math.min(15000, 1000 * Math.pow(2, Math.max(0, this._recoN - 1))) * (0.85 + Math.random() * 0.30));
      $("#consoleState").textContent = "getrennt \u2014 neu\u2026"; this.term.write(CRLF + "[getrennt" + (why ? " \u2014 " + why : "") + " \u2014 verbinde neu\u2026]" + CRLF);
      setTimeout(() => { if (myId === this.connId) this.connect(); }, dl); };
  },
  sendResize() { if (this.ws && this.ws.readyState === 1 && this.term) this.ws.send(JSON.stringify({ t: "r", rows: this.term.rows, cols: this.term.cols })); },
  async ensureSession() {
    if (this.sid) return;
    try {
      const r = await jget("/api/sessions/last");
      if (r && r.ok && r.session && r.session.id) { this.sid = r.session.id; return; }
      const n = await jpost("/api/sessions/new", {});
      if (n && n.ok && n.session && n.session.id) this.sid = n.session.id;
    } catch (e) {}
  },
  async newSession() {
    try {
      const n = await jpost("/api/sessions/new", { title: "Neue Session" });
      if (n && n.ok && n.session && n.session.id) { this.sid = n.session.id; this._renderSessName(); if (this.target === "cockpit") this.connect(); toast("Neue Session"); }
    } catch (e) {}
    this._closeHistory();
  },
  async toggleHistory() {
    const m = $("#sessMenu"); if (!m) return;
    if (!m.hidden) { m.hidden = true; return; }
    m.textContent = "…"; m.hidden = false;
    const r = await jget("/api/sessions");
    const list = (r && r.ok && r.sessions) ? r.sessions : [];
    m.textContent = "";
    if (!list.length) { const e = el("div", { class: "empty", text: "Noch keine Sessions" }); m.appendChild(e); return; }
    list.forEach(s => {
      const it = el("button", { class: "sess-item" + (s.id === this.sid ? " on" : ""), text: (s.title || "Session") });
      const d = new Date((s.last_active || 0) * 1000);
      it.appendChild(el("span", { class: "sess-when", text: isNaN(d.getTime()) ? "" : d.toLocaleString() }));
      it.addEventListener("click", () => this.resume(s.id, s.title));
      m.appendChild(it);
    });
  },
  resume(sid, title) { if (!sid) return; this.sid = sid; this._renderSessName(title); this.setTarget("cockpit"); this._closeHistory();

    try { if (Messenger && Messenger.host) Messenger.focusSession(sid); } catch (e) {} },
  _closeHistory() { const m = $("#sessMenu"); if (m) m.hidden = true; },
  _renderSessName(title) { const n = $("#sessName"); if (!n) return; n.textContent = title ? ("\uD83D\uDCAC " + title) : (this.sid ? ("\uD83D\uDCAC " + this.sid.slice(0, 6)) : ""); }
};

const Cadence = {
  LEVELS: [["echtzeit", "Echtzeit"], ["haeufig", "häufig"], ["selten", "selten"], ["nie", "nie"]],
  POLL: { echtzeit: 30000, haeufig: 60000, selten: 300000, nie: 0 },
  get(key, def) { return localStorage.getItem("pp-cad-" + key) || def || "haeufig"; },
  set(key, v) { localStorage.setItem("pp-cad-" + key, v); },
  pollMs(level) { return this.POLL[level] != null ? this.POLL[level] : 60000; },
  control(key, def, onChange) {
    const cur = this.get(key, def);
    const wrap = el("div", { class: "cad-ctl", title: "Wie oft aktualisieren" });
    this.LEVELS.forEach(([v, lab]) => {
      const b = el("button", { class: "cad-b" + (v === cur ? " on" : ""), text: lab });
      b.setAttribute("data-v", v);
      b.onclick = (e) => {
        e.stopPropagation(); this.set(key, v);
        wrap.querySelectorAll(".cad-b").forEach(x => x.classList.toggle("on", x.getAttribute("data-v") === v));
        if (onChange) onChange(v);
      };
      wrap.appendChild(b);
    });
    return wrap;
  }
};

const Commentary = {
  render(host, cadKey, cadDef, title, d, reload) {
    host.textContent = "";
    const head = el("div", { class: "cmt-head" }, [el("span", { class: "cmt-title", text: title })]);
    head.appendChild(Cadence.control(cadKey, cadDef, () => reload && reload()));
    host.appendChild(head);
    const list = el("div", { class: "cmt-list" });
    const entries = (d && d.entries) || [];
    const level = Cadence.get(cadKey, cadDef);
    if (!entries.length) {
      const msg = level === "nie" ? "Kommentar pausiert (nie)."
        : (d && d.state === "pending") ? "Erste Einschätzung wird erstellt …"
          : "Noch keine Notizen.";
      list.appendChild(el("div", { class: "cmt-empty muted", text: msg }));
    } else {
      entries.forEach(e => list.appendChild(this.bubble(e)));
    }
    host.appendChild(list);
    list.scrollTop = list.scrollHeight;
  },
  bubble(e) {
    const b = el("div", { class: "cmt-bubble" });
    if (e.headline) b.appendChild(el("div", { class: "cmt-hl", text: e.headline }));
    if (e.text) b.appendChild(el("div", { class: "cmt-tx", text: e.text }));
    if (e.next) b.appendChild(el("div", { class: "cmt-next", text: "→ " + e.next }));
    b.appendChild(el("div", { class: "cmt-when", text: e.ts ? fmtWhen(e.ts) : "" }));
    return b;
  }
};

const Work = {
  timer: null,
  init() {

    const sw = $("#qSwitch"), lanes = $("#qLanes");
    if (sw && lanes) {
      const pick = (lane) => {
        lanes.setAttribute("data-lane", lane);
        sw.querySelectorAll(".q-sw").forEach(b => {
          const on = b.getAttribute("data-lane") === lane;
          b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false");
        });
        localStorage.setItem("pp-work-lane", lane);
      };
      sw.querySelectorAll(".q-sw").forEach(b =>
        b.addEventListener("click", () => pick(b.getAttribute("data-lane"))));
      pick(localStorage.getItem("pp-work-lane") || "wait");
    }
  },
  show() { this.refresh(); this.renderNeedsYou(); this.loadChannel(); this.renderFleet(); this.timer = setInterval(() => { if (Router.cur === "work") this.refresh(); }, 2000); },
  loadChannel() {

    const host = $("#workComment"); if (!host) return;

    const railOps = typeof Rail !== "undefined" && Rail.mq && Rail.mq.matches && UIP.get("msgr", true)
      && (Rail.order().indexOf("opslog") >= 0
          || (localStorage.getItem("pp-rail-auto-work") !== "0"
              && localStorage.getItem("pp-rail-auto-work-opslog") !== "0"));
    if (railOps) { host.hidden = true; return; }
    if (localStorage.getItem("pp-bi-workload") === "0") { host.hidden = true; return; }
    if (this.chTimer) { clearTimeout(this.chTimer); this.chTimer = null; }
    const level = Cadence.get("work", "haeufig");
    const reload = () => this.loadChannel();
    if (level === "nie") { host.hidden = false; Commentary.render(host, "work", "haeufig", "🩺 Betriebs-Log", { entries: [], state: "off" }, reload); return; }
    jget("/api/board/channel?kind=work&cad=" + level).then(d => {
      if (d && d.ok === false && d.state === "off") { host.hidden = true; return; }
      host.hidden = false;
      Commentary.render(host, "work", "haeufig", "🩺 Betriebs-Log", d || {}, reload);
      const ms = Cadence.pollMs(level);
      if (ms && Router.cur === "work") this.chTimer = setTimeout(() => { if (Router.cur === "work") this.loadChannel(); }, ms);
    }).catch(() => {});
  },
  hide() { if (this.timer) { clearInterval(this.timer); this.timer = null; } if (this.chTimer) { clearTimeout(this.chTimer); this.chTimer = null; } },

  renderNeedsYou() {
    const box = $("#needsYou"), body = $("#needsYouBody"); const c = Ceremony.active;
    if (!c) { box.hidden = true; $("#workBadge").classList.remove("on"); return; }
    box.hidden = false; $("#workBadge").classList.add("on"); $("#workBadge").textContent = "!";
    body.textContent = "";
    const rb = c.readback || {}; let facts = c.verb || "irreversibel";
    if (rb.recipient) facts += " · " + rb.recipient; if (rb.subject) facts += " · " + rb.subject;
    body.appendChild(el("div", { class: "muted", text: (c.speak || "Bestätigung nötig.") + "  [" + facts + "]" }));
    if (c.state === "holding") {
      body.appendChild(el("div", { class: "row", style: "margin-top:8px" }, [el("button", { class: "btn sm danger", text: "⏹ Stopp", onclick: () => Ceremony.cancel() })]));
    } else {
      const inp = el("input", { placeholder: "Bestätigungswort sagen/tippen", style: "margin:8px 0" });
      body.appendChild(inp);
      body.appendChild(el("div", { class: "row" }, [
        el("button", { class: "btn sm primary", text: "✓ Bestätigen", onclick: () => Ceremony.confirm(inp.value.trim()) }),
        el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: () => Ceremony.cancel() })
      ]));
    }
  },

  async refresh() {
    const head = $("#headroom");
    const d = await jget("/api/queue?limit=120");
    if (!d || d._neterr) { head.innerHTML = "<span class='warn'>Queue-Fehler</span>"; return; }
    if (d.ok === false) {

      head.innerHTML = "<span class='warn'>pnd antwortet nicht" + (d.error ? ": " + esc(d.error) : "") + "</span>";
      $("#qqn").textContent = "\u2013"; $("#qrn").textContent = "\u2013"; $("#qdn").textContent = "\u2013";
      return;
    }
    this.isAdmin = !!d.admin;
    this.queuedTotal = d.queued_total || 0;
    if (d.admin && d.status && d.status.snap) {
      const s = d.status.snap, c = d.status.cfg || {}, cc = d.counts || {};
      head.innerHTML = "<b>RAM</b> " + s.mem_available + "/" + s.mem_total + " MiB frei · <b>batch</b> " + s.batch_current +
        " MiB · <b>PSI</b> " + (s.psi_avg10 || 0).toFixed(0) + " · <b>load</b> " + (s.load1 || 0).toFixed(1) + "/" + s.cpu_count +
        " · floor " + (c.mem_floor != null ? c.mem_floor : "?") + (d.status.pressure_blocked ? " · <span class='warn'>PRESSURE</span>" : "") +
        "<div class='qcounts'>Queue insgesamt: <b>" + (cc.done || 0).toLocaleString("de-DE") + "</b> erledigt · " +
        (cc.failed || 0).toLocaleString("de-DE") + " fehlgeschlagen · <b>" + (cc.queued || 0) + "</b> warten" +
        (cc.cancelled ? " · " + cc.cancelled + " abgebrochen" : "") + "</div>";
    } else if (d.admin) {
      head.innerHTML = "<span class='warn'>pnd nicht erreichbar" + (d.error ? ": " + esc(d.error) : "") + "</span>";
    } else {
      head.innerHTML = "<b>Warteschlange</b> " + this.queuedTotal + " Aufgaben insgesamt · <b>deine</b> " + (d.mine || 0);
    }
    this._now = d.now || (Date.now() / 1000);
    this.renderActiveSessions(d.sessions || []);

    const tk = this.admissionTickets(d.admission || null);

    const allJobs = (d.jobs || []).filter(j => j.source !== "session");
    this.renderFilter(allJobs);
    const hide = this._hiddenCats();
    const jobs = allJobs.filter(j => !hide[this.jobCat(j)]), term = { done: 1, failed: 1, cancelled: 1, timeout: 1 };
    const q = jobs.filter(j => j.state === "queued"), r = jobs.filter(j => j.state === "running"), done = jobs.filter(j => term[j.state]);
    this.fill("#qq", q); this.fill("#qr", r);
    this.appendTickets("#qq", tk.wait, "queued"); this.appendTickets("#qr", tk.run, "running");

    this.fillDone("#qd", done, tk.done);
    const nQ = q.length + tk.wait.length, nR = r.length + tk.run.length, nD = done.length + tk.done.length;
    $("#qqn").textContent = nQ; $("#qrn").textContent = nR; $("#qdn").textContent = nD;

    { const a = $("#qswqn"), b = $("#qswrn"), c = $("#qswdn");
      if (a) a.textContent = nQ; if (b) b.textContent = nR; if (c) c.textContent = nD; }
    if (!Ceremony.active) {

      const b = $("#workBadge");
      if (r.length) {
        b.textContent = r.length;
        const ids = r.map(j => String(j.id || j.jid || "")).filter(Boolean).sort().join(",");
        if ((location.hash || "").indexOf("work") >= 0) localStorage.setItem("pp-work-seen", ids);
        b.classList.add("on");
        b.classList.toggle("neutral", ids === (localStorage.getItem("pp-work-seen") || "") || !ids);
      } else { b.classList.remove("on"); }
    }
    if (d.admin) {
      this.batchPrincipal = (q[0] && q[0].principal) || (r[0] && r[0].principal) || null;
      const bp = this.batchPrincipal, note = el("div", { class: "qcounts" });
      const nsub = d.subsessions || 0;
      const jobQ = q.filter(j => j.kind !== "subsession").length;
      if (nsub > 0)
        note.appendChild(el("span", { text: nsub + " Sub-Session(s) als Last sichtbar (🧩) — gespawnte Kind-Sitzungen; sie starten, sobald die Box Platz hat (Kontingent + RAM). " }));
      if (jobQ > 0 && !r.length)
        note.appendChild(el("span", { text: jobQ + " Jobs warten — meist niederpriore Hintergrund-Jobs (filler/deferrable); sie laufen, sobald die Governance Kapazität freigibt. " }));
      if (jobQ > 0)
        note.appendChild(el("button", { class: "btn sm ghost", text: "🧹 Alle wartenden leeren (" + jobQ + ")", title: "entfernt nur WARTENDE Jobs aus der Queue — Laufendes bleibt unberührt", onclick: () => this.clearWaiting() }));
      if (bp) {
        note.appendChild(el("button", { class: "btn sm", text: "⏸ Rechenlast einfrieren (" + bp + ")", onclick: () => this.freeze(bp, true) }));
        note.appendChild(el("button", { class: "btn sm", text: "▶ auftauen", onclick: () => this.freeze(bp, false) }));
      }
      if (note.childNodes.length) $("#headroom").appendChild(note);
    }
  },
  rtime(startedAt, now) {
    if (!startedAt) return "";
    const s = Math.max(0, Math.floor((now || Date.now() / 1000) - startedAt));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + " min";
    if (s < 86400) return Math.floor(s / 3600) + " h";
    return Math.floor(s / 86400) + " Tg";
  },
  renderActiveSessions(sessions) {

    const host = $("#activeSessions"); if (!host) return;

    const railSess = typeof Rail !== "undefined" && Rail.mq && Rail.mq.matches && UIP.get("msgr", true)
      && (Rail.order().indexOf("sessions") >= 0
          || (localStorage.getItem("pp-rail-auto-work") !== "0"
              && localStorage.getItem("pp-rail-auto-work-sessions") !== "0"));
    if (railSess) { host.hidden = true; return; }
    if (!sessions.length) { host.hidden = true; return; }
    host.hidden = false; host.textContent = "";
    host.appendChild(el("h4", { class: "as-h", text: "🖥 Aktive Sessions · " + sessions.length + " (RAM/CPU — kein Auftrag)" }));
    const list = el("div", { class: "as-list" });
    sessions.forEach(s => {
      const row = el("div", { class: "as-row", title: "Im Sessions-Reiter verwalten" }, [
        el("span", { class: "as-dot" }),
        el("span", { class: "as-ttl ellipsis", text: (s.voice ? "🎙 " : "") + (s.title || s.sid) }),
        el("span", { class: "as-meta", text: (this.rtime(s.started_at, this._now) || "") + (s.mem_mb ? " · " + s.mem_mb + " MiB" : "") }),
      ]);
      if (s.model) row.appendChild(el("span", { class: "aj-model", text: s.model }));
      if (this.isAdmin && s.principal) row.appendChild(el("span", { class: "qowner", text: s.principal }));
      const _nc = this.nodeChip(s); if (_nc) row.appendChild(_nc);
      if (this.isAdmin && s.sid && !s.voice) row.appendChild(el("button", { class: "btn xs ghost as-mig",
        text: "Migrieren →", title: "Auf anderen Node ziehen (z. B. für Wartung)",
        onclick: (e) => { e.stopPropagation(); this.migrateSession(s.sid); } }));
      if (s.sid && !s.voice) row.onclick = () => openSessionTerminal(s.sid);
      list.appendChild(row);
    });
    host.appendChild(list);
  },

  _NODE_ICON: { box: "🖥", vm: "🖥", pi: "🍓", container: "📦", cloud: "☁️" },
  nodeChip(row) {
    if (!row || !row.node_kind) return null;
    const isLocal = (!row.node || row.node === "local");
    const name = isLocal ? "Diese Box" : (row.node_name || row.node);
    return el("span", { class: "as-node", title: "Läuft physisch auf: " + (row.node_name || row.node || "?")
      + " · " + (row.node_kind || "?") + (row.migration_class ? " · " + row.migration_class : ""),
      text: (this._NODE_ICON[row.node_kind] || "•") + " " + name });
  },
  migrateSession(sid) {
    jget("/api/nodes").then(d => {
      const nodes = ((d && d.nodes) || []).filter(n => n.id !== "local" && n.state !== "offline");
      if (!nodes.length) { toast("Kein weiterer Node vorhanden — erst einen Pi/Node hinzufügen (Fleet)."); return; }
      const pick = nodes[0];
      jpost("/api/session/migrate", { sid: sid, target_node: pick.id }).then(r => {
        toast(r && r.ok ? (r.note || ("Kalt-Umzug nach " + (r.target || pick.name) + " geplant")) : ((r && r.error) || "Migration nicht möglich"));
      }).catch(() => {});
    }).catch(() => {});
  },
  renderFleet() {
    const host = $("#fleetPanel"); if (!host) return;
    jget("/api/nodes").then(d => {
      const nodes = (d && d.nodes) || [];
      if (!nodes.length) { host.hidden = true; return; }
      host.hidden = false; host.textContent = "";
      host.appendChild(el("div", { class: "dj-bar" }, [
        el("h4", { html: "🖧 Fleet <span class='card-hint'>" + nodes.length + " Node(s) · wo Workloads laufen können</span>" })]));
      nodes.forEach(n => {
        const caps = n.caps || {};
        const tier = caps.cells ? "Zellen (microVM)" : (caps.cell_ns ? "Container/Sandbox" : "Sandbox");
        const dot = n.state === "online" ? "🟢" : (n.state === "offline" ? "🔴" : "⚪");
        const card = el("div", { class: "dj-card" }, [
          el("div", { class: "dj-head" }, [
            el("div", { class: "dj-name", html: dot + " " + (this._NODE_ICON[n.node_kind] || "•") + " " + (n.name || n.id)
              + (n.local ? " <span class='card-hint'>(diese Box)</span>" : "") }),
            el("div", { class: "dj-state", text: (n.node_kind || "?") + " · " + (n.arch || "?") })]),
          el("div", { class: "dj-meta", html: "<span>" + tier + "</span>" + (caps.kvm ? "<span>KVM</span>" : "")
            + "<span class='muted'>" + (n.state || "") + "</span>" })]);

        const res = n.res || {};
        if (res.nproc) {
          const bar = (p) => el("span", { class: "usebar " + (p >= 90 ? "bad" : p >= 70 ? "warn" : "good") },
            [el("i", { style: "width:" + Math.max(0, Math.min(100, p)) + "%" })]);
          const gb = (mb) => (mb / 1024).toLocaleString("de", { maximumFractionDigits: 1 });
          const rw = el("div", { class: "dj-meta" }, [el("span", { class: "muted tnum",
            text: res.nproc + " Kerne" + (res.mem_total_mb ? " · " + gb(res.mem_total_mb) + " GB" : "") })]);
          if (res.load1 != null) {
            const cp = Math.round(res.load1 / res.nproc * 100);
            rw.appendChild(bar(cp)); rw.appendChild(el("span", { class: "muted tnum", text: "CPU " + cp + "%" }));
          }
          if (res.mem_total_mb && res.mem_avail_mb != null) {
            const mp = Math.round((res.mem_total_mb - res.mem_avail_mb) / res.mem_total_mb * 100);
            rw.appendChild(bar(mp)); rw.appendChild(el("span", { class: "muted tnum", text: "RAM " + mp + "%" }));
          }
          card.appendChild(rw);
        }
        host.appendChild(card);
      });
      const on = nodes.filter(n => n.state === "online" && n.res && n.res.nproc);
      if (on.length) {
        const cores = on.reduce((s, n) => s + (n.res.nproc || 0), 0);
        const mem = on.reduce((s, n) => s + (n.res.mem_total_mb || 0), 0);
        host.appendChild(el("div", { class: "card-hint", text: "Fleet online: " + cores + " Kerne · "
          + Math.round(mem / 1024) + " GB RAM über " + on.length + " Node(s). Verteilen/Verschieben von Workloads = Fleet-Phase 3 (offen) — bis dahin läuft alles auf dieser Box." }));
      }
    }).catch(() => {});
  },

  FILTER_CATS: { device: "🔎 Gerätesuche", voice: "🎙 Voice", llm: "🧠 System-LLM", filler: "⚙ Hintergrund", sub: "🧩 Sub-Sessions", other: "Sonstige" },
  jobCat(j) {
    if (j.kind === "subsession") return "sub";
    const t = String(j.client_tag || j.label || j.task_type || "").toLowerCase();
    if (t.startsWith("device")) return "device";
    if (t.startsWith("voice")) return "voice";
    if (t.startsWith("llm")) return "llm";
    if (t === "filler" || j.source === "filler") return "filler";
    return "other";
  },
  _hiddenCats() {
    try { return JSON.parse(localStorage.getItem("pp-work-hide") || "{}") || {}; } catch (e) { return {}; }
  },
  renderFilter(jobs) {

    const host = $("#workFilter"); if (!host) return;
    const cats = Array.from(new Set((jobs || []).map(j => this.jobCat(j)))).sort();
    const sig = cats.join(",");
    if (host._sig === sig && host._built) { this._syncFilterLabel(cats); return; }
    host._sig = sig; host._built = true; host.textContent = "";
    if (!cats.length) { host.hidden = true; return; }
    host.hidden = false;
    const menu = el("div", { class: "wf-menu", hidden: true });
    cats.forEach(cat => {
      const hide = this._hiddenCats();
      const lab = el("label", { class: "wf-item" });
      const cb = el("input", { type: "checkbox" }); cb.checked = !hide[cat];
      cb.addEventListener("change", () => {
        const h = this._hiddenCats();
        if (cb.checked) delete h[cat]; else h[cat] = 1;
        localStorage.setItem("pp-work-hide", JSON.stringify(h));
        this._syncFilterLabel(cats); this.refresh();
      });
      lab.appendChild(cb); lab.appendChild(document.createTextNode(" " + (this.FILTER_CATS[cat] || cat)));
      menu.appendChild(lab);
    });
    this._filterBtn = el("button", { class: "btn sm ghost", onclick: () => { menu.hidden = !menu.hidden; } });
    host.appendChild(this._filterBtn); host.appendChild(menu);
    this._syncFilterLabel(cats);
  },
  _syncFilterLabel(cats) {
    if (!this._filterBtn) return;
    const hide = this._hiddenCats();
    const n = (cats || []).filter(c => hide[c]).length;
    this._filterBtn.textContent = "⚙ Anzeige" + (n ? " · " + n + " ausgeblendet" : "");
  },
  admissionTickets(a) {

    const out = { wait: [], run: [], done: [] };
    if (!a) return out;
    [["LLM", a.llm], ["exec", a.exec], ["act", a.act]].forEach(([kind, x]) => {
      if (!x) return;
      (x.waiting_list || []).forEach(t => out.wait.push(Object.assign({ _kind: kind }, t)));
      (x.granted_list || []).forEach(t => out.run.push(Object.assign({ _kind: kind }, t)));
      (x.done_list || []).forEach(t => out.done.push(Object.assign({ _kind: kind }, t)));
    });
    return out;
  },
  appendTickets(sel, tickets, state) {
    const box = $(sel); if (!box || !tickets.length) return;
    const empty = box.querySelector(".empty"); if (empty) empty.remove();
    tickets.slice(0, 40).forEach(t => box.appendChild(this.ticketCard(t, state)));
  },
  ticketCard(t, state) {
    const who = t.origin || (t.cell === "tcp" ? "extern / API" : (t.cell || t.principal || ""));
    const kindLbl = t._kind === "LLM" ? "LLM-Aufruf" : (t._kind === "exec" ? "Ausführung" : "Aktion");
    const top = el("div", { class: "qtop" }, [
      el("span", { class: "qid", text: "🔮" }),
      el("span", { class: "qtag ellipsis", text: kindLbl + " · " + (t.voice ? "🎙 " : "") + who }),
    ]);
    if (t.model) top.appendChild(el("span", { class: "aj-model", text: t.model }));
    if (this.isAdmin && t.principal) top.appendChild(el("span", { class: "qowner", text: t.principal }));
    const meta = state === "queued"
      ? ("wartet" + (t.position ? " · Pos " + t.position : "") + (t.wait_s ? " · " + Math.round(t.wait_s) + "s" : ""))
      : state === "running"
        ? ("läuft" + (t.wait_s ? " · " + Math.round(t.wait_s) + "s" : ""))
        : ("fertig" + (t.done_at ? " · " + fmtWhen(t.done_at) : ""));
    const card = el("div", { class: "qcard s-" + state + " qticket" }, [top]);
    card.appendChild(el("div", { class: "qmeta", text: meta }));
    return card;
  },
  async freeze(principal, on) {

    const r = await jpost("/api/admin/user/" + encodeURIComponent(principal) + "/" + (on ? "pause" : "resume"), {});
    toast(r && r.ok !== false ? (on ? "⏸ " + principal + " eingefroren" : "▶ " + principal + " fortgesetzt") : "Fehler");
    setTimeout(() => this.refresh(), 400);
  },
  fill(sel, jobs) {
    const box = $(sel); box.textContent = "";
    if (!jobs.length) { box.appendChild(el("div", { class: "empty", text: "—" })); return; }
    jobs.forEach(j => box.appendChild(this.card(j)));
  },
  fillDone(sel, jobs, tickets) {

    const box = $(sel); box.textContent = "";
    const items = [];
    (jobs || []).forEach(j => items.push({ ts: j.finished_at || j.done_at || j.ended_at || 0, make: () => this.card(j) }));
    (tickets || []).forEach(t => items.push({ ts: t.done_at || t.finished_at || 0, make: () => this.ticketCard(t, "done") }));
    if (!items.length) { box.appendChild(el("div", { class: "empty", text: "—" })); return; }
    items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    items.slice(0, 40).forEach(it => box.appendChild(it.make()));
  },
  async djForm() {
    const wrap = el("div", { class: "dj-form stack" });
    const add = (lab, node) => { wrap.appendChild(el("label", { class: "dj-lab", text: lab })); wrap.appendChild(node); };
    const title = el("input", { class: "dj-in", placeholder: "z. B. Paper-Reproduktion" });
    add("Titel", title);
    const tasks = el("textarea", { class: "dj-ta", rows: "5", placeholder: "Eine Aufgabe pro Zeile — jede wird von einer eigenen isolierten Session bearbeitet." });
    add("Aufgaben (eine pro Zeile)", tasks);
    wrap.appendChild(el("div", { class: "dj-hint muted", text: "Leer lassen und spaeter Aufgaben hinzufuegen ist ok." }));
    const conc = el("input", { class: "dj-in", type: "number", value: "2", min: "1", max: "16" });
    add("Gleichzeitige Worker", conc);
    wrap.appendChild(el("div", { class: "dj-hint muted", text: "Jeder Worker ist eine eigene microVM (~1,5 GB RAM). Klein anfangen." }));
    const modelSel = el("select", { class: "dj-in" }, ["sonnet", "opus", "haiku"].map(m => el("option", { value: m, text: m })));
    add("Modell", modelSel);
    const effortSel = el("select", { class: "dj-in" }, [["low", "niedrig"], ["medium", "mittel"], ["high", "hoch"]].map(o => el("option", { value: o[0], text: o[1] })));
    effortSel.value = "high"; add("Sorgfalt", effortSel);
    const auton = el("input", { class: "dj-range", type: "range", min: "0", max: "5", value: "4" });
    const autonVal = el("span", { class: "dj-rangeval", text: "4" });
    auton.addEventListener("input", () => { autonVal.textContent = auton.value; });
    wrap.appendChild(el("label", { class: "dj-lab", text: "Autonomie (0-5)" }));
    wrap.appendChild(el("div", { class: "dj-row" }, [auton, autonVal]));
    const presets = [["voll", "Voll (Wegwerf, Internet+LAN)"], ["standard", "Im Haus"], ["erweitert", "Im Haus, erweitert"], ["fernzugriff", "Fernzugriff"], ["minimal", "Nur Assistenz"]];
    const presetSel = el("select", { class: "dj-in" }, presets.map(o => el("option", { value: o[0], text: o[1] })));
    add("Rechte-Voreinstellung", presetSel);
    const vpnSel = el("select", { class: "dj-in" }, [el("option", { value: "", text: "— kein VPN —" })]);
    let vpns = []; try { const vd = await jget("/api/vpn"); vpns = (vd && vd.vpns) || []; } catch (e) {}
    vpns.forEach(v => vpnSel.appendChild(el("option", { value: v.id, text: (v.name || v.id) + (v.active ? " (verbunden)" : "") })));
    add("Durch VPN routen?", vpnSel);
    const bind = el("input", { type: "checkbox" });
    const bindWrap = el("div", { class: "dj-bind", hidden: true }, [
      el("label", {}, [bind, el("span", { text: " Worker fest durch diesen VPN schleifen (fail-closed)" })]),
      el("div", { class: "dj-warn", text: "VPN-gebundene Dauerjobs brechen meist am ~24h-Reconnect (2FA) ab. Der Tunnel muss verbunden sein (Sessions -> Ausstattung -> VPN verbinden), sonst warten die Worker." })]);
    wrap.appendChild(bindWrap);
    vpnSel.addEventListener("change", () => { bindWrap.hidden = !vpnSel.value; if (!vpnSel.value) bind.checked = false; });
    const submit = el("button", { class: "btn", text: "Dauerjob anlegen" });
    submit.addEventListener("click", async () => {
      const body = {
        title: (title.value || "Dauerjob").trim(),
        max_concurrent: Math.max(1, Math.min(16, parseInt(conc.value, 10) || 2)),
        template: { model: modelSel.value, effort: effortSel.value, autonomy: parseInt(auton.value, 10), preset: presetSel.value, vpn: vpnSel.value || null, vpn_dauerjob: !!(vpnSel.value && bind.checked), caps: {} },
        tasks: (tasks.value || "").split("\n").map(s => s.trim()).filter(Boolean)
      };
      submit.disabled = true;
      let r; try { r = await jpost("/api/metasession", body); } catch (e) { r = null; }
      submit.disabled = false;
      if (r && r.ok) { toast("Dauerjob angelegt"); Overlay.close(); this.refresh(); try { Rail.render(); } catch (e) {} }
      else { toast((r && r.error) || "Fehler beim Anlegen"); }
    });
    wrap.appendChild(submit);
    return wrap;
  },
  async djAction(id, action) {
    if (action === "delete" && !confirm("Diesen Dauerjob wirklich loeschen? Laufende Worker-Sessions werden gestoppt.")) return;
    let r; try { r = await jpost("/api/metasession/" + id + "/" + action, {}); } catch (e) { r = null; }
    if (r && r.ok) { toast(action === "delete" ? "Geloescht" : action === "pause" ? "Pausiert" : "Fortgesetzt"); this.refresh(); try { Rail.render(); } catch (e) {} }
    else toast((r && r.error) || "Fehler");
  },
  djAddTasks(id) {
    const wrap = el("div", { class: "dj-form stack" });
    wrap.appendChild(el("div", { class: "muted", text: "Neue Aufgaben (eine pro Zeile)." }));
    const ta = el("textarea", { class: "dj-ta", rows: "6" });
    wrap.appendChild(ta);
    const b = el("button", { class: "btn", text: "Hinzufuegen" });
    b.addEventListener("click", async () => {
      const list = (ta.value || "").split("\n").map(s => s.trim()).filter(Boolean);
      if (!list.length) { toast("Keine Aufgaben"); return; }
      b.disabled = true;
      let r; try { r = await jpost("/api/metasession/" + id + "/tasks", { tasks: list }); } catch (e) { r = null; }
      b.disabled = false;
      if (r && r.ok) { toast((r.added || 0) + " Aufgabe(n) hinzugefuegt"); Overlay.close(); this.refresh(); }
      else toast((r && r.error) || "Fehler");
    });
    wrap.appendChild(b);
    Overlay.open("Aufgaben hinzufuegen", wrap);
  },
  async djDetail(id) {
    const wrap = el("div", { class: "dj-detail stack" }, [el("div", { class: "muted", text: "laedt …" })]);
    Overlay.open("Dauerjob", wrap);
    let d; try { d = await jget("/api/metasession/" + id); } catch (e) { d = null; }
    wrap.textContent = "";
    if (!d || !d.ok) { wrap.appendChild(el("div", { class: "warn", text: "Nicht gefunden." })); return; }
    const t = d.template || {}, c = d.counts || {};
    wrap.appendChild(el("h3", { text: d.title || id }));
    wrap.appendChild(el("div", { class: "muted", text: "Status " + d.state + " - gleichzeitig " + d.max_concurrent + " - Modell " + (t.model || "?") + " - Autonomie L" + (t.autonomy != null ? t.autonomy : "?") + (t.vpn ? " - VPN " + t.vpn + (t.vpn_dauerjob ? " (gebunden)" : "") : "") }));
    wrap.appendChild(el("div", { class: "muted", text: "wartet " + (c.pending || 0) + " - laeuft " + (c.running || 0) + " - fertig " + (c.done || 0) + " - Fehler " + (c.error || 0) }));
    const runningD = d.state === "running";
    wrap.appendChild(el("div", { class: "dj-acts" }, [
      el("button", { class: "btn xs", text: "+ Aufgaben", onclick: () => this.djAddTasks(id) }),
      el("button", { class: "btn xs ghost", text: runningD ? "Pause" : "Fortsetzen", onclick: () => { Overlay.close(); this.djAction(id, runningD ? "pause" : "resume"); } }),
      el("button", { class: "btn xs ghost dj-del", text: "Loeschen", onclick: () => { Overlay.close(); this.djAction(id, "delete"); } })
    ]));
    const tasks = (d.tasks || []).slice(-40).reverse();
    if (!tasks.length) wrap.appendChild(el("div", { class: "muted", text: "Noch keine Aufgaben." }));
    tasks.forEach(tk => {
      const cls = tk.state === "error" ? "error" : tk.state === "done" ? "done" : tk.state === "running" ? "run" : "";
      wrap.appendChild(el("div", { class: "dj-task " + cls }, [
        el("span", { class: "dj-task-state", text: tk.state || "?" }),
        el("span", { class: "dj-task-prompt", text: (tk.prompt || "").slice(0, 90) })]));
    });
  },
  jobOrigin(j) {

    if (j.origin) return j.origin;
    if (j.kind === "subsession") return "🧩 " + (j.label || "Sub-Session");
    const t = String(j.client_tag || j.label || j.task_type || "").toLowerCase();
    const MAP = {
      "device.discover": "🔎 Geräte-Erkennung (System)",
      "llm.chat": "🧠 LLM-Aufruf (System/Dashboard)",
      "voice.stt": "🎙 Spracherkennung (Voice)",
      "voice.tts": "🔊 Sprachausgabe (Voice)",
    };
    if (MAP[t]) return MAP[t];
    if (t.startsWith("voice")) return "🎙 " + t + " (Voice)";
    if (t.startsWith("device")) return "🔎 " + t + " (Geräte)";
    if (t.startsWith("llm")) return "🧠 " + t + " (System)";
    if (t === "filler" || j.source === "filler") return "⚙ Hintergrund (filler)";
    return (j.label || j.client_tag || "System-Aufgabe") + (j.source && j.source !== "cli" ? " · " + j.source : "");
  },
  card(j) {
    const pct = (j.prog_done != null && j.prog_total) ? Math.floor(100 * j.prog_done / j.prog_total) : null;
    const sub = j.kind === "subsession";
    const top = el("div", { class: "qtop" }, [
      el("span", { class: "qid", text: sub ? "🧩" : "#" + j.id }),
      el("span", { class: "qtag ellipsis", text: this.jobOrigin(j) }),
    ]);
    if (this.isAdmin && j.principal) top.appendChild(el("span", { class: "qowner", text: j.principal }));
    if (!sub && this.isAdmin && j.state === "queued") {
      top.appendChild(el("button", { class: "qbump", text: "▲", title: "vorziehen (prio −20)",
        onclick: (e) => { e.stopPropagation(); this.reprioritize(j.id, -20); } }));
      top.appendChild(el("button", { class: "qbump", text: "▼", title: "zurückstellen (prio +20)",
        onclick: (e) => { e.stopPropagation(); this.reprioritize(j.id, 20); } }));
    }
    if (!sub && (j.state === "queued" || j.state === "running")) {
      const x = el("button", { class: "qx", text: "✕", title: this.isAdmin ? "einfangen / abbrechen" : "abbrechen",
        onclick: (e) => { e.stopPropagation(); this.cancel(j.id); } }); top.appendChild(x);
    }
    const card = el("div", { class: "qcard s-" + j.state }, [top]);

    if (j.kerne && j.state === "queued") {
      card.appendChild(el("div", { class: "qpos", text: "wartet auf " + j.kerne + " Kern" + (j.kerne > 1 ? "e" : "") +
        (j.kerne_wunsch ? " (flexibel, Wunsch " + j.kerne_wunsch + ")" : "") +
        " · ETA " + (j.eta_human || "unbekannt") }));
    }
    if (j.position != null) {
      const t = "Position " + j.position + (this.queuedTotal ? " von " + this.queuedTotal : "") + (j.eta_human && !(j.kerne && j.state === "queued") ? " · ETA " + j.eta_human : "");
      card.appendChild(el("div", { class: "qpos", text: t }));
    }
    if (pct != null) { const bar = el("div", { class: "bar" }, [el("i")]); bar.firstChild.style.width = pct + "%"; card.appendChild(bar); card.appendChild(el("div", { class: "qmeta", text: pct + "%" })); }
    else if (j.state === "running") {

      const rt = Work.rtime(j.started_at, this._now);
      card.appendChild(el("div", { class: "qmeta", text: rt ? ("läuft · " + rt) : "läuft" }));
    }
    if (j.prog_msg) card.appendChild(el("div", { class: "qmsg", text: j.prog_msg }));
    if (j.room) card.appendChild(el("button", { class: "qroom", text: "▶ " + j.room, onclick: (e) => { e.stopPropagation(); Overlay.room(j.room); } }));
    const mt = [];
    if (j.submitted_at) mt.push("eingereicht " + fmtWhen(j.submitted_at));
    if (j.started_at) mt.push("gestartet " + fmtWhen(j.started_at));
    if (j.finished_at) mt.push("fertig " + fmtWhen(j.finished_at));
    mt.push(j.state);
    if (j.source) mt.push(j.source);
    if (j.exit_code != null) mt.push("exit " + j.exit_code);
    if (j.mem_estimate != null) mt.push(j.mem_estimate + "M");
    if (j.kerne) mt.push(j.kerne + " Kern" + (j.kerne > 1 ? "e" : "") + (j.kerne_wunsch ? " (Wunsch " + j.kerne_wunsch + ")" : ""));
    if (this.isAdmin && j.prio != null) mt.push("prio " + j.prio);

    const tTitle = (j.submitted_at ? "eingereicht " + new Date(j.submitted_at * 1000).toLocaleString() : "") +
      (j.started_at ? "\ngestartet " + new Date(j.started_at * 1000).toLocaleString() : "") +
      (j.finished_at ? "\nfertig " + new Date(j.finished_at * 1000).toLocaleString() : "");
    card.appendChild(el("div", { class: "qmeta", title: tTitle, text: mt.join(" · ") }));
    if (j.node && j.node !== "local") { const _nc = this.nodeChip(j); if (_nc) card.appendChild(_nc); }
    if (!sub) card.addEventListener("click", () => Overlay.job(j.id));
    return card;
  },
  async cancel(id) { await jpost("/api/queue/" + id + "/cancel"); this.refresh(); },
  async clearWaiting() {
    const r0 = await jget("/api/queue?limit=1");
    const n = (r0 && r0.counts && r0.counts.queued) || this.queuedTotal || 0;
    if (!confirm("Alle wartenden Jobs abbrechen?\nLaufende Jobs und Sessions bleiben unberührt.")) return;
    const r = await jpost("/api/queue/clear-waiting", {});
    toast(r && r.ok ? ("🧹 " + (r.cleared || 0) + " wartende Jobs geleert" + (r.failed ? " (" + r.failed + " Fehler)" : "")) : ("Fehler: " + ((r && r.error) || "?")));
    setTimeout(() => this.refresh(), 400);
  },
  async reprioritize(id, delta) {
    const d = await jpost("/api/queue/" + id + "/reprioritize", { delta });
    if (d && d.ok) toast("Job #" + id + " → prio " + d.prio); else toast("Fehler: " + ((d && d.error) || "?"));
    this.refresh();
  }
};
function esc(s) { return (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }

const Overlay = {
  feedWs: null,
  init() {
    $("#sheetClose").addEventListener("click", () => this.close());
    $("#sheetBackdrop").addEventListener("click", () => this.close());
  },
  open(title, node) {
    $("#sheetTitle").textContent = title; const b = $("#sheetBody"); b.textContent = ""; b.appendChild(node);
    $("#sheet").classList.add("on"); $("#sheet").setAttribute("aria-hidden", "false"); $("#sheetBackdrop").classList.add("on");
  },
  close() {
    $("#sheet").classList.remove("on"); $("#sheet").setAttribute("aria-hidden", "true"); $("#sheetBackdrop").classList.remove("on");
    if (this.feedWs) { try { this.feedWs.close(); } catch (e) {} this.feedWs = null; }
  },
  async job(id) {
    const wrap = el("div", { class: "stack" }); this.open("Auftrag #" + id, wrap);
    wrap.appendChild(el("div", { class: "muted", text: "lädt …" }));
    const j = await jget("/api/queue/" + id); const logd = await jget("/api/queue/" + id + "/log");
    wrap.textContent = "";
    wrap.appendChild(el("div", { class: "row" }, [el("span", { class: "badge s-" + (j.state || "") , text: j.state || "?" }), el("span", { class: "muted", text: (j.client_tag || "") + " · " + (j.mem_estimate || 0) + "M" })]));
    if (j.state === "queued" || j.state === "running") wrap.appendChild(el("button", { class: "btn sm danger", text: "✕ Abbrechen", onclick: () => { Work.cancel(id); this.close(); } }));
    if (Array.isArray(j.events) && j.events.length) {
      wrap.appendChild(el("div", { class: "subhead", text: "Events" }));
      j.events.slice(-20).forEach(ev => wrap.appendChild(el("div", { class: "muted", text: typeof ev === "string" ? ev : JSON.stringify(ev) })));
    }
    wrap.appendChild(el("div", { class: "subhead", text: "Log" }));
    const logtext = logd && (logd.log || logd.text || logd.output) || (typeof logd === "string" ? logd : "");
    wrap.appendChild(el("pre", { class: "sheet-log", text: logtext || "(kein Log)" }));
  },
  room(name) {
    const pre = el("pre", { class: "sheet-log", text: "" }); this.open("live: " + name, pre);
    if (this.feedWs) { try { this.feedWs.close(); } catch (e) {} }
    const ws = new WebSocket(wsUrl("/ws/feed?room=" + encodeURIComponent(name))); ws.binaryType = "arraybuffer"; this.feedWs = ws;
    ws.onmessage = (e) => { if (typeof e.data === "string") { pre.textContent += e.data; pre.scrollTop = pre.scrollHeight; } };
  }
};

const ACTIONS = [
  { id: "go-start",   title: "Start öffnen",          hint: "lens", run: () => Router.go("start") },
  { id: "go-work",    title: "Work öffnen",           hint: "lens", run: () => Router.go("work") },

  { id: "go-msgr",    title: "Messenger öffnen",      hint: "lens", run: () => Router.go("msgr") },

  { id: "claude",     title: "Claude-Anmeldung",      hint: "Einrichtung", run: () => claudeSignin(null) },

  { id: "vpn",        title: "VPN-Zugänge anzeigen",  hint: "Einstellungen", run: () => Router.go("settings") },
  { id: "theme",      title: "Thema wechseln",        hint: "ui", run: () => Theme.toggle() },
  { id: "logout",     title: "Abmelden",              hint: "auth", run: () => logout() },

  { id: "agent-state",title: "Agent: Status abrufen", hint: "verb state", verb: "state", args: {} }
];
const Palette = {
  sel: 0, filtered: ACTIONS,
  init() {
    this.box = $("#palette"); this.input = $("#paletteInput"); this.list = $("#paletteList");
    $("#paletteBtn").addEventListener("click", () => this.open());
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); this.open(); }
      else if (e.key === "Escape" && this.box.classList.contains("on")) this.close();
    });
    this.input.addEventListener("input", () => this.render());
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); this.sel = Math.min(this.sel + 1, this.filtered.length - 1); this.render(true); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.sel = Math.max(this.sel - 1, 0); this.render(true); }
      else if (e.key === "Enter") { e.preventDefault(); this.exec(this.filtered[this.sel]); }
    });
    this.box.addEventListener("click", (e) => { if (e.target === this.box) this.close(); });
  },
  open() { this.box.classList.add("on"); this.input.value = ""; this.sel = 0; this.render(); setTimeout(() => this.input.focus(), 30); },
  close() { this.box.classList.remove("on"); },
  match(q, s) {
    q = q.toLowerCase(); s = s.toLowerCase(); let i = 0; for (let c = 0; c < s.length && i < q.length; c++) if (s[c] === q[i]) i++; return i === q.length;
  },
  render(keepSel) {
    const q = this.input.value.trim();
    this.filtered = q ? ACTIONS.filter(a => this.match(q, a.title + " " + a.hint)) : ACTIONS;
    if (!keepSel) this.sel = 0; if (this.sel >= this.filtered.length) this.sel = Math.max(0, this.filtered.length - 1);
    this.list.textContent = "";
    this.filtered.forEach((a, i) => {
      const row = el("div", { class: "palette-item" + (i === this.sel ? " sel" : "") }, [el("span", { text: a.title }), el("span", { class: "p-hint", text: a.hint })]);
      row.addEventListener("click", () => this.exec(a));
      this.list.appendChild(row);
    });
  },
  exec(a) { if (!a) return; this.close(); if (a.verb) agentExec(a.verb, a.args); else if (a.run) a.run(); }
};

let ROLE = window.PP_ROLE || "";
let IS_ADMIN = ["owner", "admin"].indexOf(ROLE) >= 0;

function reconcileRole(role) {
  if (role) ROLE = role;
  const wasAdmin = IS_ADMIN;
  IS_ADMIN = ["owner", "admin"].indexOf(ROLE) >= 0;
  if (IS_ADMIN && !wasAdmin) {
    try { Admin.init(); } catch (e) {}
    try { Stats.init(); } catch (e) {}
    ["#stgBoxHead", "#stgBoxCard", "#stDevCard", "#stFrgCard", "#stShellCard"].forEach(id => { const n = $(id); if (n) n.hidden = false; });
    try { const f = $("#stFrgFrame"); if (f && !f.getAttribute("src")) f.setAttribute("src", "/freigaben"); } catch (e) {}
    try { const d = $("#devFrame"); if (d && !d.getAttribute("src")) d.setAttribute("src", "/devices"); } catch (e) {}
  }
}
const BAN_FEATURES = ["streaming", "wan", "queue", "screen", "voice", "browser", "terminal"];

async function adminApi(path, opts) {
  let r;
  try { r = await fetch(path, opts || {}); }
  catch (e) { return { ok: false, _neterr: true, error: String(e) }; }
  if (r.status === 403) { Admin.forbidden(); return { ok: false, _forbidden: true }; }
  if (r.status === 401) { sessionLost(); return { ok: false, _auth: false }; }
  const ct = r.headers.get("Content-Type") || "";
  if (ct.indexOf("application/json") >= 0) { try { return await r.json(); } catch (e) { return { ok: false }; } }
  return r;
}
const aget  = (p) => adminApi(p);
const apost = (p, body) => adminApi(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

const Admin = {
  timer: null, hold: 0, capPct: 100, boxMemMb: 0, nproc: 0, reserved: 0,
  keys: [], catalog: [], principals: [], keysShowRev: false, keyEdit: null,
  init() {
    if (!IS_ADMIN) return;
    if (this._inited) { const nav = $("#navAdmin"); if (nav) nav.hidden = false; return; }
    this._inited = true;
    LENS_TITLE.admin = "🛡 Admin";
    LENSES.admin = this;
    const nav = $("#navAdmin"); if (nav) nav.hidden = false;
    $("#admResMinus").addEventListener("click", () => this.stepReserved(-1));
    $("#admResPlus").addEventListener("click", () => this.stepReserved(1));
    const ms = $("#admMailSend"); if (ms) ms.addEventListener("click", () => this.sendMailTest());
    const pr = $("#admPoolReload"); if (pr) pr.addEventListener("click", () => this.reloadPool());
    const pa = $("#admPoolAdd"); if (pa) pa.addEventListener("click", () => this.addAccount());

    const cx = $("#admCodexConnect"); if (cx) cx.addEventListener("click", () => this.oauthStart("codex", "codex"));
    const ir = $("#admIdReload"); if (ir) ir.addEventListener("click", () => this.loadIdentity());
    const ipair = $("#admIdPair"); if (ipair) ipair.addEventListener("click", () => this.pairMint());
    const is = $("#admIdShowRev"); if (is) is.addEventListener("change", () => { this.identShowRev = is.checked; this.renderIdentity(); });
    const kn = $("#admKeyNew"); if (kn) kn.addEventListener("click", () => this.openKeyEditor(null));
    const kr = $("#admKeysReload"); if (kr) kr.addEventListener("click", () => this.loadKeys());
    const ks = $("#admKeysShowRev"); if (ks) ks.addEventListener("change", () => { this.keysShowRev = ks.checked; this.renderKeys(); });
    const vr = $("#admVpnReload"); if (vr) vr.addEventListener("click", () => this.loadVpns());
    const vmr = $("#admVmsReload"); if (vmr) vmr.addEventListener("click", () => this.loadVms());
    const ps = $("#admShutdown"); if (ps) ps.addEventListener("click", () => this.powerAction("poweroff"));
    const pb = $("#admReboot"); if (pb) pb.addEventListener("click", () => this.powerAction("reboot"));
    $$(".adm-tab").forEach(b => b.addEventListener("click", () => this.switchTab(b.getAttribute("data-atab"))));
    const fsr = $("#admFsReload"); if (fsr) fsr.addEventListener("click", () => this.loadFairshare());
    this.wireAccounts();
  },
  show() {
    this.switchTab(localStorage.getItem("pp-adm-tab") || "overview");
    this.loadReserved(); this.loadMail(); this.loadPool(); this.loadAdvisor(); this.loadKeys(); this.loadIdentity(); this.loadVpns(); this.loadVms(); this.loadFairshare(); this.refresh();
    this.timer = setInterval(() => { if (Router.cur === "admin") this.refresh(); }, 3000);
  },
  hide() { if (this.timer) { clearInterval(this.timer); this.timer = null; } },
  forbidden() {
    const nav = $("#navAdmin"); if (nav) nav.hidden = true;
    this.hide(); delete LENS_TITLE.admin;
    if (Router.cur === "admin") Router.go("start");
  },
  refresh() { this.loadOverview(); this.loadPool(); this.loadAdvisor(); this.loadVpns(); this.loadVms(); if (!this.hold) this.loadUsers(); },

  switchTab(name) {
    this._atab = name;
    $$(".adm-tab").forEach(b => b.classList.toggle("on", b.getAttribute("data-atab") === name));
    $$(".adm-panel").forEach(p => { p.hidden = p.getAttribute("data-apanel") !== name; });
    localStorage.setItem("pp-adm-tab", name);
  },

  _sel(opts, cur, onch) {
    const s = el("select", { class: "fs-sel" });
    opts.forEach(o => { const op = el("option", { value: o.v, text: o.t }); if (String(o.v) === String(cur)) op.selected = true; s.appendChild(op); });
    s.addEventListener("change", () => onch(s.value));
    return s;
  },
  async loadFairshare() {
    const box = $("#admFsTable"), kpiEl = $("#admFsKpi"); if (!box) return;
    const d = await aget("/api/admin/fairshare");
    if (!d || d._forbidden || d._neterr) return;
    if (!d.ok) { box.textContent = ""; box.appendChild(el("div", { class: "empty", text: "Fairshare nicht verfügbar (pnd nicht erreichbar?)." })); return; }
    if (kpiEl) {
      const k = d.kpi || {}; kpiEl.textContent = "";
      const chip = (big, lab) => el("div", { class: "adm-fs-chip" }, [el("span", { class: "fsc-big", text: String(big) }), el("span", { class: "fsc-lab", text: lab })]);
      kpiEl.appendChild(chip(k.accounts != null ? k.accounts : "–", "Konten"));
      kpiEl.appendChild(chip((k.util_pct != null ? k.util_pct + "%" : "–"), "Auslastung · " + (k.load1 != null ? k.load1 : "?") + "/" + (k.cpu_count || "?") + " Kerne"));
      kpiEl.appendChild(chip(k.throttled || 0, "gedrosselt"));
      kpiEl.appendChild(chip(k.suspended || 0, "gesperrt"));
      if (k.backlog_total_s) kpiEl.appendChild(chip(Math.round(k.backlog_total_s) + "s", "Backlog gesamt"));
    }
    const rows = d.rows || []; box.textContent = "";
    if (!rows.length) { box.appendChild(el("div", { class: "empty", text: "Keine aktiven Konten." })); return; }
    box.appendChild(el("div", { class: "fs-row fs-head" }, [
      el("span", { class: "fs-c-acct", text: "Konto" }),
      el("span", { class: "fs-c-share", text: "Anteil · Fair-Faktor" }),
      el("span", { class: "fs-c-preset", text: "Profil" }),
      el("span", { class: "fs-c-weight", text: "Gewicht" }),
      el("span", { class: "fs-c-sess", text: "Sessions" }),
      el("span", { class: "fs-c-submit", text: "Einreichen" }),
    ]));
    rows.forEach(r => box.appendChild(this.fsRow(r)));
  },
  fsRow(r) {
    const principal = r.principal || r.account;
    const F = r.fair_factor != null ? r.fair_factor : 1;
    const pct = Math.round((r.norm_share || 0) * 100);
    const fill = el("div", { class: "fs-bar-fill " + (F < 0.34 ? "lo" : (F < 0.67 ? "mid" : "hi")), style: "width:" + Math.max(3, Math.round(F * 100)) + "%" });
    const share = el("div", { class: "fs-c-share" }, [
      el("div", { class: "fs-share-num", text: pct + "% Anteil · F " + (F != null ? F.toFixed(2) : "?") }),
      el("div", { class: "fs-bar" }, [fill]),
      (r.backlog_s ? el("div", { class: "fs-backlog muted", text: "Backlog " + Math.round(r.backlog_s) + "s · " + (r.rows || 0) + " Jobs" }) : null),
    ]);
    const preset = this._sel([
      { v: "", t: "— Profil wählen —" },
      { v: "guest-filler", t: "Gast · Füller (Gew. 1)" },
      { v: "standard", t: "Standard (Gew. 4)" },
      { v: "trusted-batch", t: "Vertraut (Gew. 16)" },
      { v: "owner-exclusive", t: "Owner · exklusiv (Gew. 64)" },
    ], r.qos_preset || "", (v) => { if (v) this.fsApplyPreset(principal, v); });
    const weight = this._sel([1, 2, 4, 8, 16, 32, 64].map(w => ({ v: w, t: "Gewicht " + w })), r.weight || 1, (v) => this.fsSetPolicy(principal, { weight: parseInt(v, 10) }));
    const sess = this._sel([{ v: 0, t: "Sessions ∞" }, { v: 1, t: "max 1" }, { v: 2, t: "max 2" }, { v: 4, t: "max 4" }, { v: 8, t: "max 8" }, { v: 16, t: "max 16" }],
      r.max_sessions || 0, (v) => this.fsSetPolicy(principal, { max_sessions: parseInt(v, 10) }));
    const submit = this._sel([{ v: "1", t: "erlaubt" }, { v: "0", t: "⛔ gesperrt" }], r.submit_enabled ? "1" : "0", (v) => this.fsSubmit(principal, v === "1"));
    const acct = el("div", { class: "fs-c-acct" }, [
      el("div", { class: "fs-acct-name ellipsis", text: principal }),
      (r.block_reason ? el("div", { class: "fs-block", text: "⛔ " + r.block_reason }) : null),
    ]);
    return el("div", { class: "fs-row" + (r.submit_enabled ? "" : " blocked") }, [
      acct, share,
      el("div", { class: "fs-c-preset" }, [preset]),
      el("div", { class: "fs-c-weight" }, [weight]),
      el("div", { class: "fs-c-sess" }, [(r.live_sessions != null ? el("div", { class: "fs-live", text: r.live_sessions + " aktiv" }) : null), sess]),
      el("div", { class: "fs-c-submit" }, [submit]),
    ]);
  },
  async fsSetPolicy(principal, patch) { this._fsAfter(await apost("/api/admin/policy", Object.assign({ action: "set-policy", target_principal: principal, reason: "admin-ui" }, patch))); },
  async fsApplyPreset(principal, preset) { this._fsAfter(await apost("/api/admin/policy", { action: "apply-preset", target_principal: principal, preset, reason: "admin-ui" })); },
  async fsSubmit(principal, enabled) { this._fsAfter(await apost("/api/admin/policy", { action: enabled ? "submit-resume" : "submit-suspend", target_principal: principal, reason: "admin-ui" })); },
  _fsAfter(r) { toast(r && r.ok !== false ? "✓ übernommen — Nutzer benachrichtigt" : ("Fehler" + (r && r.error ? ": " + r.error : ""))); setTimeout(() => this.loadFairshare(), 400); },

  async loadVpns() {
    const box = $("#admVpn"); if (!box) return;
    const d = await jget("/api/vpns");
    if (!d || d._neterr || d._auth === false) return;
    const vpns = (d && d.vpns) || [];
    box.textContent = "";
    if (!vpns.length) { box.appendChild(el("div", { class: "empty", text: "Keine VPN-Zugänge eingerichtet." })); return; }
    vpns.forEach(v => {
      const up = v.status === "up" || !!v.active;
      const unknown = v.status === "unknown" || v.status == null;
      const row = el("div", { class: "vpn-row" + (up ? " up" : "") });
      row.appendChild(el("span", { class: "vpn-dot " + (up ? "on" : (unknown ? "unk" : "off")),
        title: up ? "aktiv" : (unknown ? "Status unbekannt" : "nicht verbunden") }));
      const sub = [v.endpoint || v.gateway || "", v.user ? "User " + v.user : "", v.purpose || ""].filter(Boolean).join(" · ");
      row.appendChild(el("div", { class: "vpn-body" }, [
        el("div", { class: "vpn-title", text: (v.name || v.id) }),
        el("div", { class: "vpn-sub muted", text: sub }),
      ]));
      const tags = el("div", { class: "vpn-tags" });
      tags.appendChild(el("span", { class: "vpn-state " + (up ? "on" : (unknown ? "unk" : "off")),
        text: up ? "AKTIV" : (unknown ? "?" : "AUS") }));
      if (v.operator_gated) tags.appendChild(el("span", { class: "vpn-op", title: "Verbinden nur durch Operator (2FA am Handy)", text: "🔒 Operator" }));
      row.appendChild(tags);
      box.appendChild(row);
    });
  },

  async loadReserved() {
    const d = await aget("/api/admin/reserved_cores");
    if (!d || d._forbidden || d._neterr) return;
    if (d.nproc != null) this.nproc = d.nproc;
    if (d.reserved != null) this.reserved = d.reserved;
    this.renderStepper(d.tenant_cap_pct);
  },
  async stepReserved(delta) {
    const max = Math.max(0, (this.nproc || 1) - 1);
    const n = Math.min(max, Math.max(0, (this.reserved || 0) + delta));
    if (n === this.reserved) return;
    const d = await apost("/api/admin/reserved_cores", { n });
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    this.reserved = (d.reserved != null) ? d.reserved : n;
    if (d.nproc != null) this.nproc = d.nproc;
    this.renderStepper(d.tenant_cap_pct);
    if (d.msg) toast(d.msg);
  },
  renderStepper(capPct) {
    if (capPct != null) this.capPct = capPct;
    const max = Math.max(0, (this.nproc || 1) - 1);
    $("#admResN").textContent = this.reserved;
    $("#admResMinus").disabled = this.reserved <= 0;
    $("#admResPlus").disabled = this.reserved >= max;
    $("#admResCap").textContent = (this.nproc || "?") + " Kerne − " + this.reserved + " → " + this.capPct + "%";
  },

  async loadVms() {
    const d = await aget("/api/admin/ram");
    if (!d || d._forbidden || d._neterr) return;
    if (d.ok && d.ram) this.renderVms(d.ram);
  },
  renderVms(m) {
    const box = $("#admVms"); if (!box) return; box.textContent = "";
    const gb = (mb) => ((mb || 0) / 1024).toFixed(1);
    const pct = Math.max(0, Math.min(100, m.used_pct != null ? m.used_pct
      : (m.budget_mb ? 100 * m.committed_mb / m.budget_mb : 0)));
    const full = !(m.can_session || m.can_screen);
    const bar = el("div", { class: "vm-barwrap" });
    const fill = el("div", { class: "vm-barfill" + (full || pct >= 90 ? " full" : "") });
    fill.style.width = pct.toFixed(1) + "%";
    bar.appendChild(fill); box.appendChild(bar);
    box.appendChild(el("div", { class: "vm-budline",
      text: gb(m.committed_mb) + " / " + gb(m.budget_mb) + " GB Gast-RAM belegt · " + gb(m.free_budget_mb)
        + " GB frei · " + m.count + " VM" + (m.count === 1 ? "" : "s")
        + "  (Maschine " + gb(m.total_mb) + " GB − " + gb(m.host_reserve_mb) + " GB Host-Reserve)" }));
    if (this.fleet && this.fleet.nodes > 1) {
      box.appendChild(el("div", { class: "vm-budline muted",
        text: "Fleet gesamt: " + Math.round((this.fleet.mem_total_mb || 0) / 1024) + " GB RAM - "
          + this.fleet.nproc + " Kerne / " + this.fleet.nodes + " Nodes (nested-VM-Budget oben gilt nur fuer diese Box)" }));
    }
    box.appendChild(el("div", { class: "vm-hint" + (full ? " warn" : ""),
      text: full ? "Budget voll — für eine weitere VM zuerst unten eine stoppen."
        : ("Platz für eine weitere " + (m.can_screen ? "GUI-Sitzung (2 GB)" : "Sitzung (1,5 GB)") + ".") }));
    if (!m.running || !m.running.length) {
      box.appendChild(el("div", { class: "empty", text: "Keine nested VM läuft gerade." })); return;
    }
    m.running.forEach(v => {
      const isScreen = v.kind === "screen";
      const row = el("div", { class: "vm-row" });
      row.appendChild(el("span", { class: "vm-kind " + (isScreen ? "screen" : "session"),
        text: isScreen ? "GUI" : "Sitzung" }));
      const sub = [v.owner ? "User " + v.owner : "", v.session || "", "PID " + v.pid].filter(Boolean).join(" · ");
      row.appendChild(el("div", { class: "vm-body" }, [
        el("div", { class: "vm-title", text: v.label || v.id }),
        el("div", { class: "vm-sub muted", text: sub })]));
      row.appendChild(el("span", { class: "vm-mem tnum", text: gb(v.mem_mb) + " GB" }));
      const btn = el("button", { class: "btn sm vm-stop", text: "Stoppen" });
      btn.addEventListener("click", () => this.stopVm(v.id, v.label || v.id, isScreen));
      row.appendChild(btn); box.appendChild(row);
    });
  },
  async stopVm(id, label, isScreen) {
    const note = isScreen ? "Die GUI-Sitzung wird beendet."
      : "Die Sitzung wird heruntergefahren — das Gespräch bleibt gespeichert (später fortsetzbar).";
    if (!window.confirm("VM stoppen: " + label + "\n\n" + note)) return;
    const d = await apost("/api/admin/ram/stop", { id });
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    toast(d.ok ? "VM gestoppt — RAM frei" : ("Stopp fehlgeschlagen" + (d.error ? ": " + d.error : "")));
    if (d.ram) this.renderVms(d.ram); else this.loadVms();
  },

  async powerAction(mode) {
    const label = mode === "reboot" ? "Neu starten" : "Herunterfahren";
    const status = $("#admPowerStatus");
    const d = await apost("/api/admin/shutdown", { mode });
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    if (d.ok !== false || !d.need_approval) {
      toast(d.error || "Unerwartete Antwort — nichts passiert.");
      if (status) status.textContent = d.error || "";
      return;
    }
    let totp = null;
    if (d.totp_required) {
      totp = window.prompt(label + " — bitte den Handy-Code (2FA) eingeben:\n\n" + (d.message || ""));
      if (totp === null || !totp.trim()) { toast("Abgebrochen — nichts passiert."); return; }
    } else if (!window.confirm(label + ": wirklich?\n\nAlle Dienste und Sitzungen werden geordnet " +
               "beendet" + (mode === "reboot" ? ", danach startet die Box neu." : ", danach ist die Box AUS.") +
               "\n\n" + (d.message || ""))) {
      toast("Abgebrochen — nichts passiert."); return;
    }
    const c = await apost("/api/admin/shutdown", { mode, approval: d.approval, totp });
    if (!c || c._forbidden) return;
    if (c._neterr) { toast("Netzfehler"); return; }
    if (c.ok) {
      toast(c.message || (label + " eingeleitet"));
      if (status) status.textContent = "⏻ " + (c.message || (label + " eingeleitet")) +
        " Diese Seite verliert gleich die Verbindung.";
      const sb = $("#admShutdown"), rb = $("#admReboot");
      if (sb) sb.disabled = true; if (rb) rb.disabled = true;
    } else {
      toast(c.error || "Fehlgeschlagen — nichts passiert.");
      if (status) status.textContent = c.error || "";
    }
  },

  async loadMail() {
    const d = await aget("/api/admin/mail/config");
    if (!d || d._forbidden || d._neterr) return;
    this.renderMail(d);
  },
  renderMail(d) {
    const box = $("#admMailStatus"); if (!box) return; box.textContent = "";
    const ok = !!d.configured;
    box.appendChild(el("div", { class: "adm-mail-row" }, [
      el("span", { class: "pill role-" + (ok ? "owner" : "user"), text: ok ? "aktiv" : "nicht konfiguriert" }),
      el("span", { class: "muted", text: ok ? ("Absender: " + (d.sender || "?")) : "Mailjet-Zugang fehlt (System-Ebene)" })
    ]));
  },
  async sendMailTest() {
    const to = ($("#admMailTo").value || "").trim();
    if (!to) { toast("Empfänger fehlt"); return; }
    const btn = $("#admMailSend"); btn.disabled = true;
    const d = await apost("/api/admin/mail/test", { to });
    btn.disabled = false;
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    toast(d.ok ? ("Gesendet an " + to) : ("Fehlgeschlagen: " + (d.detail || d.error || "?")));
  },

  oauth: null,
  fmtDur(s) {
    s = Math.max(0, s | 0);
    if (s < 90) return s + "s";
    if (s < 5400) return Math.round(s / 60) + " min";
    if (s < 172800) return Math.round(s / 3600) + " h";
    return Math.round(s / 86400) + " d";
  },
  fmtDate(ts) {
    if (!ts) return "—";
    const dt = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
    return p(dt.getDate()) + "." + p(dt.getMonth() + 1) + "." + dt.getFullYear();
  },
  usageBar(pct) {
    const p = Math.max(0, Math.min(100, pct | 0));
    const cls = p >= 90 ? "bad" : p >= 70 ? "warn" : "good";
    return el("span", { class: "usebar " + cls }, [el("i", { style: "width:" + p + "%" })]);
  },
  async loadPool() {
    const d = await aget("/api/admin/llm/pool");
    if (!d || d._forbidden || d._neterr) return;

    try { const g = await aget("/api/admin/llm/gemini"); if (g && g.ok) d._gemini = g; } catch (e) {}
    try { const e2 = await aget("/api/admin/llm/endpoints"); if (e2 && e2.ok) { d._endpoints = e2.providers || []; this._epPresets = e2.presets || []; } } catch (e) {}
    this.renderPool(d);
  },
  renderPool(d) {
    const box = $("#admPoolBody"); if (!box) return;

    if (box.contains(document.activeElement) && document.activeElement !== document.body) return;
    box.textContent = "";
    if (d.ok === false) { box.appendChild(el("div", { class: "empty", text: d.msg || "nicht verfügbar" })); return; }
    box.appendChild(el("div", { class: "adm-pool-head" }, [
      el("span", { class: "pill role-" + (d.multi ? "owner" : "user"), text: d.multi ? "Pool aktiv" : "Einzelkonto" }),
      el("span", { class: "muted", text: (d.enabled || 0) + " Konten aktiv · bis zu " + (d.max_concurrent || "?") + " gleichzeitige Aufrufe (Kontenzahl unbegrenzt)" }),
      el("button", { class: "btn sm ghost", text: "📊 Auslastung abrufen",
        title: "Live-5h/7d-% je Konto vom Anbieter holen (aktualisiert die Balken)",
        onclick: (e) => this.refreshUsage(e.target) }),
    ]));

    {
      const swInp = el("input", { type: "number", min: "1", max: "100", class: "adm-sw-inp",
                                  value: d.switch_pct != null ? String(d.switch_pct) : "" , placeholder: "aus" });
      const row = el("div", { class: "adm-pool-route muted" }, [
        el("span", { text: d.preferred ? ("Abgebucht wird: ⭐ " + d.preferred) : "Abgebucht wird: Automatik (meiste Reserve)" }),
        d.preferred ? el("button", { class: "btn sm ghost", text: "Automatik", title: "Bevorzugung aufheben",
          onclick: async () => { await apost("/api/admin/llm/pool", { action: "prefer", id: "" }); this.loadPool(); } }) : null,
        el("span", { text: " · Auto-Wechsel ab " }), swInp, el("span", { text: "% (5h)" }),
        el("button", { class: "btn sm ghost", text: "OK", title: "Konten über dieser 5h-Auslastung werden gemieden, solange ein anderes darunter liegt",
          onclick: async () => {
            const r = await apost("/api/admin/llm/pool", { action: "switch_pct", value: swInp.value });
            toast(r && r.ok ? (r.switch_pct ? "Auto-Wechsel ab " + r.switch_pct + "%" : "Auto-Wechsel aus") : "Fehler");
            this.loadPool();
          } }),
      ]);
      box.appendChild(row);
    }
    (d.accounts || []).forEach(a => {
      const nfo = a.info || {};
      const s5 = (a.five_hour && a.five_hour.status) || "";
      const cls = (a.cooling || /reject|block|exceed|reach|throttl/.test(s5)) ? "bad"
                : /warn|near|approach/.test(s5) ? "warn" : (a.logged_in ? "good" : "off");
      const title = nfo.display_name || nfo.email || a.id;
      const head = el("div", { class: "adm-pool-id" }, [
        el("span", { class: "adm-pool-name", text: title }),
        nfo.email && nfo.email !== title ? el("span", { class: "muted adm-pool-email", text: nfo.email }) : null,
        el("span", { class: "adm-pool-login " + (a.logged_in ? "in" : "out"),
                     text: a.logged_in ? "angemeldet" : "kein Login" }),
        a.provider && a.provider !== "claude" ? el("span", { class: "pill role-user", text: a.provider }) : null,
        a.enabled ? null : el("span", { class: "muted", text: "· aus" }),
      ]);
      const sub = el("div", { class: "adm-pool-sub muted tnum" }, [
        el("span", { text: (nfo.subscription || "—") + (nfo.tier ? (" · " + nfo.tier.replace("default_claude_", "")) : "") }),
        el("span", { text: "Erneuerung: " + this.fmtDate(nfo.next_renewal) }),
      ]);

      const usage = el("div", { class: "adm-pool-usage tnum" });
      if (nfo.five_hour_pct != null) {
        usage.appendChild(el("span", { class: "adm-use", text: "5h" }));
        usage.appendChild(this.usageBar(nfo.five_hour_pct));
        usage.appendChild(el("span", { class: "muted", text: Math.round(nfo.five_hour_pct) + "%" }));
      }
      if (nfo.seven_day_pct != null) {
        usage.appendChild(el("span", { class: "adm-use", text: "7d" }));
        usage.appendChild(this.usageBar(nfo.seven_day_pct));
        usage.appendChild(el("span", { class: "muted", text: Math.round(nfo.seven_day_pct) + "%" }));
      }
      if (nfo.five_hour_pct == null && nfo.seven_day_pct == null) {
        const auslastung = nfo.usage_stale
          ? ("Auslastung unbekannt" + (nfo.usage_at ? " · Stand " + this.fmtDate(nfo.usage_at) : ""))
          : (a.cooling ? ("gesperrt · frei in " + this.fmtDur(a.cooldown_s)) : (s5 || (a.logged_in ? "bereit" : "—")));
        usage.appendChild(el("span", { class: "muted", text: auslastung }));
      } else if (nfo.usage_at) {
        usage.appendChild(el("span", { class: "muted", text: "· Stand " + this.fmtDate(nfo.usage_at) }));
      }
      const meta = el("div", { class: "adm-pool-meta muted tnum" }, [
        el("span", { text: "inflight " + (a.inflight || 0) }),
        el("span", { text: (a.calls || 0) + " calls · " + (a.errors || 0) + " err · " + (a.rate_limited || 0) + " rl" }),
        a.cooling ? el("span", { class: "adm-cool", text: "Cooldown " + this.fmtDur(a.cooldown_s) }) : null,
      ]);
      const acts = el("div", { class: "adm-pool-acts" }, [
        a.logged_in ? el("button", { class: "btn sm ghost", text: d.preferred === a.id ? "⭐ bevorzugt" : "⭐ nutzen",
          title: d.preferred === a.id ? "Dieses Konto wird gerade bevorzugt abgebucht" : "Ab sofort bevorzugt von diesem Konto abbuchen",
          onclick: async () => { await apost("/api/admin/llm/pool", { action: "prefer", id: d.preferred === a.id ? "" : a.id }); this.loadPool(); } }) : null,
        el("button", { class: "btn sm ghost", text: a.logged_in ? "Neu anmelden" : "Anmelden (OAuth)",
          onclick: () => this.oauthStart(a.id, a.provider || "claude") }),
        a.logged_in ? el("button", { class: "btn sm ghost", text: a.enabled ? "Deaktivieren" : "Aktivieren",
          onclick: () => this.poolAction(a.enabled ? "disable" : "enable", a.id) }) : null,
        a.cooling ? el("button", { class: "btn sm ghost", text: "Cooldown lösen",
          onclick: () => this.poolAction("clear_cooldown", a.id) }) : null,
        a.id !== "primary" ? el("button", { class: "btn sm ghost danger", text: "Entfernen",
          onclick: () => { if (window.confirm("Konto '" + a.id + "' aus dem Pool entfernen?")) this.poolAction("remove", a.id); } }) : null,
      ]);
      box.appendChild(el("div", { class: "adm-pool-row " + cls }, [head, sub, usage, meta, acts]));
    });

    {
      const FIRST = ["claude", "codex", "ollama", "gemini"];
      const badge = (id) => FIRST.indexOf(String(id || "").toLowerCase()) < 0
        ? el("span", { class: "pill", title: "kein first-class-Anbieter — als ungetestet gekennzeichnet", text: "· ungetestet" }) : null;
      const g = d._gemini;
      const eps = d._endpoints || [];
      if (g || eps.length) box.appendChild(el("div", { class: "adm-pool-eph muted", text: "🧩 Endpoint-Gehirne (API-Key / lokal)" }));
      if (g) {
        const grow = el("div", { class: "prov-row col" });
        grow.appendChild(el("div", { class: "prov-head" }, [el("b", { text: "Gemini (Google)" }),
          el("span", { class: g.connected ? "pill role-user" : "muted", text: g.connected ? "verbunden ✓" : "nicht verbunden" }),
          el("span", { class: "muted", text: g.installed ? "CLI ✓" : "CLI fehlt" }),
          (g.models || []).length ? el("span", { class: "muted", text: g.models.length + " Modelle" }) : null]));
        const gi = el("input", { type: "password", placeholder: g.connected ? "API-Key gesetzt (neu eingeben = ersetzen)" : "API-Key aus Google AI Studio", autocomplete: "off", spellcheck: "false" });
        grow.appendChild(el("div", { class: "prov-form" }, [gi,
          el("button", { class: "btn sm", text: g.connected ? "Ersetzen" : "Verbinden", onclick: async () => {
            const key = (gi.value || "").trim(); if (!key) { toast("API-Key fehlt"); return; }
            const r = await apost("/api/admin/llm/gemini", { action: "set_key", api_key: key });
            if (r && r.ok) { toast(r.connected ? "Gemini verbunden ✓" : "gespeichert"); gi.value = ""; gi.blur(); this.loadPool(); }
            else toast((r && r.error) || "Fehler", true);
          } }),
          g.connected ? el("button", { class: "btn sm ghost", text: "Trennen", onclick: async () => {
            const r = await apost("/api/admin/llm/gemini", { action: "clear" }); if (r && r.ok) { toast("Gemini getrennt"); this.loadPool(); }
          } }) : null]));
        box.appendChild(grow);
      }
      eps.forEach(p => box.appendChild(Settings.endpointRow.call(Settings, p, badge)));
    }
  },
  async refreshUsage(btn) {
    const t0 = btn ? btn.textContent : ""; if (btn) { btn.disabled = true; btn.textContent = "… holt"; }
    const d = await apost("/api/admin/llm/pool", { action: "refresh_usage" });
    if (btn) { btn.disabled = false; btn.textContent = t0; }
    if (!d || d._forbidden || d._neterr) { toast("Netzfehler", true); return; }
    if (!d.ok) { toast(d.msg || "Fehlgeschlagen", true); return; }
    const bad = (d.results || []).filter(r => r.status && r.status !== "ok");
    toast("Auslastung aktualisiert: " + (d.updated || 0) + " Konten"
      + (bad.length ? " · " + bad.length + " ohne Wert" : ""));
    this.loadPool();
  },
  async reloadPool() {
    const d = await apost("/api/admin/llm/pool", { action: "reload" });
    if (d && d._neterr) { toast("Netzfehler"); return; }
    if (d && d.ok) { toast("Pool neu geladen (" + (d.accounts != null ? d.accounts : "?") + " Konten)"); this.loadPool(); }
  },
  async poolAction(action, id) {
    const d = await apost("/api/admin/llm/pool", { action, id });
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    if (d.ok) { toast(action + ": " + id); this.loadPool(); }
    else toast("Fehlgeschlagen: " + (d.msg || "?"));
  },
  async addAccount() {

    const p = $("#admOauthPanel"); if (!p) return;
    if (this._oauthTimer) { toast("Erst die laufende Anmeldung abschließen oder abbrechen"); return; }
    this.oauth = null; this._oauthBuilt = false;
    p.hidden = false; p.textContent = "";
    let provs = [];
    try { const d = await jget("/api/llm/providers"); provs = (d && d.providers) || []; } catch (e) {}
    const TESTED = ["claude", "codex", "ollama", "gemini"];
    const opts = provs.map(pr => el("option", { value: "oauth:" + pr.id,
      text: pr.label + (pr.installed ? "" : " — CLI fehlt")
        + (TESTED.indexOf(String(pr.id || "").toLowerCase()) < 0 ? " · ungetestet" : ""),
      disabled: pr.installed ? null : true }));
    opts.push(el("option", { value: "key:gemini", text: "Gemini (Google) — API-Key" }));
    opts.push(el("option", { value: "key:endpoint", text: "OpenAI-kompatibler Endpoint (Ollama, Mistral, …)" }));
    const sel = el("select", { title: "Anbieter für das neue Konto" }, opts);
    const ni = el("input", { placeholder: "Kurzname (z. B. max-4)", spellcheck: "false" });
    p.appendChild(el("div", { class: "adm-oauth-head" }, [
      el("strong", { text: "Neues Konto — Anbieter wählen" }),
      el("span", { class: "muted", text: "OAuth wie auch API-Key/Endpoint: alles direkt hier" }),
    ]));
    p.appendChild(el("div", { class: "prov-form" }, [sel, ni,
      el("button", { class: "btn sm", text: "Weiter", onclick: () => {
        const v = sel.value || "";

        if (v === "key:gemini") { this.keyFormGemini(p); return; }
        if (v === "key:endpoint") { this.keyFormEndpoint(p); return; }
        const id = (ni.value || "").trim();
        if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$/.test(id)) { toast("Kurzname fehlt oder ungültig (z. B. max-4)"); return; }
        this.oauthStart(id, v.slice("oauth:".length));
      } }),
      el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: () => { p.hidden = true; p.textContent = ""; } }),
    ]));
  },
  keyFormGemini(p) {

    p.textContent = "";
    p.appendChild(el("div", { class: "adm-oauth-head" }, [el("strong", { text: "Gemini (Google) verbinden" }),
      el("span", { class: "muted", text: "API-Key aus Google AI Studio (aistudio.google.com/app/apikey)" })]));
    const gi = el("input", { type: "password", placeholder: "API-Key", autocomplete: "off", spellcheck: "false" });
    p.appendChild(el("div", { class: "prov-form" }, [gi,
      el("button", { class: "btn sm", text: "Verbinden", onclick: async () => {
        const key = (gi.value || "").trim(); if (!key) { toast("API-Key fehlt"); return; }
        const r = await apost("/api/admin/llm/gemini", { action: "set_key", api_key: key });
        if (r && r.ok) { toast(r.connected ? "Gemini verbunden ✓" : "gespeichert"); p.hidden = true; p.textContent = ""; this.loadPool(); }
        else toast((r && r.error) || "Fehler", true);
      } }),
      el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: () => { p.hidden = true; p.textContent = ""; } })]));
    gi.focus();
  },
  async keyFormEndpoint(p) {

    p.textContent = "";
    p.appendChild(el("div", { class: "adm-oauth-head" }, [el("strong", { text: "Endpoint-Anbieter anlegen" }),
      el("span", { class: "muted", text: "Getestet: Claude, Codex, Ollama, Gemini — alles andere ausdrücklich ungetestet" })]));
    if (!this._epPresets) {
      try { const e2 = await aget("/api/admin/llm/endpoints"); if (e2 && e2.ok) this._epPresets = e2.presets || []; } catch (e) {}
    }
    const pHint = el("div", { class: "muted" });
    const pre = el("select", { title: "Vorlage: bekannte Anbieter mit fertiger Base-URL" },
      [el("option", { value: "", text: "Vorlage wählen … (oder Felder selbst füllen)" })]);
    ((this._epPresets) || []).forEach(t => pre.appendChild(el("option", { value: t.id, text: t.name + " · ungetestet" })));
    const ni2 = el("input", { placeholder: "id (klein, z. B. mistral)", spellcheck: "false" });
    const nn = el("input", { placeholder: "Anzeigename (z. B. Mistral)", spellcheck: "false" });
    const nu = el("input", { placeholder: "Base-URL (https://… oder http://host:port)", spellcheck: "false" });
    const nk = el("input", { type: "password", placeholder: "API-Key (optional — Ollama braucht keinen)", autocomplete: "off" });
    pre.addEventListener("change", () => {
      const t = ((this._epPresets) || []).find(x => x.id === pre.value);
      pHint.textContent = "";
      if (!t) return;
      ni2.value = t.id; nn.value = t.name; nu.value = t.base_url;
      pHint.textContent = "API-Key erstellen: ";
      pHint.appendChild(el("a", { href: t.key_console, target: "_blank", rel: "noopener", text: t.key_console }));
    });
    const disc = el("button", { class: "btn sm ghost", text: "🔎 Ollama im LAN finden", title: "Scannt das LAN nach Ollama-Servern (Port 11434)", onclick: async () => {
      disc.disabled = true; const t0 = disc.textContent; disc.textContent = "… scanne LAN";
      let r; try { r = await aget("/api/admin/llm/ollama/discover"); } catch (e) { r = null; }
      disc.disabled = false; disc.textContent = t0;
      const hosts = (r && r.ok && r.found) || [];
      if (!hosts.length) { toast("Kein Ollama im LAN gefunden (Port 11434)"); return; }
      const h = hosts[0];
      ni2.value = "ollama"; nn.value = "Ollama (" + (h.host || "?") + ")"; nu.value = h.base_url || ("http://" + h.host + ":11434");
      pHint.textContent = hosts.length + " Ollama-Server gefunden: " + hosts.map(x => x.host + " (" + (x.models || []).length + " Modelle)").join(" · ");
      toast("Ollama gefunden ✓ — Felder ausgefüllt, unten „Anlegen“ klicken");
    } });
    p.appendChild(el("div", { class: "prov-form" }, [pre, disc]));
    p.appendChild(pHint);
    p.appendChild(el("div", { class: "prov-form" }, [ni2, nn, nu, nk,
      el("button", { class: "btn sm", text: "Anlegen", onclick: async () => {
        const entry = { id: (ni2.value || "").trim().toLowerCase(), name: (nn.value || "").trim(),
          base_url: (nu.value || "").trim(), api_key: (nk.value || "").trim(), discovery: "openai" };
        const r = await apost("/api/admin/llm/endpoints", { action: "save", entry });
        if (r && r.ok) { toast("Anbieter angelegt ✓ — jetzt unten testen & Modell wählen"); p.hidden = true; p.textContent = ""; this.loadPool(); }
        else toast((r && r.error) || "Fehler", true);
      } }),
      el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: () => { p.hidden = true; p.textContent = ""; } })]));
  },

  async oauthStart(id, provider) {
    if (this._oauthTimer) clearInterval(this._oauthTimer);
    this._oauthBuilt = false;
    this.oauth = { id, provider: provider || "claude", phase: "starting", lines: [] };
    this.renderOauth();
    const d = await apost("/api/admin/llm/login/start", { id, provider: provider || "claude" });
    if (!d || d._forbidden) { this.oauth = null; this.renderOauth(); return; }
    if (!d.ok) { toast("Anmeldung konnte nicht gestartet werden: " + (d.detail || d.error || d.msg || "?")); this.oauth = null; this.renderOauth(); return; }
    this.oauth = { id, provider: provider || "claude", lane: d.session, phase: "läuft", lines: d.lines || [], url: d.url || "" };
    this.renderOauth();
    this._oauthTimer = setInterval(() => this.oauthPoll(), 1000);
  },
  async oauthPoll() {
    const o = this.oauth; if (!o || !o.lane) return;
    let p; try { p = await jpost("/api/admin/llm/login/poll", { session: o.lane }); } catch (e) { return; }
    if (!this.oauth || this.oauth.lane !== o.lane) return;
    if (!p || !p.ok) return;
    if (p.lines) o.lines = p.lines;
    if (p.url) o.url = p.url;
    this.renderOauth();
    if (p.connected === true) {
      clearInterval(this._oauthTimer); this._oauthTimer = null;
      if (p.usable === false) { o.phase = "fehler"; o.msg = "Anmeldung angenommen, aber das Konto kann NICHT antworten" + (p.verify_detail ? " — " + p.verify_detail : "") + "."; this.renderOauth(); return; }
      toast("Konto '" + o.id + "' angemeldet ✓" + (p.email ? " (" + p.email + ")" : ""));
      this.oauth = null; this.renderOauth(); this.loadPool();
    } else if (p.connected === false) {
      clearInterval(this._oauthTimer); this._oauthTimer = null;
      o.phase = "fehler"; o.msg = "Anmeldung nicht abgeschlossen" + (p.verify_detail ? " — " + p.verify_detail : "") + ". Details im Terminal oben.";
      this.renderOauth();
    }
  },
  async oauthSendCode() {
    const o = this.oauth; if (!o || !o.lane) return;
    const inp = $("#admOauthCode"); const code = (inp && inp.value || "").trim();
    if (!code) { toast("Code fehlt"); return; }
    const btn = $("#admOauthSend"); if (btn) btn.disabled = true;
    const d = await apost("/api/admin/llm/login/input", { session: o.lane, text: code, key: "enter" });
    if (btn) btn.disabled = false;
    if (!(d && d.ok)) toast("Code konnte nicht übergeben werden: " + ((d && (d.detail || d.msg)) || "?"));
  },
  async oauthCancel() {
    const o = this.oauth; if (!o) return;
    if (this._oauthTimer) clearInterval(this._oauthTimer);
    this._oauthTimer = null; this.oauth = null; this.renderOauth();
    if (o.lane) { try { await apost("/api/admin/llm/login/cancel", { session: o.lane }); } catch (e) {} }
    try { await apost("/api/admin/llm/oauth/cancel", { id: o.id }); } catch (e) {}
  },
  renderOauth() {

    const p = $("#admOauthPanel"); if (!p) return;
    if (!this.oauth) { p.hidden = true; p.textContent = ""; this._oauthBuilt = false; return; }
    if (!this._oauthBuilt || !p.firstChild) {
      this._oauthBuilt = true;
      p.hidden = false; p.textContent = "";
      const o = this.oauth;
      this._oPhase = el("span", { class: "pill role-user", text: o.phase || "…" });
      p.appendChild(el("div", { class: "adm-oauth-head" }, [
        el("strong", { text: "Anmeldung · Konto '" + o.id + "'" + (o.provider && o.provider !== "claude" ? " · " + o.provider : "") }),
        this._oPhase,
        el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: () => this.oauthCancel() }),
      ]));

      this._oTerm = el("pre", { class: "adm-oauth-term" });
      p.appendChild(this._oTerm);
      this._oLinkWrap = el("div", { class: "adm-oauth-btns" }, [el("span", { class: "muted", text: "🔄 Link wird abgerufen …" })]);
      p.appendChild(el("div", { class: "adm-oauth-steps" }, [
        el("div", { text: "1) Anmelde-Link öffnen — im Browser/Profil, wo das GEWÜNSCHTE Konto angemeldet ist (oder privates Fenster):" }),
        this._oLinkWrap,
        el("div", { text: "2) Nach der Anmeldung wird ein Code angezeigt. Diesen hier einfügen:" }),
        el("div", { class: "adm-oauth-code" }, [
          el("input", { type: "text", id: "admOauthCode", placeholder: "Code aus dem Anmelde-Fenster", autocomplete: "off", spellcheck: "false",
                        onkeydown: (e) => { if (e.key === "Enter") this.oauthSendCode(); } }),
          el("button", { class: "btn sm", id: "admOauthSend", text: "Bestätigen", onclick: () => this.oauthSendCode() }),
        ]),
      ]));
      this._oErr = el("div", { class: "adm-oauth-err" }); this._oErr.hidden = true;
      p.appendChild(this._oErr);
    }
    const o = this.oauth;
    if (this._oPhase) this._oPhase.textContent = o.phase || "…";
    if (this._oTerm) {

      const atBottom = this._oTerm.scrollTop + this._oTerm.clientHeight >= this._oTerm.scrollHeight - 12;
      this._oTerm.textContent = (o.lines || []).join("\n") || "Starte Anmelde-Lauf …";
      if (atBottom) this._oTerm.scrollTop = this._oTerm.scrollHeight;
    }
    if (o.url && this._oLinkWrap && !this._oLinkWrap._done) {
      this._oLinkWrap._done = true; this._oLinkWrap.textContent = "";
      this._oLinkWrap.appendChild(el("a", { class: "btn sm", text: "Anmelde-Link öffnen", href: o.url, target: "_blank", rel: "noopener" }));
      this._oLinkWrap.appendChild(el("button", { class: "btn sm ghost", text: "Link kopieren", onclick: () => { try { navigator.clipboard.writeText(o.url); toast("Link kopiert"); } catch (e) {} } }));
    }
    if (this._oErr) { this._oErr.hidden = !o.msg; this._oErr.textContent = o.msg ? "✗ " + o.msg : ""; }
  },

  async loadOverview() {
    const d = await aget("/api/admin/overview");
    if (!d || d._forbidden || d._neterr) return;
    if (d.tenant_cap_pct != null) this.capPct = d.tenant_cap_pct;
    if (d.nproc != null) this.nproc = d.nproc;
    this.fleet = d.fleet || null;
    this.boxMemMb = d.mem_total_mb || d.box_mem_mb || (d.pn_batch && d.pn_batch.mem_total_mb) || this.boxMemMb;
    this.renderOverview(d);
  },
  renderOverview(d) {
    const wrap = $("#admStats"); wrap.textContent = "";
    const pct = (v) => (v == null ? "–" : v + "%");

    const cores = (v) => (v == null ? "–" : (v / 100).toLocaleString("de", { maximumFractionDigits: 1 }) + " Kerne");
    const stat = (num, lbl) => el("div", { class: "adm-stat" }, [
      el("div", { class: "adm-num", text: String(num) }), el("div", { class: "adm-lbl", text: lbl })
    ]);
    wrap.appendChild(stat(d.nproc != null ? d.nproc : "–", "Kerne"));
    if (d.fleet && d.fleet.nodes > 1) {
      wrap.appendChild(stat(d.fleet.nproc, "Kerne (Fleet, " + d.fleet.nodes + " Nodes)"));
      wrap.appendChild(stat(Math.round((d.fleet.mem_total_mb || 0) / 1024) + " GB", "RAM (Fleet)"));
    }
    wrap.appendChild(stat(d.admin_reserved_cores != null ? d.admin_reserved_cores : "–", "reserviert"));
    wrap.appendChild(stat(cores(d.tenant_cap_pct), "Nutzer-Cap"));
    wrap.appendChild(stat(cores(d.tenant_cpu_pct), "CPU jetzt"));
    wrap.appendChild(stat(cores(d.free_headroom_pct), "Headroom"));
    wrap.appendChild(stat(d.active_cells != null ? d.active_cells : "–", "aktive Umgebungen"));
    const pb = d.pn_batch || {};
    const cpuPsi = pb.cpu_psi_avg10 != null ? pb.cpu_psi_avg10 : d.cpu_psi_avg10;
    const memPsi = pb.mem_psi_avg10 != null ? pb.mem_psi_avg10 : d.mem_psi_avg10;
    const psi = $("#admPsi"); psi.textContent = "";
    if (cpuPsi != null) psi.appendChild(this.gauge("pn-batch CPU-Druck", cpuPsi));
    if (memPsi != null) psi.appendChild(this.gauge("pn-batch RAM-Druck", memPsi));
  },
  gauge(label, v) {
    const val = Math.max(0, Math.min(100, Number(v) || 0));
    const bar = el("div", { class: "bar" }, [el("i")]); bar.firstChild.style.width = val + "%";
    if (val >= 70) bar.firstChild.style.background = "var(--danger)";
    else if (val >= 40) bar.firstChild.style.background = "var(--warn)";
    return el("div", { class: "adm-gauge" }, [
      el("div", { class: "adm-lbl", text: label + " · avg10 " + val.toFixed(0) }), bar
    ]);
  },

  async loadAdvisor() {
    const d = await aget("/api/admin/advisor");
    if (!d || d._forbidden || d._neterr) return;
    this.renderAdvisor(d);
  },
  async advisorAction(action) {
    if (action === "run") toast("Advisor läuft … (bis ~1 min)");
    const d = await apost("/api/admin/advisor", { action });
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    if (d.ok === false) { toast(d.msg || "Fehler"); return; }
    if (action === "enable" || action === "disable") toast("Advisor " + (d.enabled ? "aktiviert" : "deaktiviert"));
    else if (d.note) toast("Advisor: " + d.note);
    this.loadAdvisor();
  },
  renderAdvisor(d) {
    const box = $("#admAdvisor"); if (!box) return; box.textContent = "";
    const on = !!d.enabled;
    const head = el("div", { class: "adm-pool-head" }, [
      el("span", { class: "pill role-" + (on ? "owner" : "user"), text: on ? "aktiv" : "aus" }),
      el("span", { class: "muted", text: "Band " + ((d.prio_band || [])[0]) + "–" + ((d.prio_band || [])[1]) +
        " · max " + (d.max_moves || "?") + "/Lauf · alle " + Math.round((d.interval_s || 0) / 60) + " min" }),
    ]);
    const acts = el("div", { class: "adm-pool-acts" }, [
      el("button", { class: "btn sm " + (on ? "danger" : "primary"), text: on ? "Ausschalten" : "Einschalten",
        onclick: () => this.advisorAction(on ? "disable" : "enable") }),
      el("button", { class: "btn sm ghost", text: "Jetzt ausführen", title: "Einen Advise-Lauf sofort starten", onclick: () => this.advisorAction("run") }),
    ]);
    head.appendChild(acts);
    box.appendChild(head);
    const last = d.last;
    if (last && last.note) box.appendChild(el("div", { class: "muted", text: "Letzter Lauf: " + last.note + (last.llm_status ? " (LLM " + last.llm_status + ")" : "") }));
    const log = el("div", { class: "adm-advlog" });
    const dec = d.decisions || [];
    if (!dec.length) log.appendChild(el("div", { class: "empty", text: "noch keine Entscheidungen" }));
    else dec.slice(0, 12).forEach(m => log.appendChild(el("div", { class: "adm-advrow tnum" }, [
      el("span", { class: (m.applied ? "ok" : "muted"), text: (m.applied ? "✓" : "·") + " #" + m.id }),
      el("span", { class: "muted ellipsis", text: (m.task || "job") + " · " + (m.user || "?") }),
      el("span", { text: m.from + "→" + m.to }),
      el("span", { class: "muted ellipsis", text: m.reason || "" }),
    ])));
    box.appendChild(log);
  },

  wireAccounts() {
    { const b = $("#acctNewBtn"); if (b) b.onclick = () => { const f = $("#acctNewForm"); if (f) f.hidden = !f.hidden; }; }
    { const b = $("#acctMsgAllBtn"); if (b) b.onclick = () => Msgs.presetTo("all"); }
    { const f = $("#acctNewForm"); if (f) f.addEventListener("submit", (e) => { e.preventDefault(); this.createAccount(); }); }
    { const ib = $("#acctInviteBtn"); if (ib) ib.addEventListener("click", () => this.inviteAccount()); }
  },
  async createAccount() {
    const v = (id) => (($(id) || {}).value || "").trim();
    const r = await apost("/api/admin/users/create", {
      uid: v("#acctNuid"), name: v("#acctNname"), email: v("#acctNemail"),
      role: v("#acctNrole") || "user", password: ($("#acctNpw") || {}).value || "",
    });
    if (r && r.ok) {
      toast("Konto '" + r.uid + "' angelegt");
      ["#acctNuid", "#acctNname", "#acctNemail", "#acctNpw"].forEach(id => { const i = $(id); if (i) i.value = ""; });
      { const f = $("#acctNewForm"); if (f) f.hidden = true; }
      this.loadUsers();
    } else toast("Anlegen fehlgeschlagen: " + ((r && r.error) || "?"));
  },
  async acctAction(uid, verb, body, label) {
    const r = await apost("/api/admin/user/" + encodeURIComponent(uid) + "/" + verb, body || {});
    toast(r && r.ok !== false ? (label || verb) + ": " + uid : "Fehler: " + ((r && r.error) || "?"));
    this.loadUsers();
  },
  async inviteAccount() {
    const email = (window.prompt("E-Mail-Adresse der einzuladenden Person:", "") || "").trim();
    if (!email) return;
    const name = (window.prompt("Anzeigename (optional):", "") || "").trim();
    const d = await apost("/api/admin/users/invite", { email, name });
    if (!d || d._forbidden || d._neterr) { toast("Netzfehler", true); return; }
    if (!d.ok) { toast(d.error || "Einladung fehlgeschlagen", true); return; }
    if (d.mail_sent) toast("Einladung an " + email + " gesendet (Konto: " + d.uid + ")");
    else window.prompt("E-Mail-Versand nicht möglich — Link selbst weitergeben:", d.link || "");
    this.loadUsers();
  },
  async approveAccount(uid) {
    const d = await apost("/api/admin/users/approve", { uid });
    if (!d || d._forbidden || d._neterr) { toast("Netzfehler", true); return; }
    if (!d.ok) { toast(d.error || "Freischalten fehlgeschlagen", true); return; }
    if (d.mail_sent) toast(uid + " freigeschaltet — Passwort-Link per E-Mail gesendet");
    else if (d.link) window.prompt("Freigeschaltet — Link selbst weitergeben:", d.link);
    else toast(uid + " freigeschaltet (keine E-Mail hinterlegt — Passwort über 🔑 setzen)");
    this.loadUsers();
  },
  renderAccounts(users) {

    const host = $("#acctTable"); if (!host) return;
    this._acctUsers = users;
    const pending = users.filter(u => u.status === "pending");
    { const b = $("#admBadge"); if (b) { b.textContent = pending.length; b.classList.toggle("on", pending.length > 0); } }
    if (host.contains(document.activeElement) && document.activeElement !== document.body) return;
    host.textContent = "";
    if (!users.length) { host.appendChild(el("div", { class: "empty", text: "—" })); return; }
    const counts = { alle: users.length, wartend: pending.length,
                     aktiv: users.filter(u => u.status === "active").length,
                     gesperrt: users.filter(u => u.status === "disabled").length };
    if (!this._acctFilter) this._acctFilter = pending.length ? "wartend" : "alle";
    if (this._acctFilter === "wartend" && !pending.length) this._acctFilter = "alle";
    const bar = el("div", { class: "acct-bar" });
    const chips = {};
    [["alle", "Alle"], ["wartend", "⏳ Wartend"], ["aktiv", "Aktiv"], ["gesperrt", "⛔ Gesperrt"]].forEach(([k, l]) => {
      const c = el("button", { class: "acct-chip" + (this._acctFilter === k ? " on" : "") + (k === "wartend" && counts.wartend ? " urgent" : ""),
        text: l + " (" + counts[k] + ")",
        onclick: () => { this._acctFilter = k;
          Object.keys(chips).forEach(x => chips[x].classList.toggle("on", x === k));
          this._acctRowsRender(); } });
      chips[k] = c; bar.appendChild(c);
    });
    const search = el("input", { class: "acct-search", type: "search", placeholder: "🔎 Name, uid oder E-Mail…", value: this._acctQ || "" });
    search.addEventListener("input", () => { this._acctQ = search.value; this._acctRowsRender(); });
    bar.appendChild(search);
    host.appendChild(bar);

    if (!this._acctSort) this._acctSort = { key: "_default", dir: 1 };
    const scroll = el("div", { class: "acct-scroll" });
    const tbl = el("table", { class: "acct-tbl" });
    const thead = el("thead"); const htr = el("tr");
    const COLS = [["konto", "Konto"], ["email", "E-Mail"], ["rolle", "Rolle"], ["status", "Status"],
                  ["created", "Registriert"], ["last", "Zuletzt"], ["akt", "Aktivität"], ["", "Aktionen"]];
    COLS.forEach(([key, label]) => {
      const th = el("th", { text: label });
      if (key) {
        th.classList.add("sortable");
        if (this._acctSort.key === key) th.classList.add(this._acctSort.dir > 0 ? "asc" : "desc");
        th.onclick = () => {
          this._acctSort = { key, dir: this._acctSort.key === key ? -this._acctSort.dir : 1 };
          Array.from(htr.children).forEach(x => x.classList.remove("asc", "desc"));
          th.classList.add(this._acctSort.dir > 0 ? "asc" : "desc");
          this._acctRowsRender();
        };
      }
      htr.appendChild(th);
    });
    thead.appendChild(htr); tbl.appendChild(thead);
    this._acctRowsHost = el("tbody");
    tbl.appendChild(this._acctRowsHost);
    scroll.appendChild(tbl);
    host.appendChild(scroll);
    this._acctRowsRender();
    this._acctStatsLoad();
  },
  async _acctStatsLoad() {

    const now = Date.now();
    if (this._acctStatsAt && now - this._acctStatsAt < 60000) return;
    this._acctStatsAt = now;
    let d; try { d = await aget("/api/admin/stats"); } catch (e) { return; }
    if (!d || !d.ok) return;
    const map = {};
    ((d.week && d.week.top_users) || []).forEach(t => { map[t.user] = t; });
    this._acctStats = map;
    this._acctRowsRender();
  },
  _acctRowsRender() {
    const box = this._acctRowsHost; if (!box || !box.isConnected) return;
    box.textContent = "";
    const q = (this._acctQ || "").toLowerCase();
    const f = this._acctFilter || "alle";
    const stf = { wartend: "pending", aktiv: "active", gesperrt: "disabled" }[f];
    let rows = [...(this._acctUsers || [])];
    if (stf) rows = rows.filter(u => u.status === stf);
    if (q) rows = rows.filter(u => ((u.name || "") + " " + u.uid + " " + (u.email || "")).toLowerCase().includes(q));
    const s = this._acctSort || { key: "_default", dir: 1 };
    const stats = this._acctStats || {};
    const keyf = {
      _default: (u) => [(u.status === "pending" ? 0 : 1), String(u.name || u.uid).toLowerCase()],
      konto: (u) => String(u.name || u.uid).toLowerCase(),
      email: (u) => String(u.email || "￿").toLowerCase(),
      rolle: (u) => ({ owner: 0, admin: 1, user: 2, guest: 3 }[u.role] != null ? { owner: 0, admin: 1, user: 2, guest: 3 }[u.role] : 9),
      status: (u) => ({ pending: 0, active: 1, disabled: 2 }[u.status] != null ? { pending: 0, active: 1, disabled: 2 }[u.status] : 9),
      created: (u) => u.created || 0,
      last: (u) => u.last_login || 0,
      akt: (u) => ((stats[u.uid] || {}).llm_calls || 0) + ((u.usage || {}).cpu_pct || 0),
    }[s.key] || ((u) => 0);
    rows.sort((a, b) => { const ka = keyf(a), kb = keyf(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * s.dir; });
    if (!rows.length) {
      const tr = el("tr"); const td = el("td", { class: "empty", text: q ? "keine Treffer" : "keine Konten in dieser Ansicht" });
      td.colSpan = 8; tr.appendChild(td); box.appendChild(tr); return;
    }
    rows.forEach(u => {
      const uid = u.uid;
      const roleSel = el("select", { class: "fs-sel", title: "Rolle" },
        ["owner", "admin", "user", "guest"].map(r => el("option", { value: r, text: r, selected: (u.role === r) || null })));
      roleSel.disabled = u.role === "owner";
      roleSel.onchange = () => { this.acctAction(uid, "account", { role: roleSel.value }, "Rolle"); roleSel.blur(); };
      const statusPill = u.status === "pending"
        ? el("span", { class: "pill st-pending", text: "⏳ wartet" })
        : u.status === "disabled"
          ? el("span", { class: "pill st-off", text: "⛔ gesperrt" })
          : el("span", { class: "pill st-on", text: "aktiv" });

      const akt = el("div", { class: "acct-activity" });
      const usage = u.usage || {};
      const liveBits = [];
      if (u.seat_running) liveBits.push("🖥 Bildschirm");
      if (usage.cpu_pct > 0.5 || usage.mem_mb > 32) liveBits.push((usage.cpu_pct || 0).toFixed(0) + "% CPU · " + (usage.mem_mb || 0) + " MB");
      if (u.frozen) liveBits.push("❄ pausiert");
      (Array.isArray(u.bans) ? u.bans : Object.keys(u.bans || {})).forEach(b => liveBits.push("⛔ " + (b && b.feature || b)));
      if (liveBits.length) akt.appendChild(el("div", { class: "acct-live", text: liveBits.join(" · ") }));
      const st7 = stats[uid];
      akt.appendChild(el("div", { class: "muted tnum", text: st7
        ? (st7.llm_calls || 0) + " LLM-Aufrufe · " + (st7.events || 0) + " Ereignisse (7 T.)"
        : (this._acctStats ? "keine Aktivität (7 T.)" : "…") }));
      const acts = el("div", { class: "acct-acts" });
      if (u.status === "pending") {
        acts.appendChild(el("button", { class: "btn sm primary", text: "✅ Freischalten",
          title: "Konto aktivieren — der Nutzer meldet sich mit seinem selbst gewählten Passwort an",
          onclick: () => this.approveAccount(uid) }));
        acts.appendChild(el("button", { class: "btn sm ghost danger", text: "Ablehnen",
          title: "Anfrage ablehnen: Konto wird entfernt, der Name wird wieder frei",
          onclick: () => { if (window.confirm("Anfrage von '" + uid + "' ablehnen? Das Konto wird entfernt."))
            this.acctAction(uid, "delete", {}, "Abgelehnt & entfernt"); } }));
      } else {
        acts.appendChild(el("button", { class: "btn sm ghost", text: "✉️", title: "Nachricht an " + uid + " schreiben", onclick: () => Msgs.presetTo(uid) }));
        acts.appendChild(el("button", { class: "btn sm ghost", text: "🔑", title: "Neues Passwort setzen", onclick: () => {
          const pw = window.prompt("Neues Passwort für '" + uid + "' (min. 8 Zeichen):", "");
          if (pw) this.acctAction(uid, "password", { password: pw }, "Passwort");
        } }));
        if (u.role !== "owner") {
          acts.appendChild(el("button", { class: "btn sm ghost", text: u.status === "active" ? "⛔" : "✅",
            title: u.status === "active" ? "Konto sperren (laufende Sitzungen enden)" : "Konto entsperren",
            onclick: () => this.acctAction(uid, "account", { status: u.status === "active" ? "disabled" : "active" },
                                           u.status === "active" ? "Gesperrt" : "Entsperrt") }));
          acts.appendChild(el("button", { class: "btn sm ghost danger", text: "🗑", title: "Konto löschen", onclick: () => {
            if (window.confirm("Konto '" + uid + "' wirklich löschen?")) this.acctAction(uid, "delete", {}, "Gelöscht");
          } }));
        }
      }
      const tr = el("tr", { class: (u.status === "pending" ? "pend" : "") + (u.status === "disabled" ? " off" : "") });
      const td = (child, cls) => { const c = el("td", cls ? { class: cls } : {}); (Array.isArray(child) ? child : [child]).forEach(x => { if (x != null) c.appendChild(typeof x === "string" ? el("span", { text: x }) : x); }); tr.appendChild(c); };
      td([el("b", { text: u.name && u.name !== uid ? u.name : uid }),
          el("div", { class: "muted", text: uid + (u.role === "owner" ? " · 👑" : "") + (u.auth_source && u.auth_source !== "local" ? " · " + u.auth_source : "") })], "acct-who");
      td(u.email
        ? [el("span", { class: "ellipsis", title: u.email, text: u.email }),
           el("span", { class: "acct-vrf " + (u.email_verified ? "ok" : ""), title: u.email_verified ? "E-Mail bestätigt" : "E-Mail unbestätigt", text: u.email_verified ? "✓" : "?" }),
           (u.email_optout ? el("span", { title: "hat Mail-Kopien abbestellt", text: "📵" }) : null)]
        : "—", "acct-mail");
      td(roleSel);
      td(statusPill);
      td(u.created ? fmtWhen(u.created) : "—", "muted tnum");
      td(u.last_login ? fmtWhen(u.last_login) : "nie", "muted tnum");
      td(akt);
      td(acts, "acct-actcell");
      box.appendChild(tr);
    });
  },

  async loadUsers() {
    const d = await aget("/api/admin/users");
    if (!d || d._forbidden || d._neterr) return;
    const users = Array.isArray(d) ? d : (Array.isArray(d.users) ? d.users : []);
    this.renderAccounts(users);
    this.renderUsers(users);
  },
  renderUsers(users) {
    const box = $("#admUsers"); box.textContent = "";
    if (!users.length) { box.appendChild(el("div", { class: "empty", text: "keine Nutzer" })); return; }
    users.forEach(u => box.appendChild(this.userCard(u)));
  },
  userCard(u) {
    const uid = (u.uid != null) ? String(u.uid) : "";
    const usage = u.usage || {}, live = u.live_limits || {}, alloc = u.allocation || {};
    const capPct = this.capPct || 100;

    const head = el("div", { class: "usr-head" }, [
      (u.seat_running ? el("span", { class: "run-dot", title: "Bildschirm läuft" }) : null),
      el("span", { class: "usr-name", text: u.name || uid || "?" }),
      el("span", { class: "usr-uid mono", text: "uid " + uid }),
      el("span", { class: "pill role-" + (u.role || "user"), text: u.role || "user" }),
      (u.status ? el("span", { class: "muted", text: u.status }) : null),
      (u.frozen ? el("span", { class: "badge warn", text: "❄ pausiert" }) : null)
    ]);

    const cpuPct = Number(usage.cpu_pct) || 0;
    const cpuW = capPct ? Math.max(0, Math.min(100, cpuPct / capPct * 100)) : 0;
    const memMb = Number(usage.mem_mb) || 0;
    const memMax = Number(live.mem_max_mb) || this.boxMemMb || 0;
    const memW = memMax ? Math.max(0, Math.min(100, memMb / memMax * 100)) : 0;
    const bar = (w) => { const b = el("div", { class: "bar" }, [el("i")]); b.firstChild.style.width = w + "%"; return b; };
    const bars = el("div", { class: "usr-bars" }, [
      el("div", { class: "usr-barwrap" }, [el("div", { class: "adm-lbl", text: "CPU " + (cpuPct / 100).toFixed(1) + " / " + (capPct / 100).toFixed(1) + " Kerne" }), bar(cpuW)]),
      el("div", { class: "usr-barwrap" }, [el("div", { class: "adm-lbl", text: "RAM " + memMb + " / " + (memMax || "?") + " MiB" }), bar(memW)])
    ]);

    const alcTxt = "live: CPU " + (live.cpu_max_pct != null ? live.cpu_max_pct + "%" : "–") +
      " · RAM " + (live.mem_max_mb != null ? live.mem_max_mb + "M" : "–") +
      " · w " + (live.weight != null ? live.weight : "–") +
      "   set: CPU " + (alloc.cpu_pct != null ? alloc.cpu_pct + "%" : "–") +
      " · RAM " + (alloc.mem_mb != null ? alloc.mem_mb + "M" : "–") +
      " · w " + (alloc.weight != null ? alloc.weight : "–");
    const allocEl = el("div", { class: "usr-alloc mono", text: alcTxt });

    const acts = el("div", { class: "usr-actions" });
    if (u.frozen) acts.appendChild(el("button", { class: "btn sm", text: "▶ Fortsetzen", onclick: () => this.act(uid, "resume") }));
    else acts.appendChild(el("button", { class: "btn sm", text: "⏸ Pause", onclick: () => this.act(uid, "pause") }));
    acts.appendChild(el("button", { class: "btn sm ghost", text: "⏻ Soft-Stop", onclick: () => this.act(uid, "soft_stop") }));
    acts.appendChild(el("button", { class: "btn sm danger", text: "✖ Hard-Stop",
      onclick: () => this.act(uid, "hard_stop", null, { confirm: "Hard-Stop für " + (u.name || uid) + "? Prozesse werden hart beendet." }) }));

    const realloc = this.reallocPanel(uid, alloc, live);
    const ban = this.banPanel(uid, u.bans || {});
    acts.appendChild(el("button", { class: "btn sm", text: "⚙ Zuteilung", onclick: () => this.togglePanel(realloc, ban) }));
    acts.appendChild(el("button", { class: "btn sm", text: "⛔ Sperre", onclick: () => this.togglePanel(ban, realloc) }));

    return el("div", { class: "card usr" }, [head, bars, allocEl, acts, realloc, ban]);
  },

  togglePanel(show, other) {
    if (other && !other.hidden) other.hidden = true;
    show.hidden = !show.hidden;
    this.hold = $$(".usr-editor").filter(p => !p.hidden).length;
  },

  reallocPanel(uid, alloc, live) {
    const cpu0 = alloc.cpu_pct != null ? alloc.cpu_pct : (live.cpu_max_pct != null ? live.cpu_max_pct : "");
    const mem0 = alloc.mem_mb != null ? alloc.mem_mb : (live.mem_max_mb != null ? live.mem_max_mb : "");
    const w0   = alloc.weight != null ? alloc.weight : (live.weight != null ? live.weight : "");
    const cpu = el("input", { inputmode: "numeric", placeholder: "CPU %", value: cpu0 });
    const mem = el("input", { inputmode: "numeric", placeholder: "RAM MiB", value: mem0 });
    const w   = el("input", { inputmode: "numeric", placeholder: "Gewicht", value: w0 });
    const save = el("button", { class: "btn sm primary", text: "Speichern", onclick: () => {
      const body = {};
      const put = (k, v, orig) => { const t = String(v).trim(); if (t !== "" && t !== String(orig)) body[k] = Number(t); };
      put("cpu_pct", cpu.value, cpu0); put("mem_mb", mem.value, mem0); put("weight", w.value, w0);
      if (!Object.keys(body).length) { toast("nichts geändert"); return; }
      this.act(uid, "reallocate", body);
    } });

    const bias = el("input", { inputmode: "numeric", placeholder: "− = früher", value: (alloc.prio_bias != null ? alloc.prio_bias : "") });
    const biasSave = el("button", { class: "btn sm", text: "Prio-Bias", title: "Warteschlangen-Bias für künftige Jobs dieses Nutzers (−100..100, negativ = früher dran)", onclick: () => {
      const t = String(bias.value).trim();
      if (t === "" || isNaN(Number(t))) { toast("Bias-Zahl fehlt"); return; }
      this.act(uid, "set_prio_bias", { prio_bias: Number(t) });
    } });
    return el("div", { class: "usr-editor", hidden: true }, [
      el("div", { class: "adm-lbl", text: "Neu zuteilen (nur geänderte Felder)" }),
      el("div", { class: "field-row" }, [
        el("label", { class: "efield" }, ["CPU %", cpu]),
        el("label", { class: "efield" }, ["RAM MiB", mem]),
        el("label", { class: "efield" }, ["Gewicht", w])
      ]),
      el("div", { class: "row" }, [save]),
      el("div", { class: "adm-lbl", text: "Warteschlangen-Priorität (künftige Jobs)" }),
      el("div", { class: "field-row" }, [el("label", { class: "efield" }, ["Prio-Bias", bias]), biasSave])
    ]);
  },

  banPanel(uid, bans) {
    const chips = el("div", { class: "ban-chips" });
    const keys = Object.keys(bans || {});
    if (keys.length) keys.forEach(f => {
      const info = bans[f] || {};
      const suffix = info.window ? " " + info.window : (info.until_ts ? " bis " + this.fmtTs(info.until_ts) : "");
      const chip = el("span", { class: "chip ban", title: f + suffix }, [el("span", { class: "u", text: f + suffix })]);
      chip.appendChild(el("button", { class: "chip-x", text: "✕", title: "Sperre aufheben", onclick: () => this.act(uid, "unban", { feature: f }) }));
      chips.appendChild(chip);
    });
    else chips.appendChild(el("span", { class: "muted", text: "keine aktiven Sperren" }));

    const feat = el("select", {}, BAN_FEATURES.map(f => el("option", { value: f }, f)));
    const mode = el("select", {}, [
      el("option", { value: "standing" }, "unbefristet"),
      el("option", { value: "until" }, "bis Zeitpunkt"),
      el("option", { value: "window" }, "Tagesfenster")
    ]);
    const until = el("input", { type: "datetime-local" });
    const win = el("input", { placeholder: "HH:MM-HH:MM" });
    const untilWrap = el("label", { class: "efield", hidden: true }, ["bis", until]);
    const winWrap = el("label", { class: "efield", hidden: true }, ["Fenster", win]);
    mode.addEventListener("change", () => { untilWrap.hidden = mode.value !== "until"; winWrap.hidden = mode.value !== "window"; });
    const apply = el("button", { class: "btn sm danger", text: "Sperren", onclick: () => {
      const body = { feature: feat.value };
      if (mode.value === "until") {
        if (!until.value) { toast("Zeitpunkt fehlt"); return; }
        body.until_ts = Math.floor(new Date(until.value).getTime() / 1000);
      } else if (mode.value === "window") {
        const v = win.value.trim();
        if (!/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(v)) { toast("Fenster: HH:MM-HH:MM"); return; }
        body.window = v;
      }
      this.act(uid, "ban", body);
    } });
    return el("div", { class: "usr-editor", hidden: true }, [
      el("div", { class: "adm-lbl", text: "Feature-Sperren" }),
      chips,
      el("div", { class: "field-row" }, [
        el("label", { class: "efield" }, ["Feature", feat]),
        el("label", { class: "efield" }, ["Modus", mode]),
        untilWrap, winWrap
      ]),
      el("div", { class: "row" }, [apply])
    ]);
  },
  fmtTs(ts) { try { return new Date(Number(ts) * 1000).toLocaleString(); } catch (e) { return String(ts); } },

  async act(uid, verb, body, opts) {
    opts = opts || {};
    if (opts.confirm && !window.confirm(opts.confirm)) return;
    const d = await apost("/api/admin/user/" + encodeURIComponent(uid) + "/" + verb, body || {});
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    toast(d.msg || (d.ok ? "ok" : "Fehler"));
    this.hold = 0; this.loadUsers();
  },

  async loadKeys() {
    const d = await aget("/api/keys?all=1");
    if (!d || d._forbidden || d._neterr) return;
    this.keys = d.keys || [];
    this.catalog = d.catalog || [];
    this.principals = d.principals || [];
    this.renderKeys();
  },
  renderKeys() {
    const box = $("#admKeys"); if (!box) return; box.textContent = "";
    let rows = this.keys.slice().sort((a, b) => (b.created || 0) - (a.created || 0));
    if (!this.keysShowRev) rows = rows.filter(k => !k.revoked);
    if (!rows.length) { box.appendChild(el("div", { class: "empty", text: "keine Schlüssel" })); return; }
    const now = Date.now() / 1000;
    rows.forEach(k => {
      const expired = k.expires_at && now > k.expires_at;
      const st = k.revoked ? ["widerrufen", "bad"] : expired ? ["abgelaufen", "warn"] : ["aktiv", "good"];
      const scopes = (k.scopes && k.scopes.length)
        ? k.scopes.map(s => el("span", { class: "scope-chip", text: s }))
        : [el("span", { class: "scope-chip full", text: "VOLL — alle Pfade" })];
      const meta = el("div", { class: "key-meta muted" }, [
        el("code", { class: "tnum", text: k.id }),
        el("span", { text: " · " + (k.uid || "?") }),
        k.label ? el("span", { text: " · " + k.label }) : null,
        el("span", { text: " · " + (k.rate_per_min ? k.rate_per_min + "/min" : "kein Limit") }),
        el("span", { text: " · " + (k.expires_at ? "Ablauf " + this.fmtDate(k.expires_at) : "kein Ablauf") }),
        el("span", { text: " · zuletzt " + (k.last_used ? this.fmtDate(k.last_used) : "nie") }),
      ]);
      const actions = el("div", { class: "key-actions" });
      if (!k.revoked) {
        actions.append(
          el("button", { class: "btn sm ghost", text: "Bearbeiten", onclick: () => this.openKeyEditor(k.id) }),
          el("button", { class: "btn sm ghost", text: "Rotieren", onclick: () => this.rotateKey(k) }),
          el("button", { class: "btn sm danger", text: "Widerrufen", onclick: () => this.revokeKey(k) }),
        );
      }
      box.appendChild(el("div", { class: "key-row" + (k.revoked ? " is-revoked" : "") }, [
        el("div", { class: "key-head" }, [el("span", { class: "key-state " + st[1], text: st[0] }), meta]),
        el("div", { class: "key-scopes" }, scopes),
        actions,
      ]));
    });
  },
  openKeyEditor(kid) {
    const box = $("#admKeyEditor"); if (!box) return;
    const k = kid ? this.keys.find(x => x.id === kid) : null;
    this.keyEdit = kid || null;
    const isNew = !k;
    const have = new Set((k && k.scopes) || []);
    box.hidden = false; box.textContent = "";
    const uidUnion = Array.from(new Set([...(this.principals || []), ...this.keys.map(x => x.uid)])).filter(Boolean).sort();
    const rows = [];
    rows.push(el("div", { class: "ke-title", text: isNew ? "Neuer Schlüssel" : ("Schlüssel " + k.id + " bearbeiten") }));
    if (isNew) {
      const dl = el("datalist", { id: "keyPrincList" }, uidUnion.map(u => el("option", { value: u })));
      rows.push(el("label", { class: "ke-field" }, [
        el("span", { text: "Principal (uid)" }),
        el("input", { id: "keyPrincipal", list: "keyPrincList", placeholder: "z. B. smarthome", autocomplete: "off" }), dl,
      ]));
    } else {
      rows.push(el("div", { class: "ke-field muted", text: "Principal: " + (k.uid || "?") + " · Secret bleibt unverändert" }));
    }
    rows.push(el("label", { class: "ke-field" }, [
      el("span", { text: "Bezeichnung" }),
      el("input", { id: "keyLabel", value: (k && k.label) || "", placeholder: "z. B. smarthome-pi", maxlength: "80", autocomplete: "off" }),
    ]));
    const cat = el("div", { class: "ke-scopes" }, (this.catalog || []).map(c =>
      el("label", { class: "scope-opt" + (c.danger ? " danger" : "") }, [
        el("input", { type: "checkbox", value: c.prefix, checked: have.has(c.prefix) }),
        el("code", { text: c.prefix }), el("span", { class: "muted", text: c.label || "" }),
      ])));
    rows.push(el("div", { class: "ke-field" }, [el("span", { text: "Scopes (keine ausgewählt = VOLLzugriff)" }), cat]));
    const custom = ((k && k.scopes) || []).filter(s => !(this.catalog || []).some(c => c.prefix === s));
    rows.push(el("label", { class: "ke-field" }, [
      el("span", { text: "Weitere Scopes (durch Leerzeichen)" }),
      el("input", { id: "keyCustom", value: custom.join(" "), placeholder: "/api/… /screen/…", autocomplete: "off" }),
    ]));
    rows.push(el("div", { class: "ke-grid2" }, [
      el("label", { class: "ke-field" }, [el("span", { text: "Ablauf (Tage · 0 = nie)" }),
        el("input", { id: "keyTtl", type: "number", min: "0", value: this._ttlDaysOf(k) })]),
      el("label", { class: "ke-field" }, [el("span", { text: "Rate (Req/min · 0 = kein Limit)" }),
        el("input", { id: "keyRate", type: "number", min: "0", value: (k && k.rate_per_min) || 0 })]),
    ]));
    rows.push(el("div", { class: "ke-actions" }, [
      el("button", { class: "btn", text: isNew ? "Erzeugen" : "Speichern", onclick: () => this.submitKey() }),
      el("button", { class: "btn ghost", text: "Abbrechen", onclick: () => this.closeKeyEditor() }),
    ]));
    box.append.apply(box, rows);
    const first = $("#keyPrincipal") || $("#keyLabel"); if (first) first.focus();
  },
  _ttlDaysOf(k) {
    if (!k || !k.expires_at) return 0;
    const d = Math.ceil((k.expires_at - Date.now() / 1000) / 86400);
    return d > 0 ? d : 0;
  },
  closeKeyEditor() { const b = $("#admKeyEditor"); if (b) { b.hidden = true; b.textContent = ""; } this.keyEdit = null; },
  _gatherScopes() {
    const checked = $$(".ke-scopes .scope-opt input:checked").map(i => i.value);
    const raw = (($("#keyCustom") || {}).value || "").split(/[\s,]+/).filter(Boolean);
    const custom = raw.filter(s => s.indexOf("/") === 0);
    const dropped = raw.filter(s => s.indexOf("/") !== 0);
    return { scopes: Array.from(new Set(checked.concat(custom))), dropped };
  },
  async submitKey() {
    const label = (($("#keyLabel") || {}).value || "").trim();
    const { scopes, dropped } = this._gatherScopes();

    if (dropped.length) { toast("Ungültige Scopes (führendes / fehlt): " + dropped.join(" ")); return; }
    if (!scopes.length && !window.confirm("Keine Scopes = VOLLZUGRIFF auf ALLE Endpunkte (inkl. /api/admin, /api/secret). Wirklich einen Vollzugriffs-Schlüssel erstellen?")) return;
    const ttl_days = parseInt(($("#keyTtl") || {}).value || "0", 10) || 0;
    const rate_per_min = parseInt(($("#keyRate") || {}).value || "0", 10) || 0;
    if (this.keyEdit) {
      const d = await apost("/api/keys/" + encodeURIComponent(this.keyEdit) + "/update", { scopes, label, ttl_days, rate_per_min });
      if (!d || d._forbidden) return;
      if (d._neterr) { toast("Netzfehler"); return; }
      if (!d.ok) { toast("Fehler: " + (d.error || "?")); return; }
      toast("Schlüssel aktualisiert (live)"); this.closeKeyEditor(); this.loadKeys();
    } else {
      const principal = (($("#keyPrincipal") || {}).value || "").trim();
      if (!principal) { toast("Principal fehlt"); return; }
      const d = await apost("/api/keys", { for_principal: principal, label, scopes, ttl_days, rate_per_min });
      if (!d || d._forbidden) return;
      if (d._neterr) { toast("Netzfehler"); return; }
      if (!d.ok) { toast("Fehler: " + (d.error || "?")); return; }
      this.showSecret(d.key, principal); this.loadKeys();
    }
  },
  async revokeKey(k) {
    if (!window.confirm("Schlüssel " + k.id + " (" + (k.uid || "?") + ") widerrufen? Clients mit diesem Key verlieren sofort den Zugang.")) return;
    const d = await apost("/api/keys/" + encodeURIComponent(k.id) + "/revoke", {});
    if (!d || d._forbidden) return;
    if (d._neterr) { toast("Netzfehler"); return; }
    toast(d.ok ? "widerrufen" : "Fehler"); this.loadKeys();
  },
  async rotateKey(k) {
    if (!window.confirm("Rotieren erzeugt einen NEUEN Schlüssel für „" + (k.uid || "?") + "“ mit denselben Scopes und widerruft den alten.\nDer Client muss den neuen .env-Wert übernehmen. Fortfahren?")) return;
    const ttl_days = this._ttlDaysOf(k);
    const d = await apost("/api/keys", { for_principal: k.uid, label: k.label || "", scopes: k.scopes || [], rate_per_min: k.rate_per_min || 0, ttl_days });
    if (!d || !d.ok) { toast("Rotieren fehlgeschlagen: " + ((d && d.error) || "?")); return; }

    const rv = await apost("/api/keys/" + encodeURIComponent(k.id) + "/revoke", {});
    if (!rv || rv._forbidden || rv._neterr || !rv.ok) {
      this.showSecret(d.key, k.uid, null);
      toast("⚠ Neuer Schlüssel erstellt, aber der ALTE wurde NICHT widerrufen — bitte manuell widerrufen!");
      this.loadKeys(); return;
    }
    this.showSecret(d.key, k.uid, k.id); this.loadKeys();
  },

  _idName(k) {
    return "\u201E" + (k.device_label || k.key_id || "?") + "\u201C";
  },
  _idZeit(ms) {
    if (!ms) return "—";
    const d = new Date(Number(ms)), p = (n) => String(n).padStart(2, "0");
    if (isNaN(d.getTime())) return "—";
    return p(d.getDate()) + "." + p(d.getMonth() + 1) + "." + d.getFullYear() + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  },
  async loadIdentity() {
    const d = await aget("/api/identity/geraete");
    if (!d || d._forbidden || d._neterr) return;
    this.ident = d; this.renderIdentity();
  },

  async pairMint() {
    const box = $("#admIdPairBox"); if (!box) return;
    box.hidden = false; box.textContent = "";
    box.appendChild(el("div", { class: "muted", text: "Erzeuge Kopplungscode …" }));
    const d = await jpost("/api/pair/mint", { label: "", ttl_s: 600 });
    if (!d || d._neterr) { box.textContent = ""; box.appendChild(el("div", { class: "empty", text: "Netzfehler beim Erzeugen." })); return; }
    if (!d.ok) { box.textContent = ""; box.appendChild(el("div", { class: "empty", text: d.error || "Konnte keinen Code erzeugen." })); return; }
    this._renderPair(box, d);
  },
  _renderPair(box, d) {
    box.textContent = "";
    if (this._pairTimer) { clearInterval(this._pairTimer); this._pairTimer = null; }
    const wrap = el("div", { class: "adm-pair-wrap",
      style: "border:1px solid var(--bd,rgba(128,128,128,.3));border-radius:12px;padding:14px;margin:10px 0;text-align:center" });
    wrap.appendChild(el("div", { style: "font-weight:600;margin-bottom:8px", text: "Neues Gerät koppeln" }));

    const selfBtn = el("button", { class: "btn", style: "width:100%;margin:0 0 4px",
      text: "📱 Dieses Gerät koppeln — ohne Scan" });
    selfBtn.addEventListener("click", () => { location.href = d.url; });
    wrap.appendChild(selfBtn);
    wrap.appendChild(el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px",
      text: "Für das Gerät, auf dem du gerade bist — führt dich direkt zur Kopplung." }));
    wrap.appendChild(el("div", { class: "muted",
      style: "border-top:1px solid var(--bd,rgba(128,128,128,.25));padding-top:10px;font-size:12px;margin-bottom:4px",
      text: "… oder ein ANDERES Gerät scannt diesen Code:" }));
    if (d.qr) {
      const img = el("img", { alt: "Kopplungs-QR",
        style: "width:210px;max-width:72%;border-radius:10px;background:#fff;padding:8px;display:block;margin:2px auto 6px" });
      img.src = d.qr; wrap.appendChild(img);
    } else {
      wrap.appendChild(el("div", { class: "muted", text: "(QR nicht verfügbar — nutze den Code unten)" }));
    }
    wrap.appendChild(el("div", { class: "tnum",
      style: "font:700 20px ui-monospace,Menlo,Consolas,monospace;letter-spacing:2px;margin:6px 0;word-break:break-all" ,
      text: d.code }));
    const left = el("b", { text: "…" });
    wrap.appendChild(el("div", { class: "muted", style: "margin:2px 0 8px" }, [ el("span", { text: "Läuft ab in " }), left ]));
    const base = (d.url || "").replace(/[?&]code=.*$/, "");
    wrap.appendChild(el("div", { class: "muted", style: "text-align:left;line-height:1.6;font-size:13px" }, [
      el("div", { text: "Auf dem NEUEN Gerät:" }),
      el("div", { text: "① Kamera / QR-Scanner auf den Code richten, oder" }),
      el("div", { text: "② " + (base || "diese Box") + "/pair öffnen und den Code eintippen." }),
      el("div", { text: "Beim Koppeln entsteht der Geräteschlüssel automatisch; ein Zweitfaktor (TOTP) wird abgefragt, falls für das Konto scharf." }),
    ]));
    const acts = el("div", { class: "key-actions", style: "justify-content:center;margin-top:10px" });
    acts.appendChild(el("button", { class: "btn sm ghost", text: "Link kopieren", onclick: () => {
      if (navigator.clipboard) navigator.clipboard.writeText(d.url || d.code).then(() => toast("Link kopiert"), () => {});
    } }));
    acts.appendChild(el("button", { class: "btn sm", text: "Neuen Code", onclick: () => this.pairMint() }));
    acts.appendChild(el("button", { class: "btn sm ghost", text: "Schließen", onclick: () => {
      if (this._pairTimer) { clearInterval(this._pairTimer); this._pairTimer = null; }
      box.hidden = true; box.textContent = ""; this.loadIdentity();
    } }));
    wrap.appendChild(acts);
    box.appendChild(wrap);
    let rem = Math.max(0, parseInt(d.expires_in, 10) || 0);
    const tick = () => {
      if (rem <= 0) { left.textContent = "abgelaufen — „Neuen Code“ tippen";
        if (this._pairTimer) { clearInterval(this._pairTimer); this._pairTimer = null; } return; }
      const m = Math.floor(rem / 60), s = rem % 60;
      left.textContent = m + ":" + String(s).padStart(2, "0"); rem--;
    };
    tick(); this._pairTimer = setInterval(tick, 1000);
  },
  renderIdentity() {
    const stBox = $("#admIdState"), box = $("#admId");
    if (!box || !stBox || !this.ident) return;
    const d = this.ident;
    stBox.textContent = ""; box.textContent = "";
    if (!d.ok) {
      stBox.appendChild(el("div", { class: "empty", text: d.error || "Identitätsschicht nicht verfügbar" }));
      return;
    }

    stBox.appendChild(el("div", { class: "adm-id-mode " + (d.beobachtet_nur ? "warn" : "good"),
      text: d.beobachtet_nur
        ? "⏸ Durchsetzung: BEOBACHTET NUR — die Prüfung läuft mit, hält aber nichts auf. Ein Gerät ohne Schlüssel kommt weiterhin herein."
        : "🔒 Durchsetzung: SCHARF — ohne freigegebenen Geräteschlüssel kommt niemand herein." }));
    if (!d.owner_vorhanden) {
      stBox.appendChild(el("div", { class: "adm-id-mode warn",
        text: "⚠ Diese Box hat noch KEINEN Owner-Geräteschlüssel. Solange kann niemand ein weiteres Gerät freigeben. " +
              "Der erste entsteht beim Koppeln eines Geräts, das einen Ed25519-Schlüssel mitschickt — der private Teil bleibt dabei auf dem Gerät." }));
    }

    const abschnitt = (titel, hinweis, kinder) => {
      if (!kinder.length) return;
      box.appendChild(el("div", { class: "adm-id-sec" }, [
        el("div", { class: "adm-id-sec-h", text: titel + " (" + kinder.length + ")" }),
        hinweis ? el("div", { class: "adm-id-sec-n muted", text: hinweis }) : null,
      ].filter(Boolean)));
      kinder.forEach(c => box.appendChild(c));
    };
    abschnitt("Wartet auf Freigabe", "Diese Geräte dürfen nichts, bis sie freigegeben sind.",
              (d.wartend || []).map(k => this._idRow(k)));
    abschnitt("Freigegeben", "", (d.aktiv || []).map(k => this._idRow(k)));
    abschnitt("Gekoppelt, aber OHNE Geräteschlüssel",
              "Diese Geräte sind über einen Kopplungscode hereingekommen und tragen einen Maschinenschlüssel — " +
              "aber keinen Geräteschlüssel. Wird die Durchsetzung scharf gestellt, fallen genau sie heraus. " +
              "Abhilfe: neu koppeln mit einem Client, der einen Ed25519-Schlüssel mitschickt.",
              (d.ohne_geraeteschluessel || []).map(g => this._idPairRow(g)));
    if (this.identShowRev) {
      abschnitt("Widerrufen", "", (d.widerrufen || []).map(k => this._idRow(k)));
    }
    if (!box.childNodes.length) {
      box.appendChild(el("div", { class: "empty", text: "kein Gerät bekannt" }));
    }
  },
  _idRow(k) {
    const st = k.state === "active" ? ["freigegeben", "good"]
             : k.state === "pending" ? ["wartet", "warn"] : ["widerrufen", "bad"];
    const meta = el("div", { class: "key-meta muted" }, [
      el("code", { class: "tnum", text: k.key_id }),
      el("span", { text: " · " + (k.principal || "?") }),
      el("span", { text: " · angemeldet " + this._idZeit(k.created_ts) }),
      k.approved_ts ? el("span", { text: " · freigegeben " + this._idZeit(k.approved_ts) }) : null,
      k.revoked_ts ? el("span", { text: " · widerrufen " + this._idZeit(k.revoked_ts) }) : null,
      k.enrolled_by ? el("span", { text: " · durch " + k.enrolled_by }) : null,
      k.rotated_from ? el("span", { text: " · rotiert aus " + k.rotated_from }) : null,
    ]);
    const actions = el("div", { class: "key-actions" });
    if (k.state === "pending") {
      actions.appendChild(el("button", { class: "btn sm", text: "Freigeben", onclick: () => this.identApprove(k) }));
    }
    if (k.state !== "revoked") {
      actions.appendChild(el("button", { class: "btn sm danger", text: "Widerrufen", onclick: () => this.identRevoke(k) }));
    }
    return el("div", { class: "key-row" + (k.state === "revoked" ? " is-revoked" : "") }, [
      el("div", { class: "key-head" }, [
        el("span", { class: "key-state " + st[1], text: st[0] }),
        el("span", { class: "adm-id-name", text: k.device_label || "(ohne Namen)" })]),
      meta, actions,
    ]);
  },
  _idPairRow(g) {

    return el("div", { class: "key-row" }, [
      el("div", { class: "key-head" }, [
        el("span", { class: "key-state warn", text: "ohne Schlüssel" }),
        el("span", { class: "adm-id-name", text: g.name || g.did || "(ohne Namen)" })]),
      el("div", { class: "key-meta muted" }, [
        el("code", { class: "tnum", text: g.did || "?" }),
        el("span", { text: " · " + (g.principal || "?") }),
        g.apikey_id ? el("span", { text: " · Maschinenschlüssel " + g.apikey_id }) : null,
        el("span", { text: " · " + this.fmtDate(g.seit) }),

        g.ausgeblendet ? el("span", { class: "adm-id-grund", text: " · aus der Geräteliste ausgeblendet, kommt aber weiterhin herein" }) : null,
      ]),
      el("div", { class: "adm-id-grund", text: "Grund: " + (g.grund || "unbekannt") }),
    ]);
  },
  async identApprove(k) {
    const d = await jpost("/api/identity/approve", { key_id: k.key_id });
    if (!d || d._neterr) { toast("Netzfehler"); return; }
    if (d.action !== "ceremony") { toast(d.error || "Freigabe nicht möglich"); return; }
    this._idCeremony(d, k);
  },
  _idCeremony(d, k) {
    const box = $("#admIdCer"); if (!box) return;
    box.hidden = false; box.textContent = "";
    const nonce = String((d.challenge && d.challenge.nonce) || "");
    const rb = d.readback || {};
    const rueck = [rb.recipient, rb.subject].concat(rb.digest || []).filter(Boolean).join(" · ");
    const eingabe = el("input", { type: "text", inputmode: "numeric", autocomplete: "off",
                                  class: "adm-id-nonce", placeholder: "Zahl hier eintippen" });
    const status = el("div", { class: "adm-id-cer-status muted" });
    const fertig = () => { box.hidden = true; box.textContent = ""; this.loadIdentity(); };
    const senden = el("button", { class: "btn sm", text: "Bestätigen", onclick: async () => {
      const r = await jpost("/api/ceremony/confirm", { re: d.re, nonce_response: eingabe.value });
      if (!r || !r.accepted) {

        status.textContent = "Nicht bestätigt (" + ((r && r.reason) || "unbekannt") + "). Bitte neu anstoßen.";
        return;
      }
      status.textContent = "Bestätigt — läuft in " + Math.round((d.hold_ms || 10000) / 1000) + " s. Abbrechen ist bis dahin möglich.";
      setTimeout(async () => {
        const s = await jget("/api/ceremony/status?re=" + encodeURIComponent(d.re));
        const erg = (s && s.result) || null;

        if (erg && erg.approved) toast("✓ " + this._idName(k) + " ist freigegeben");
        else toast("Nicht freigegeben: " + ((erg && erg.error) || (s && s.state) || "unbekannt"));
        fertig();
      }, (d.hold_ms || 10000) + 800);
    } });
    const abbruch = el("button", { class: "btn sm ghost", text: "Abbrechen", onclick: async () => {
      await jpost("/api/ceremony/cancel", { re: d.re }); toast("Gestoppt."); fertig();
    } });
    box.append(
      el("div", { class: "ke-title", text: "Freigabe bestätigen" }),
      el("div", { class: "adm-id-cer-rb", text: rueck || ("Gerät " + this._idName(k)) }),
      el("div", { class: "adm-id-cer-ask" }, [
        el("span", { text: "Zur Bestätigung diese Zahl eintippen: " }),
        el("strong", { class: "tnum adm-id-cer-nonce", text: nonce }),
      ]),
      eingabe,
      el("div", { class: "ke-actions" }, [senden, abbruch]),
      status,
    );
    try { eingabe.focus(); } catch (e) {}
  },
  async identRevoke(k) {
    if (!window.confirm("Geräteschlüssel " + this._idName(k) + " widerrufen?\n\n" +
                        "Das Gerät verliert seine Identität sofort und dauerhaft. " +
                        "Wieder hereinkommen kann es nur über eine neue Anmeldung.")) return;
    const d = await jpost("/api/identity/revoke", { key_id: k.key_id });
    if (!d || d._neterr) { toast("Netzfehler"); return; }
    toast(d.ok ? "✓ widerrufen" : ("Fehler: " + (d.error || "unbekannt")));
    this.loadIdentity();
  },

  showSecret(secret, principal, rotatedFrom) {
    const box = $("#admKeyEditor"); if (!box) return;
    this.keyEdit = null; box.hidden = false; box.textContent = "";
    box.append(
      el("div", { class: "ke-title", text: "Schlüssel für " + principal + (rotatedFrom ? " · rotiert (" + rotatedFrom + " widerrufen)" : "") }),
      el("div", { class: "ke-warn", text: "⚠ Nur JETZT sichtbar — kopieren und im Client (.env) hinterlegen." }),
      el("div", { class: "ke-secret" }, [
        el("code", { text: secret }),
        el("button", { class: "btn sm", text: "Kopieren", onclick: () => { try { navigator.clipboard.writeText(secret); toast("kopiert"); } catch (e) { toast("Kopieren nicht möglich"); } } }),
      ]),
      el("div", { class: "ke-actions" }, [el("button", { class: "btn", text: "Fertig", onclick: () => this.closeKeyEditor() })]),
    );
  }
};

function logout() { jpost("/api/logout").finally(() => location.href = "/login"); }

const Stats = {
  timer: null,
  init() {
    if (!IS_ADMIN) return;
    if (this._inited) { const nav = $("#navStats"); if (nav) nav.hidden = false; return; }
    this._inited = true;
    LENS_TITLE.stats = "📊 Statistik";
    LENSES.stats = this;
    const nav = $("#navStats"); if (nav) nav.hidden = false;
  },
  show() { this.load(); this.timer = setInterval(() => { if (Router.cur === "stats") this.load(); }, 15000); },
  hide() { if (this.timer) { clearInterval(this.timer); this.timer = null; } },
  fmt(n) { n = Math.round(n || 0); return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); },
  dur(s) { s = Math.round(s || 0); return s < 60 ? s + "s" : s < 3600 ? (s / 60).toFixed(0) + "m" : (s / 3600).toFixed(1) + "h"; },
  async load() {
    const d = await aget("/api/admin/stats");
    if (d && !d._forbidden && !d._neterr && d.ok !== false) this.render(d);
    const u = await aget("/api/admin/usage");
    if (u && !u._forbidden && !u._neterr && u.ok !== false) this.renderUsage(u);
    else if (u && !u._forbidden && !u._neterr && u.ok === false) this.renderUsageMissing(u.error || u.msg);
  },
  render(d) {
    const sum = $("#statSummary");
    if (sum) {
      sum.textContent = "";
      [["Heute", d.today], ["7 Tage", d.week], ["Gesamt", d.total]].forEach(([label, w]) => {
        w = w || {};
        sum.appendChild(el("div", { class: "stat-card" }, [
          el("div", { class: "stat-card-h", text: label }),
          el("div", { class: "stat-kv" }, [el("b", { text: this.fmt(w.llm_calls) }), el("span", { class: "muted", text: "LLM-Aufrufe" })]),
          el("div", { class: "stat-kv" }, [el("b", { text: "~" + this.fmt(w.llm_tokens_est) }), el("span", { class: "muted", text: "Token (geschätzt)" })]),
          el("div", { class: "stat-kv" }, [el("b", { text: this.fmt(w.events) }), el("span", { class: "muted", text: (w.users || 0) + " Nutzer · Aktionen" })]),
        ]));
      });
      const j = d.jobs || {};
      sum.appendChild(el("div", { class: "stat-card" }, [
        el("div", { class: "stat-card-h", text: "Aufträge" }),
        el("div", { class: "stat-kv" }, [el("b", { text: this.fmt(d.jobs_total) }), el("span", { class: "muted", text: "gesamt" })]),
        el("div", { class: "stat-kv" }, [el("b", { text: this.fmt(j.done) }), el("span", { class: "muted", text: "fertig" })]),
        el("div", { class: "stat-kv" }, [el("b", { text: this.fmt((j.queued || 0) + (j.starting || 0) + (j.running || 0)) }), el("span", { class: "muted", text: "offen" })]),
      ]));
    }
    this.renderChart(d.series || []);
    this.renderUsers((d.week && d.week.top_users) || []);
    this.renderRes(d.resources || {}, d.verbs || []);
  },
  renderChart(series) {
    const box = $("#statChart"); if (!box) return; box.textContent = "";
    const max = Math.max(1, ...series.map(s => s.llm_calls || 0));
    const bars = el("div", { class: "bars" });
    series.forEach(s => {
      const h = Math.round(((s.llm_calls || 0) / max) * 100);
      bars.appendChild(el("div", { class: "bar-col", title: s.day + ": " + (s.llm_calls || 0) + " LLM-Aufrufe, " + (s.events || 0) + " Aktionen" }, [
        el("div", { class: "bar-wrap" }, [el("i", { class: "bar", style: "height:" + Math.max(2, h) + "%" })]),
        el("span", { class: "bar-lbl muted", text: s.label }),
      ]));
    });
    box.appendChild(bars);
  },
  renderUsers(users) {
    const box = $("#statUsers"); if (!box) return; box.textContent = "";
    if (!users.length) { box.appendChild(el("div", { class: "empty", text: "keine Daten im Fenster" })); return; }
    const max = Math.max(1, ...users.map(u => u.llm_calls || 0));
    users.forEach(u => {
      const w = Math.round(((u.llm_calls || 0) / max) * 100);
      box.appendChild(el("div", { class: "stat-user-row" }, [
        el("span", { class: "stat-user-name", text: u.user }),
        el("span", { class: "stat-user-bar" }, [el("i", { style: "width:" + Math.max(3, w) + "%" })]),
        el("span", { class: "muted tnum", text: (u.llm_calls || 0) + " · ~" + this.fmt((u.llm_chars || 0) / 4) + " tok" }),
      ]));
    });
  },
  renderRes(res, verbs) {
    const box = $("#statRes");
    if (box) {
      box.textContent = "";
      const b = res.pn_batch || {}, it = res.pn_interactive || {};
      const rows = [
        ["CPU-Kerne", res.nproc != null ? (res.nproc + " (" + (res.admin_reserved_cores || 0) + " reserviert)") : "—"],
        ["Nutzer-CPU", res.tenant_cpu_pct != null ? ((res.tenant_cpu_pct / 100).toLocaleString("de", { maximumFractionDigits: 1 }) + " / " + (res.tenant_cap_pct != null ? (res.tenant_cap_pct / 100).toLocaleString("de", { maximumFractionDigits: 1 }) : "?") + " Kerne") : "—"],
        ["Batch-RAM", b.mem_mb != null ? (b.mem_mb + " MiB") : "—"],
        ["Interaktiv-RAM", it.mem_mb != null ? (it.mem_mb + " MiB") : "—"],
        ["Aktive Umgebungen", res.active_cells != null ? String(res.active_cells) : "—"],
        ["CPU-Druck PSI", b.cpu_psi_avg10 != null ? String(b.cpu_psi_avg10) : "—"],
      ];
      rows.forEach(([k, v]) => box.appendChild(el("div", { class: "stat-res-row" }, [
        el("span", { class: "muted", text: k }), el("b", { class: "tnum", text: String(v) })])));
    }
    const vb = $("#statVerbs");
    if (vb) {
      vb.textContent = "";
      vb.appendChild(el("div", { class: "stat-verbs-h muted", text: "Aktionstypen (gesamt)" }));
      (verbs || []).slice(0, 8).forEach(v => vb.appendChild(el("div", { class: "stat-verb-row tnum" }, [
        el("span", { text: v.verb }), el("span", { class: "muted", text: String(v.count) })])));
    }
  },

  renderUsage(u) {
    const box = $("#statUsage");
    if (box) {
      box.textContent = "";
      const users = (u.week && u.week.top_users) || [];
      if (!users.length) { box.appendChild(el("div", { class: "empty", text: "keine Nutzungsdaten (7 Tage)" })); }
      else {
        const max = Math.max(1, ...users.map(x => x.wall_s || 0));
        users.forEach(x => {
          const w = Math.round(((x.wall_s || 0) / max) * 100);
          const meta = [this.dur(x.wall_s) + " Rechenzeit", (x.jobs || 0) + " Jobs", (x.llm_calls || 0) + " LLM"];
          if (u.have_cpu && x.cpu_s) meta.push(this.dur(x.cpu_s) + " CPU");
          if (u.have_mem && x.mem_peak) meta.push(Math.round(x.mem_peak) + " MiB");
          box.appendChild(el("div", { class: "stat-user-row" }, [
            el("span", { class: "stat-user-name", text: x.user }),
            el("span", { class: "stat-user-bar" }, [el("i", { style: "width:" + Math.max(3, w) + "%" })]),
            el("span", { class: "muted tnum", text: meta.join(" · ") }),
          ]));
        });
      }
    }
    const cbox = $("#statResChart");
    if (cbox) {
      cbox.textContent = "";
      const series = u.series || [];
      const useCpu = u.have_cpu && series.some(s => (s.cpu_s || 0) > 0);
      const key = useCpu ? "cpu_s" : "wall_s";
      const max = Math.max(1, ...series.map(s => s[key] || 0));
      const bars = el("div", { class: "bars" });
      series.forEach(s => {
        const h = Math.round(((s[key] || 0) / max) * 100);
        bars.appendChild(el("div", { class: "bar-col", title: s.day + ": " + this.dur(s[key] || 0) + " · " + (s.jobs || 0) + " Jobs" }, [
          el("div", { class: "bar-wrap" }, [el("i", { class: "bar", style: "height:" + Math.max(2, h) + "%" })]),
          el("span", { class: "bar-lbl muted", text: s.label }),
        ]));
      });
      cbox.appendChild(bars);
    }
    const note = $("#statResNote");
    if (note) note.textContent = (u.have_cpu ? "CPU-Sekunden pro Tag" : "Rechenzeit (Wall-Sek.) pro Tag · CPU/RAM noch spärlich erfasst");
  },

  renderUsageMissing(msg) {
    const t = "⚠ " + (msg || "Verbrauchsdaten fehlen: der Abrechnungsdienst (pn-acctd) läuft nicht.");
    const box = $("#statUsage");
    if (box) { box.textContent = ""; box.appendChild(el("div", { class: "empty", text: t })); }
    const cbox = $("#statResChart");
    if (cbox) { cbox.textContent = ""; cbox.appendChild(el("div", { class: "empty", text: t })); }
    const note = $("#statResNote");
    if (note) note.textContent = "keine Daten";
  },
};

const Software = {
  loaded: false, open: null,
  show() { this.load(); },
  hide() {},
  esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); },
  async load() {
    const mount = $("#swMount"); if (!mount) return;
    let cat, cards, wcat;
    try { cat = await jget("/api/store/catalog"); } catch (e) { cat = null; }
    try { cards = await jget("/api/store/cards"); } catch (e) { cards = null; }
    try { wcat = await jget("/api/widgets/catalog"); } catch (e) { wcat = null; }
    if (!cat || !cat.ok) { mount.innerHTML = '<div class="sw-empty">Regal nicht erreichbar.</div>'; return; }
    this.kits = cat.kits || {}; this.onboard = cat.onboard || {}; this.cards = (cards && cards.cards) || {};
    this.addons = (wcat && wcat.ok !== false) ? wcat : null;
    this.render();
    if (typeof IS_ADMIN !== "undefined" && IS_ADMIN) this.loadWarden();
  },
  async loadWarden() {
    let d; try { d = await jget("/api/admin/store/warden-peek"); } catch (e) { return; }
    const host = $("#swWarden"); if (!host || !d) return;
    if (d.cell === "warm") {
      const t = (d.timers && d.timers[0]) || null;
      const last = (d.aufsicht || "").split("\n").find(l => l.startsWith("## ")) || "";
      host.innerHTML = "🛡 <b>Shelf Warden</b> läuft" + (t ? " · Aufsicht alle " + Math.round((t.every_s || 0) / 3600) + "h" : "")
        + (last ? " · letzter Lauf " + this.esc(last.replace(/^##\s*/, "")) : "");
    } else {
      host.innerHTML = "🛡 <b>Shelf Warden</b>: " + this.esc(d.cell || "unbekannt") + " — im Sessions-Board sichtbar";
    }
    host.hidden = false;
  },
  lifecycle(kid) {
    const ob = this.onboard[kid] || {}; const k = this.kits[kid] || {};
    const card = this.cards[kid];
    const verified = card ? ((((card.manual || {}).programs) || []).some(p => (p.recipes || []).some(r => r.verified))) : false;
    if (ob.state === "running") return { cls: "run", txt: "Einarbeitung läuft…" };
    if (verified) return { cls: "ok", txt: "Bedienbar" };
    if (k.installed || k.discovered) return { cls: "inst", txt: "Installiert" };
    return { cls: "", txt: "Verfügbar" };
  },
  purpose(kid) {
    const card = this.cards[kid]; const k = this.kits[kid] || {};
    if (card) { const p = (((card.manual || {}).programs) || [])[0]; if (p && p.purpose) return p.purpose; }
    return k.zweck || k.purpose || "";
  },

  match(q, hay) { return !q || String(hay).toLowerCase().indexOf(q) >= 0; },
  kitHay(kid) {
    const k = this.kits[kid] || {}; const card = this.cards[kid];
    const progs = card ? (((card.manual || {}).programs) || []).map(p => (p.name || "") + " " + (p.purpose || "")) : [];
    return [kid, this.purpose(kid), (k.tools || []).join(" ")].concat(progs).join(" ");
  },
  kitCard(kid) {
    const k = this.kits[kid] || {}; const lc = this.lifecycle(kid); const card = this.cards[kid];
    const nprog = card ? (((card.manual || {}).programs) || []).length : (k.program_count || (k.tools ? k.tools.length : 0));
    let h = '<div class="sw-card" data-kid="' + this.esc(kid) + '">'
      + '<h3>' + this.esc(kid) + (k.kind === "conda" ? '<span class="sw-kind">conda</span>' : (k.discovered ? '<span class="sw-kind">kit</span>' : '')) + '</h3>'
      + '<div class="sw-purpose">' + this.esc(this.purpose(kid)) + '</div>'
      + '<div class="sw-meta"><span class="sw-chip ' + lc.cls + '">' + lc.txt + '</span>'
      + (nprog ? '<span>' + nprog + ' Programme</span>' : '')
      + (k.bin_count ? '<span>' + k.bin_count + ' Binaries</span>' : '')
      + (k.version ? '<span>' + this.esc(String(k.version)) + '</span>' : '') + '</div>';
    if (this.open === kid) h += this.detail(kid);
    return h + '</div>';
  },
  renderAddons(q) {
    const d = this.addons;
    const builtins = Object.keys(WIDGETS).filter(w => !WIDGETS[w].addon).map(w => ({
      id: w, title: WIDGETS[w].title, desc: "Widget für die Seitenleiste (vorinstalliert)", kind: "builtin" }));
    const inst = ((d && d.installed) || []).map(a => ({ id: a.id,
      title: (a.manifest && a.manifest.title) || a.id, desc: (a.manifest && a.manifest.description) || "",
      kind: a.enabled ? "on" : "off" }));
    const avail = ((d && d.available) || []).map(a => ({ id: a.id,
      title: (a.manifest && a.manifest.title) || a.id, desc: (a.manifest && a.manifest.description) || "",
      kind: "avail" }));
    const all = builtins.concat(inst, avail).filter(x => this.match(q, x.id + " " + x.title + " " + x.desc));
    if (!all.length) return "";
    let h = '<div class="sw-sec">🧩 Widgets &amp; Add-ons <span class="sw-count">' + all.length + '</span>'
      + '<button class="btn sm" data-act="waCat">Katalog öffnen</button></div>'
      + '<div class="sw-secsub">Bausteine für die rechte Seitenleiste — Betriebs-Inhalte (Watchdog, laufende Aufträge, Web-Wächter) erscheinen NUR dort, auf Wunsch per ＋ ablegbar.</div>'
      + '<div class="sw-grid">';
    for (const x of all) {
      const chip = x.kind === "builtin" ? '<span class="sw-chip inst">Vorinstalliert</span>'
        : x.kind === "on" ? '<span class="sw-chip ok">Installiert · aktiv</span>'
        : x.kind === "off" ? '<span class="sw-chip">Installiert · aus</span>'
        : '<span class="sw-chip">Verfügbar</span>';
      h += '<div class="sw-card sw-addon"><h3>' + this.esc(x.title) + '</h3>'
        + '<div class="sw-purpose">' + this.esc(x.desc) + '</div>'
        + '<div class="sw-meta">' + chip + '</div></div>';
    }
    return h + '</div>';
  },
  render() {
    const mount = $("#swMount"); if (!mount) return;
    const q = (this._q || "").trim().toLowerCase();
    const ids = Object.keys(this.kits).filter(kid => this.match(q, this.kitHay(kid))).sort((a, b) => {
      const la = this.lifecycle(a).cls === "ok" ? 0 : 1, lb = this.lifecycle(b).cls === "ok" ? 0 : 1;
      return la - lb || a.localeCompare(b);
    });
    const instIds = ids.filter(kid => { const c = this.lifecycle(kid).cls; return c === "ok" || c === "inst" || c === "run"; });
    const availIds = ids.filter(kid => instIds.indexOf(kid) < 0);
    let h = '<div class="sw-head"><h2>📦 Software</h2>'
      + '<div class="sw-sub">Was auf der Box installiert ist — und was es zusätzlich gibt. Betrieb &amp; Verlauf laufender Aufträge: Work-Reiter bzw. Widgets in der Seitenleiste.</div>'
      + '<input id="swSearch" class="sw-search" placeholder="🔎 Suchen: Kisten, Programme, Widgets, Add-ons …" value="' + this.esc(this._q || "") + '">'
      + '<div class="sw-warden" id="swWarden" hidden></div></div>';
    h += '<div class="sw-sec">📀 Auf der Box <span class="sw-count">' + instIds.length + '</span></div>';
    h += instIds.length ? '<div class="sw-grid">' + instIds.map(k => this.kitCard(k)).join("") + '</div>'
      : '<div class="sw-empty">' + (q ? "Keine installierte Kiste passt zur Suche." : "Noch keine Kisten installiert.") + '</div>';
    h += this.renderAddons(q);
    if (availIds.length) {
      h += '<div class="sw-sec">🛒 Verfügbare Kisten <span class="sw-count">' + availIds.length + '</span></div>'
        + '<div class="sw-grid">' + availIds.map(k => this.kitCard(k)).join("") + '</div>';
    }
    h += this.renderOpen();
    mount.innerHTML = h;
    const s = $("#swSearch");
    if (s) s.oninput = () => {
      this._q = s.value;
      clearTimeout(this._qT);
      this._qT = setTimeout(() => {
        const p = s.selectionStart;
        this.render();
        const s2 = $("#swSearch");
        if (s2) { s2.focus(); try { s2.setSelectionRange(p, p); } catch (e) {} }
      }, 200);
    };
    const wc = mount.querySelector("[data-act='waCat']");
    if (wc) wc.onclick = () => WA.openCatalog();
    mount.querySelectorAll(".sw-addon").forEach(c => c.addEventListener("click", () => WA.openCatalog()));
    this.wireOpen();
    mount.querySelectorAll(".sw-card[data-kid]").forEach(c => {
      c.addEventListener("click", e => {
        if (e.target.closest("[data-act]")) return;
        const kid = c.getAttribute("data-kid");
        this.open = this.open === kid ? null : kid; this.render();
        if (typeof IS_ADMIN !== "undefined" && IS_ADMIN) this.loadWarden();
      });
    });
    mount.querySelectorAll("[data-kitequip]").forEach(b => {
      b.addEventListener("click", e => { e.stopPropagation(); this.equipKit(b.getAttribute("data-kitequip"), b); });
    });
    mount.querySelectorAll("[data-act='reonboard']").forEach(b => {
      b.addEventListener("click", async e => {
        e.stopPropagation();
        const kid = b.getAttribute("data-kid"); b.disabled = true; b.textContent = "startet…";
        try { const r = await jpost("/api/admin/store/onboard", { kit: kid }); toast(r && r.ok ? "Einarbeitung gestartet — sichtbar im Sessions-Board" : ((r && r.error) || "abgelehnt")); }
        catch (err) { toast("Fehler"); }
        setTimeout(() => this.load(), 1500);
      });
    });
    if (typeof IS_ADMIN !== "undefined" && IS_ADMIN) this.loadWarden();
  },

  sessOptions() {
    const sess = this._sessions;
    if (sess == null) return '<option value="">… lädt …</option>';
    return sess.length
      ? sess.map(s => '<option value="' + this.esc(s.id) + '">' + this.esc(s.title) + '</option>').join("")
      : '<option value="">— keine Session (erst eine anlegen) —</option>';
  },
  equipRow(kid) {
    const k = this.kits[kid] || {};
    if (!(k.installed || k.discovered))
      return '<div class="sw-equip" style="margin-top:10px;font-size:12px;opacity:.7">Diese Kiste ist noch nicht gebaut — erst bauen, dann ausstatten.</div>';
    return '<div class="sw-equip" data-act="kitEquip" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:10px;font-size:12px">'
      + '<span style="opacity:.75">🧰 An Session ausstatten:</span>'
      + '<select data-act="kitEquip" class="swKitSess" style="flex:1;min-width:150px;padding:5px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      + this.sessOptions() + '</select>'
      + '<button class="btn sm primary" data-act="kitEquip" data-kitequip="' + this.esc(kid) + '">Ausstatten (Neustart)</button>'
      + '<span style="opacity:.6;flex-basis:100%">Wird beim Start der Zelle schreibgeschützt unter <code>/opt/kits/' + this.esc(kid) + '/bin</code> eingehängt — die Session startet dafür neu, der Verlauf bleibt.</span></div>';
  },
  async equipKit(kid, btn) {
    const row = btn.closest(".sw-equip"), sel = row ? row.querySelector(".swKitSess") : null;
    const sid = sel ? sel.value : "";
    if (!sid) { toast("Erst eine Ziel-Session wählen"); return; }
    const t0 = btn.textContent; btn.disabled = true; btn.textContent = "stattet aus…";
    let cur = [];
    try {
      const b = await jget("/api/session/board");
      const s = ((b && b.sessions) || []).find(x => x.sid === sid);
      cur = (s && s.kits) || [];
    } catch (e) {}
    if (cur.indexOf(kid) >= 0) { toast("Diese Session hat " + kid + " bereits"); btn.disabled = false; btn.textContent = t0; return; }
    let r; try { r = await jpost("/api/session/provision", { sid, kits: cur.concat([kid]), restart: true }); } catch (e) { r = null; }
    if (!(r && r.ok)) toast((r && r.error) || "Ausstatten fehlgeschlagen");
    else if (r.kits_unknown && r.kits_unknown.length) toast("Nicht ausgestattet: " + r.kits_unknown.join(", "));
    else toast("🧰 " + kid + " ausgestattet — Session startet neu");
    btn.disabled = false; btn.textContent = t0;
  },
  detail(kid) {
    const card = this.cards[kid];
    if (!card) return '<div class="sw-detail">' + this.equipRow(kid) + '<div class="sw-empty">Noch keine Bedien-Kartei — ein Agent erkundet die Kiste erst.</div>' + this.adminBtns(kid) + '</div>';
    const progs = ((card.manual || {}).programs) || [];
    let h = '<div class="sw-detail">';
    for (const p of progs) {
      h += '<div class="sw-prog"><h4>' + this.esc(p.name || "?") + '<span class="pm">' + this.esc(p.modality || "cli") + '</span></h4>';
      if (p.purpose) h += '<p>' + this.esc(p.purpose) + '</p>';
      if (p.capabilities && p.capabilities.length) h += '<p>Kann: ' + this.esc(p.capabilities.join("; ")) + '</p>';
      for (const r of (p.recipes || [])) {
        if (!r.verified) continue;
        h += '<div class="sw-recipe" title="' + this.esc(r.goal || "") + '">$ ' + this.esc(r.command || "") + '</div>';
      }
      h += '</div>';
    }
    const when = card.at ? new Date(card.at * 1000).toLocaleString() : "";
    h += this.equipRow(kid);
    h += '<div class="sw-prov">🔎 Erkundet von einem Agenten' + (card.written_by ? " (" + this.esc(card.written_by) + ")" : "") + (when ? " am " + this.esc(when) : "") + " — die Kommandos oben wurden real ausgeführt und verifiziert.</div>";
    h += this.adminBtns(kid) + '</div>';
    return h;
  },

  renderOpen() {
    return '<div class="sw-open">'
      + '<div style="border:1px solid rgba(140,150,170,.25);border-radius:12px;padding:12px 14px;margin:14px 0">'
      +   '<h3 style="margin:0 0 4px;font-size:15px">🧱 Drei Wege zu Software — welcher wann</h3>'
      +   '<div style="font-size:12px;opacity:.8;line-height:1.55">'
      +     '<b>1. Werkzeug-Kiste (oben):</b> fertig gebaut, schreibgeschützt, kostet der Session keinen Platz und ist beim Start sofort da — inklusive Bedienungsanleitung. Für große Toolchains der beste Weg.<br>'
      +     '<b>2. Der Agent installiert selbst:</b> in jeder Zelle laufen <code>apt</code>, <code>pip3</code> und <code>sudo</code> (Paketschicht <code>pn-pkg</code>). Landet im dauerhaften Delta der Session — ideal zum Ausprobieren einzelner Programme.<br>'
      +     '<b>3. Flathub-App (unten):</b> grafische Programme in der Office-Zelle einer Session mit aktivem Desktop.'
      +   '</div>'
      + '</div>'
      + '<div style="border:1px solid rgba(140,150,170,.25);border-radius:12px;padding:12px 14px;margin:14px 0">'
      +   '<h3 style="margin:0 0 4px;font-size:15px">🔎 App suchen &amp; installieren</h3>'
      +   '<div style="font-size:12px;opacity:.75;margin-bottom:8px">Durchsuche den großen Flathub-Katalog und installiere direkt in die Office-Zelle einer Session (die Session braucht aktiven Desktop + Internet).</div>'
      +   '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">'
      +     '<input id="swAppQ" placeholder="z. B. firefox, vlc, gimp …" style="flex:1;min-width:180px;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      +     '<button class="btn sm primary" data-act="swAppGo">Suchen</button>'
      +   '</div>'
      +   '<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;font-size:12px">'
      +     '<span style="opacity:.7">Ziel-Session:</span>'
      +     '<select id="swAppSess" style="flex:1;min-width:160px;padding:5px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit"></select>'
      +   '</div>'
      +   '<div id="swAppResults"></div>'
      + '</div>'
      + '<div style="border:1px solid rgba(140,150,170,.25);border-radius:12px;padding:12px 14px;margin:14px 0">'
      +   '<h3 style="margin:0 0 4px;font-size:15px">🧩 MCP-Server hinzufügen</h3>'
      +   '<div style="font-size:12px;opacity:.75;margin-bottom:8px">Registriere einen Model-Context-Protocol-Server (stdio-Befehl oder http/sse-URL). Wird pro Nutzer gespeichert. <b>Hinweis:</b> die Registry hält deine Auswahl fest; das automatische Einspeisen in laufende Zellen ist noch offen.</div>'
      +   '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">'
      +     '<input id="swMcpName" placeholder="Name" style="width:130px;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      +     '<select id="swMcpTransport" style="padding:6px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      +       '<option value="stdio">stdio (Befehl)</option><option value="http">http (URL)</option><option value="sse">sse (URL)</option>'
      +     '</select>'
      +     '<input id="swMcpTarget" placeholder="npx -y @modelcontextprotocol/server-… oder https://…" style="flex:1;min-width:180px;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      +     '<button class="btn sm primary" data-act="swMcpAdd">Hinzufügen</button>'
      +   '</div>'
      +   '<div id="swMcpList"></div>'
      +   '<div style="display:flex;gap:8px;margin-top:10px;align-items:center">'
      +     '<input id="swMcpQ" placeholder="Katalog durchsuchen (z. B. git, browser, sqlite) …" style="flex:1;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit">'
      +     '<button class="btn sm" data-act="swMcpSearch">🔍 Katalog</button></div>'
      +   '<div id="swMcpCat"></div>'
      + '</div>'
      + '</div>';

  },
  wireOpen() {
    const q = $("#swAppQ");
    if (q) {
      q.value = this._appq || "";
      q.oninput = () => { this._appq = q.value; };
      q.onkeydown = e => { if (e.key === "Enter") this.doSearch(); };
    }
    const go = $("[data-act='swAppGo']"); if (go) go.onclick = () => this.doSearch();
    this.fillSess();
    this.fillResults();
    const add = $("[data-act='swMcpAdd']"); if (add) add.onclick = () => this.addMcp();
    const cs = $("[data-act='swMcpSearch']"); if (cs) cs.onclick = () => this.searchMcp();
    const cq = $("#swMcpQ");
    if (cq) {
      cq.addEventListener("keydown", (e) => { if (e.key === "Enter") this.searchMcp(); });

      cq.addEventListener("input", () => {
        clearTimeout(this._mcpQT);
        this._mcpQT = setTimeout(() => this.searchMcp(), 300);
      });
    }
    this.fillMcp();
    if (!this._sessLoaded) { this._sessLoaded = true; this.loadSessions(); }
    if (!this._mcpLoaded) { this._mcpLoaded = true; this.loadMcp(); }
  },
  async loadSessions() {
    let r; try { r = await jget("/api/sessions"); } catch (e) { return; }
    this._sessions = ((r && r.ok && r.sessions) || []).filter(s => !s.archived)
      .map(s => ({ id: s.sid || s.id, title: s.title || s.sid || s.id }));
    this.fillSess();
  },
  fillSess() {
    const sel = $("#swAppSess"); if (!sel) return;
    const sess = this._sessions;
    if (sess == null) { sel.innerHTML = '<option value="">… lädt …</option>'; return; }
    sel.innerHTML = sess.length
      ? sess.map(s => '<option value="' + this.esc(s.id) + '">' + this.esc(s.title) + '</option>').join("")
      : '<option value="">— keine Session (erst eine anlegen) —</option>';
    if (this._appSess && sess.some(s => s.id === this._appSess)) sel.value = this._appSess;
    sel.onchange = () => { this._appSess = sel.value; };
    this._appSess = sel.value;
  },
  async doSearch() {
    const q = (($("#swAppQ") || {}).value || this._appq || "").trim();
    const box = $("#swAppResults"); if (!box) return;
    if (q.length < 2) { box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Suchbegriff zu kurz.</div>'; return; }
    box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Suche …</div>';
    let r; try { r = await jget("/api/session/appsearch?q=" + encodeURIComponent(q)); } catch (e) { r = null; }
    if (!r || !r.ok) { this._appResults = []; box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">' + this.esc((r && r.error) || "Suche fehlgeschlagen") + '</div>'; return; }
    this._appResults = r.results || [];
    this.fillResults();
  },
  fillResults() {
    const box = $("#swAppResults"); if (!box) return;
    const res = this._appResults;
    if (res == null) { box.innerHTML = ""; return; }
    if (!res.length) { box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Nichts gefunden.</div>'; return; }
    box.innerHTML = res.map(a => '<div class="sw-prog" style="display:flex;gap:8px;align-items:center">'
      + '<div style="flex:1;min-width:0"><b>' + this.esc(a.name) + '</b>'
      + '<div style="font-size:11.5px;opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + this.esc(a.summary || a.id) + '</div></div>'
      + '<button class="btn sm primary" data-appinstall="' + this.esc(a.id) + '">Installieren</button></div>').join("");
    box.querySelectorAll("[data-appinstall]").forEach(b => b.onclick = () => this.doInstall(b.getAttribute("data-appinstall"), b));
  },
  async doInstall(appId, btn) {
    const sid = this._appSess || ($("#swAppSess") || {}).value || "";
    if (!sid) { toast("Erst eine Ziel-Session wählen (Desktop aktiv)"); return; }
    if (btn) { btn.disabled = true; btn.textContent = "startet…"; }
    let r; try { r = await jpost("/api/session/app", { sid, op: "install", app_id: appId }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.reason) || "Installation nicht möglich"); if (btn) { btn.disabled = false; btn.textContent = "Installieren"; } return; }
    toast("🛍 Installation gestartet — kann einige Minuten dauern");
    clearInterval(this._appPoll);
    this._appPoll = setInterval(async () => {
      let st; try { st = await jget("/api/session/app?sid=" + encodeURIComponent(sid)); } catch (e) { return; }
      if (!st || st.phase === "laeuft") return;
      clearInterval(this._appPoll);
      if (st.phase === "fehler") toast("🛍 Fehlgeschlagen: " + String(st.error || "").slice(0, 80));
      else toast("🛍 Installation fertig");
      if (btn) { btn.disabled = false; btn.textContent = "Installieren"; }
    }, 3000);
  },
  async loadMcp() {
    let r; try { r = await jget("/api/mcp/servers"); } catch (e) { return; }
    this._mcpServers = (r && r.ok && r.servers) || [];
    this.fillMcp();
  },
  fillMcp() {
    const box = $("#swMcpList"); if (!box) return;
    const mcp = this._mcpServers;
    if (mcp == null) { box.innerHTML = ""; return; }
    box.innerHTML = mcp.length
      ? mcp.map(m => '<div class="sw-prog" style="display:flex;gap:8px;align-items:center">'
          + '<div style="flex:1;min-width:0"><b>' + this.esc(m.name) + '</b> <span class="pm">' + this.esc(m.transport || "stdio") + '</span>'
          + '<div style="font-size:11.5px;opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + this.esc(m.command || m.url || "") + '</div></div>'
          + '<button class="btn sm danger" data-mcprm="' + this.esc(m.id) + '">Entfernen</button></div>').join("")
      : '<div class="sw-empty" style="padding:8px 2px">Noch keine MCP-Server hinterlegt.</div>';
    box.querySelectorAll("[data-mcprm]").forEach(b => b.onclick = () => this.delMcp(b.getAttribute("data-mcprm")));
  },
  async addMcp() {
    const name = ($("#swMcpName") || {}).value || "";
    const transport = ($("#swMcpTransport") || {}).value || "stdio";
    const target = ($("#swMcpTarget") || {}).value || "";
    if (!name.trim() || !target.trim()) { toast("Name und Befehl/URL nötig"); return; }
    let r; try { r = await jpost("/api/mcp/servers", { name, transport, target }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Konnte nicht hinzufügen"); return; }
    toast("🧩 MCP-Server hinzugefügt");
    const n = $("#swMcpName"); if (n) n.value = "";
    const t = $("#swMcpTarget"); if (t) t.value = "";
    this.loadMcp();
  },
  async searchMcp() {
    const q = (($("#swMcpQ") || {}).value || "").trim();
    const box = $("#swMcpCat"); if (!box) return;
    box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Suche …</div>';
    const seq = (this._mcpSeq = (this._mcpSeq || 0) + 1);
    let r; try { r = await jget("/api/mcp/catalog?q=" + encodeURIComponent(q)); } catch (e) { r = null; }
    if (seq !== this._mcpSeq) return;
    const cat = (r && r.ok && r.catalog) || [];
    if (!cat.length) { box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Nichts gefunden.</div>'; return; }
    this._mcpCat = cat;
    box.innerHTML = cat.slice(0, 12).map((c, i) => '<div class="sw-prog" style="display:flex;gap:8px;align-items:center">'
      + '<div style="flex:1;min-width:0"><b>' + this.esc(c.name || c.id) + '</b>'
      + '<div style="font-size:11.5px;opacity:.7">' + this.esc(c.description || "") + '</div></div>'
      + '<button class="btn sm" data-mcptake="' + i + '">Übernehmen</button></div>').join("");
    box.querySelectorAll("[data-mcptake]").forEach(b => b.onclick = () => this.takeMcp(parseInt(b.getAttribute("data-mcptake"), 10)));
  },
  async takeMcp(i) {
    const c = (this._mcpCat || [])[i]; if (!c) return;
    const inst = c.install || {};
    const transport = (inst.type === "http" || inst.type === "sse") ? inst.type : "stdio";
    const target = inst.url || [inst.command].concat(inst.args || []).filter(Boolean).join(" ");
    if (!target) { toast("Eintrag hat kein Installationsziel"); return; }
    let r; try { r = await jpost("/api/mcp/servers", { name: c.name || c.id, transport, target }); } catch (e) { r = null; }
    if (r && r.ok) toast("🧩 " + (c.name || c.id) + " übernommen");
    else toast((r && r.error) || "Konnte nicht übernehmen");
    this.loadMcp();
  },
  async delMcp(id) {
    let r; try { r = await jpost("/api/mcp/servers/delete", { id }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Konnte nicht entfernen"); return; }
    toast("Entfernt");
    this.loadMcp();
  },

  async loadA2A() {
    let r; try { r = await jget("/api/alert2action"); } catch (e) { return; }
    this._a2aWatches = (r && r.ok && r.watches) || [];
    this.fillA2A();
  },
  a2aChip(kind, val) {
    const map = {
      phase: { ARMED: ["scharf", "#22c55e"], PENDING: ["prüft…", "#eab308"], DISARMED: ["ausgelöst", "#94a3b8"], COOLDOWN: ["Abklingzeit", "#38bdf8"] },
      health: { ok: ["ok", "#22c55e"], selector_empty: ["blind (leer)", "#eab308"], banned_suspected: ["blockiert?", "#ef4444"], http_error: ["HTTP-Fehler", "#ef4444"], fetch_error: ["Netzfehler", "#ef4444"], unknown: ["—", "#94a3b8"], blocked: ["gesperrt", "#ef4444"] }
    };
    const e = (map[kind] || {})[val] || [val || "?", "#94a3b8"];
    return '<span style="display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;border:1px solid ' + e[1] + '55;color:' + e[1] + '">' + this.esc(e[0]) + '</span>';
  },
  fillA2A() {

    const box = (this._a2aHost && this._a2aHost.isConnected) ? this._a2aHost : $("#a2aList");
    if (!box) return;
    const ws = this._a2aWatches;
    if (ws == null) { box.innerHTML = ""; return; }
    if (!ws.length) { box.innerHTML = '<div class="sw-empty" style="padding:8px 2px">Noch keine Web-Wächter angelegt.</div>'; return; }
    box.innerHTML = ws.map(w => {
      const sig = w.signal || {};
      const sigTxt = sig.kind === "text_contains" ? ('Text enthält „' + this.esc(sig.pattern || "") + '"')
        : sig.kind === "text_absent" ? ('Text fehlt „' + this.esc(sig.pattern || "") + '"')
        : sig.kind === "number_cmp" ? ('Zahl ' + this.esc((sig.number || {}).op || "<") + ' ' + this.esc(String((sig.number || {}).value ?? "")))
        : sig.kind === "diff_any" ? "jede Änderung"
        : sig.kind === "llm" ? ('KI: ' + this.esc((sig.llm || {}).condition || "")) : this.esc(sig.kind || "");
      const act = (w.action || {}).kind === "agent_session" ? "🤖 Agent-Session (Freigabe nötig)" : "📨 Benachrichtigen";
      const pend = (w.pending || []).map(p => '<div style="margin-top:6px;padding:6px 8px;border:1px solid #eab30855;border-radius:8px;background:#eab30810;font-size:12px">⚠ <b>Freigabe angefragt</b> (' + fmtWhen(p.at) + ')' + (p.evidence ? ' — Beleg: „' + this.esc(String(p.evidence).slice(0, 120)) + '"' : '')
        + '<div style="margin-top:5px;display:flex;gap:6px"><button class="btn sm primary" data-a2aok="' + this.esc(w.id) + '|' + this.esc(p.id) + '">Freigeben</button>'
        + '<button class="btn sm" data-a2ano="' + this.esc(w.id) + '|' + this.esc(p.id) + '">Verwerfen</button></div></div>').join("");
      return '<div class="sw-prog" style="display:block;padding:8px 2px;border-top:1px solid rgba(140,150,170,.15)">'
        + '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
        +   '<b style="flex:1;min-width:120px">' + (w.enabled ? '' : '⏸ ') + this.esc(w.name || "") + '</b>'
        +   this.a2aChip("phase", w.phase) + ' ' + this.a2aChip("health", w.health)
        + '</div>'
        + '<div style="font-size:11.5px;opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + this.esc(w.url || "") + '</div>'
        + '<div style="font-size:12px;margin-top:2px">' + sigTxt + ' · ' + act + ' · alle ' + this.a2aInterval(w.interval_s) + (w.last_check ? ' · zuletzt ' + fmtWhen(w.last_check) : ' · noch nie geprüft') + '</div>'
        + pend
        + '<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">'
        +   '<button class="btn sm" data-a2atest="' + this.esc(w.id) + '">Jetzt prüfen</button>'
        +   '<button class="btn sm" data-a2aedit="' + this.esc(w.id) + '">Bearbeiten</button>'
        +   '<button class="btn sm" data-a2apause="' + this.esc(w.id) + '|' + (w.enabled ? '0' : '1') + '">' + (w.enabled ? 'Pause' : 'Fortsetzen') + '</button>'
        +   (w.phase === "DISARMED" || w.phase === "COOLDOWN" ? '<button class="btn sm" data-a2arearm="' + this.esc(w.id) + '">Neu schärfen</button>' : '')
        +   '<button class="btn sm danger" data-a2adel="' + this.esc(w.id) + '">Löschen</button>'
        + '</div></div>';
    }).join("");
    box.querySelectorAll("[data-a2atest]").forEach(b => b.onclick = () => this.testA2A(b.getAttribute("data-a2atest"), b));
    box.querySelectorAll("[data-a2aedit]").forEach(b => b.onclick = () => this.a2aForm((this._a2aWatches || []).find(x => x.id === b.getAttribute("data-a2aedit"))));
    box.querySelectorAll("[data-a2apause]").forEach(b => b.onclick = () => { const [id, en] = b.getAttribute("data-a2apause").split("|"); this.pauseA2A(id, en === "1"); });
    box.querySelectorAll("[data-a2arearm]").forEach(b => b.onclick = () => this.rearmA2A(b.getAttribute("data-a2arearm")));
    box.querySelectorAll("[data-a2adel]").forEach(b => b.onclick = () => this.delA2A(b.getAttribute("data-a2adel")));
    box.querySelectorAll("[data-a2aok]").forEach(b => b.onclick = () => { const [id, pid] = b.getAttribute("data-a2aok").split("|"); this.approveA2A(id, pid); });
    box.querySelectorAll("[data-a2ano]").forEach(b => b.onclick = () => { const [id, pid] = b.getAttribute("data-a2ano").split("|"); this.dismissA2A(id, pid); });
  },
  a2aInterval(s) {
    s = +s || 0;
    if (s % 86400 === 0 && s >= 86400) return (s / 86400) + " Tag(e)";
    if (s % 3600 === 0 && s >= 3600) return (s / 3600) + " h";
    if (s % 60 === 0) return (s / 60) + " Min";
    return s + " s";
  },
  a2aForm(existing) {
    const w = existing || {};
    const sig = w.signal || {}, act = w.action || {};
    const inp = (v, ph) => el("input", { class: "a2a-in", value: v == null ? "" : String(v), placeholder: ph || "",
      style: "width:100%;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit;box-sizing:border-box" });
    const lbl = t => el("div", { text: t, style: "font-size:12px;opacity:.75;margin:8px 0 3px" });
    const name = inp(w.name, "z. B. Anmeldung Kurs XY");
    const url = inp(w.url, "https://…");
    const interval = el("select", { class: "a2a-in", style: "width:100%;padding:6px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit" });
    [[300, "alle 5 Minuten"], [900, "alle 15 Minuten"], [3600, "stündlich"], [21600, "alle 6 Stunden"], [86400, "täglich"]].forEach(([v, t]) => {
      const o = el("option", { value: v, text: t }); if ((w.interval_s || 900) == v) o.selected = true; interval.append(o);
    });
    const skind = el("select", { class: "a2a-in", style: "width:100%;padding:6px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit" });
    [["text_contains", "Text erscheint"], ["text_absent", "Text verschwindet"], ["number_cmp", "Zahl vergleichen"], ["diff_any", "irgendeine Änderung"], ["llm", "KI-Bedingung (diff-gated)"]].forEach(([v, t]) => {
      const o = el("option", { value: v, text: t }); if ((sig.kind || "text_contains") === v) o.selected = true; skind.append(o);
    });
    const pattern = inp(sig.pattern, "gesuchter Text, z. B. 'Anmeldung offen'");
    const numOp = el("select", { class: "a2a-in", style: "padding:6px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit" });
    ["<", "<=", ">", ">=", "=="].forEach(op => { const o = el("option", { value: op, text: op }); if (((sig.number || {}).op || "<") === op) o.selected = true; numOp.append(o); });
    const numVal = inp((sig.number || {}).value, "Zahl");
    numVal.style.width = "120px";
    const numRow = el("div", { style: "display:flex;gap:6px;align-items:center" }, [numOp, numVal]);
    const llmCond = inp((sig.llm || {}).condition, "Bedingung in Worten, z. B. 'ein Termin ist frei geworden'");
    const akind = el("select", { class: "a2a-in", style: "width:100%;padding:6px 8px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit" });
    [["notify", "📨 Nur benachrichtigen (Bus + E-Mail)"], ["agent_session", "🤖 Agent-Session vorbereiten (Freigabe nötig)"]].forEach(([v, t]) => {
      const o = el("option", { value: v, text: t }); if ((act.kind || "notify") === v) o.selected = true; akind.append(o);
    });
    const brief = el("textarea", { class: "a2a-in", placeholder: "Auftrag für die Agent-Session, z. B. 'Melde mich mit meinen Daten für den Kurs an — fülle das Formular aus, aber SENDE NICHT ohne Freigabe.'",
      style: "width:100%;min-height:64px;padding:6px 10px;border-radius:8px;border:1px solid rgba(140,150,170,.3);background:transparent;color:inherit;font:inherit;box-sizing:border-box" });
    brief.value = act.brief || "";
    const patRow = el("div", {}, [lbl("Suchtext"), pattern]);
    const numWrap = el("div", {}, [lbl("Vergleich"), numRow]);
    const llmWrap = el("div", {}, [lbl("KI-Bedingung"), llmCond]);
    const briefWrap = el("div", {}, [lbl("Auftrag der Agent-Session"), brief,
      el("div", { text: "Sicherheit: Beim Auslösen wird nur eine Freigabe angefragt und du wirst benachrichtigt — es wird NICHTS Kontobezogenes automatisch ausgeführt.", style: "font-size:11px;opacity:.7;margin-top:4px" })]);
    const syncSig = () => { const k = skind.value; patRow.style.display = (k === "text_contains" || k === "text_absent") ? "" : "none"; numWrap.style.display = k === "number_cmp" ? "" : "none"; llmWrap.style.display = k === "llm" ? "" : "none"; };
    const syncAct = () => { briefWrap.style.display = akind.value === "agent_session" ? "" : "none"; };
    skind.addEventListener("change", syncSig); akind.addEventListener("change", syncAct);
    const err = el("div", { style: "color:#ef4444;font-size:12px;min-height:16px;margin-top:6px" });
    const save = el("button", { class: "btn sm primary", text: existing ? "Speichern" : "Anlegen" });
    const wrap = el("div", { class: "stack", style: "display:block" }, [
      lbl("Name"), name, lbl("URL (öffentlich, http/https)"), url, lbl("Intervall"), interval,
      lbl("Signal"), skind, patRow, numWrap, llmWrap, lbl("Aktion"), akind, briefWrap, err,
      el("div", { style: "margin-top:10px;display:flex;gap:8px" }, [save])
    ]);
    syncSig(); syncAct();
    save.onclick = async () => {
      const signal = { kind: skind.value };
      if (skind.value === "text_contains" || skind.value === "text_absent") signal.pattern = pattern.value;
      if (skind.value === "number_cmp") signal.number = { op: numOp.value, value: parseFloat(numVal.value || "0") };
      if (skind.value === "llm") signal.llm = { condition: llmCond.value, diff_gated: true, require_evidence: true };
      const action = { kind: akind.value, brief: brief.value };
      const payload = { name: name.value, url: url.value, interval_s: parseInt(interval.value, 10), signal, action };
      save.disabled = true; err.textContent = "";
      let r;
      try { r = existing ? await jpost("/api/alert2action/update", Object.assign({ id: w.id }, payload)) : await jpost("/api/alert2action", payload); }
      catch (e) { r = null; }
      save.disabled = false;
      if (!(r && r.ok)) { err.textContent = (r && r.error) || "Konnte nicht speichern"; return; }
      toast(existing ? "Wächter gespeichert" : "🔔 Wächter angelegt");
      Overlay.close(); this.loadA2A();
    };
    Overlay.open(existing ? "Web-Wächter bearbeiten" : "Neuer Web-Wächter", wrap);
  },
  async testA2A(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "prüft…"; }
    let r; try { r = await jpost("/api/alert2action/test", { id }); } catch (e) { r = null; }
    if (btn) { btn.disabled = false; btn.textContent = "Jetzt prüfen"; }
    if (!(r && r.ok)) { toast("Test: " + ((r && r.error) || "fehlgeschlagen")); return; }
    const health = r.health === "ok" ? "ok" : ("Health=" + r.health + (r.health_reason ? " (" + r.health_reason + ")" : ""));
    const sig = r.met ? ("Signal ERFÜLLT" + (r.evidence ? ": '" + String(r.evidence).slice(0, 80) + "'" : "")) : "Signal nicht erfüllt";
    toast("🔎 " + sig + " · " + health);
  },
  async pauseA2A(id, enabled) {
    let r; try { r = await jpost("/api/alert2action/pause", { id, enabled }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Fehler"); return; }
    this.loadA2A();
  },
  async rearmA2A(id) {
    let r; try { r = await jpost("/api/alert2action/rearm", { id }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Fehler"); return; }
    toast("Neu geschärft"); this.loadA2A();
  },
  async delA2A(id) {
    if (!confirm("Diesen Web-Wächter wirklich löschen?")) return;
    let r; try { r = await jpost("/api/alert2action/delete", { id }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Konnte nicht löschen"); return; }
    toast("Gelöscht"); this.loadA2A();
  },
  async approveA2A(id, pid) {
    if (!confirm("Agent-Session freigeben? Der Auftrag landet im Gedanken-Eingang und wird über den Ausstatten-Wizard gestartet.")) return;
    let r; try { r = await jpost("/api/alert2action/approve", { id, pid }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Freigabe fehlgeschlagen"); return; }
    toast("✅ " + (r.note || "Freigegeben")); this.loadA2A();
  },
  async dismissA2A(id, pid) {
    let r; try { r = await jpost("/api/alert2action/dismiss", { id, pid }); } catch (e) { r = null; }
    if (!(r && r.ok)) { toast((r && r.error) || "Fehler"); return; }
    this.loadA2A();
  },
  adminBtns(kid) {
    if (typeof IS_ADMIN === "undefined" || !IS_ADMIN) return "";
    return '<div class="sw-btns"><button class="btn sm" data-act="reonboard" data-kid="' + this.esc(kid) + '">🔄 Neu erkunden</button></div>';
  }
};
const LENSES = { start: Start, screen: Screen, console: Console, work: Work, software: Software, settings: Settings };
LENS_TITLE.mail = "📨 Nachrichten";

LENS_TITLE.sessions = "🗂 Sessions";
LENS_TITLE.settings = "⚙️ Einstellungen";
LENSES.sessions = { show() { const f = document.getElementById("sessFrame"); if (f && !f.getAttribute("src")) f.setAttribute("src", "/sessions"); } };

LENS_GONE.devices = "Der Geräte-Reiter ist in „Einstellungen“ aufgegangen (Bereich „Diese Brainbox“).";
LENS_TITLE.vpn = "🔒 VPN";
LENSES.vpn = { show() { const f = document.getElementById("vpnFrame"); if (f && !f.getAttribute("src")) f.setAttribute("src", "/vpn"); } };

LENS_TITLE.msgr = "💬 Messenger";
LENSES.msgr = {
  show() { Messenger.mount($("#msgrTabHost")); },
  hide() { Messenger.unmount(); },
};

MOBILE_MQ.addEventListener("change", () => {
  if (!isMobile() && Router.cur === "msgr") { Router.go("start"); return; }
  if (Router.cur === "start") { try { Start.show(); } catch (e) {} }
});

const Secrets = {
  init() {
    { const b = $("#secAdd"); if (b) b.addEventListener("click", () => this.add()); }
    { const b = $("#secFileBtn"); if (b) b.addEventListener("click", () => { const f = $("#secFile"); if (f) f.click(); }); }
    { const f = $("#secFile"); if (f) f.addEventListener("change", (e) => this.loadFile(e)); }
    { const k = $("#secKind"); if (k) k.addEventListener("change", () => this._syncKind()); }
    this._syncKind();
  },
  _syncKind() { const k = $("#secKind"); const pw = $("#secPubWrap"); if (pw) pw.hidden = !(k && k.value === "ssh_key"); },
  async load() {
    const box = $("#secretsList"); if (!box) return;
    let d; try { d = await jget("/api/secrets/list"); } catch (e) { d = null; }
    box.textContent = "";
    if (!d || d.available === false) { box.appendChild(el("div", { class: "empty", text: "Tresor nicht verfügbar." })); return; }
    const items = d.secrets || [];
    if (!items.length) { box.appendChild(el("div", { class: "empty", text: "noch keine Einträge" })); return; }
    items.forEach(s => box.appendChild(el("div", { class: "secrets-row" }, [
      el("span", { class: "sec-name", text: s.name }),
      el("span", { class: "sec-kind", text: s.kind || "text" }),
      el("button", { class: "btn xs ghost", text: "löschen", onclick: () => this.del(s.name) })])));
  },
  loadFile(e) {
    const f = e && e.target && e.target.files && e.target.files[0]; if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      const txt = String(rd.result || "");
      const v = $("#secValue"); if (v) v.value = txt;
      const n = $("#secName"); if (n && !n.value) n.value = f.name;
      const k = $("#secKind");
      if (k) {
        if (/BEGIN [A-Z ]*PRIVATE KEY/.test(txt) || /BEGIN OPENSSH PRIVATE KEY/.test(txt)) k.value = "ssh_key";
        else if (/^Host\s+/m.test(txt) && /IdentityFile|ProxyJump|HostName/.test(txt)) k.value = "ssh_config";
        this._syncKind();
      }
      toast("Datei geladen: " + f.name);
    };
    rd.readAsText(f);
  },
  async add() {
    const name = ($("#secName") && $("#secName").value || "").trim();
    const kind = ($("#secKind") && $("#secKind").value) || "env";
    const value = ($("#secValue") && $("#secValue").value) || "";
    const msg = $("#secMsg");
    if (!name || !value) { if (msg) msg.textContent = "Name und Wert sind nötig."; return; }
    if (msg) msg.textContent = "Speichern …";
    let r; try { r = await jpost("/api/secrets/set", { name, value, kind }); } catch (e) { r = null; }
    if (!r || !r.ok) { if (msg) msg.textContent = "✗ " + ((r && r.error) || "Konnte nicht speichern."); return; }
    if (kind === "ssh_key") {
      const pub = ($("#secPub") && $("#secPub").value || "").trim();
      if (pub) { try { await jpost("/api/secrets/set", { name: name + ".pub", value: pub, kind: "ssh_pub" }); } catch (e) {} }
    }
    if (msg) msg.textContent = "✅ gespeichert.";
    ["#secName", "#secValue", "#secPub"].forEach(id => { const n = $(id); if (n) n.value = ""; });
    this.load(); toast("🔑 " + name + " gespeichert");
  },
  async del(name) {
    if (!confirm(name + " löschen?")) return;
    try { await jpost("/api/secrets/delete", { name }); } catch (e) {}
    this.load(); toast("gelöscht: " + name);
  },
};

const Msgs = {

  unread: 0, _data: null, _timer: null,
  init() {
    this.poll();
    this._timer = setInterval(() => { if (!document.hidden) this.poll(); }, 90000);
  },
  async poll() {
    let d; try { d = await jget("/api/messages"); } catch (e) { return; }
    if (!(d && d.ok)) return;
    this._data = d; this.unread = d.unread || 0;
    Identity.msgBadge(this.unread);
    { const b = $("#mailBadge"); if (b) { b.textContent = this.unread; b.classList.toggle("on", !!this.unread); } }
    if (Router.cur === "mail") Mail.refresh(d);
  },
  open() { Router.go("mail"); },
  presetTo(uid) { Router.go("mail"); Mail.compose(uid); },
};

function mdRender(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = String(text || "").split("\n");
  const out = []; let inUl = false;
  lines.forEach(raw => {
    let line = esc(raw);
    line = line.replace(/`([^`]+)`/g, "<code>$1</code>");
    line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    line = line.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    line = line.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push("<li>" + line.replace(/^\s*[-*]\s+/, "") + "</li>"); return;
    }
    if (inUl) { out.push("</ul>"); inUl = false; }
    out.push(line.trim() ? line + "<br>" : "");
  });
  if (inUl) out.push("</ul>");
  return out.join("\n");
}

const Mail = {
  folder: "inbox", sel: null, _threads: null, _composing: false,
  show() { $$(".mf-item").forEach(b => b.classList.toggle("on", b.getAttribute("data-mf") === this.folder)); Msgs.poll(); },
  hide() {},
  init() {
    $$(".mf-item").forEach(b => b.addEventListener("click", () => {
      this.folder = b.getAttribute("data-mf"); this.sel = null; this._composing = false;
      $$(".mf-item").forEach(x => x.classList.toggle("on", x === b));
      this.render();
    }));
    { const b = $("#mailComposeBtn"); if (b) b.addEventListener("click", () => this.compose()); }
  },
  refresh(d) { this._data = d || Msgs._data; this.render(); },
  _mkThreads() {
    const d = this._data || Msgs._data || { received: [], sent: [] };
    const th = {};
    const add = (m, dir) => {
      const k = m.thread || m.id;
      if (!th[k]) th[k] = { id: k, items: [], last: 0, subject: "", unread: 0, who: new Set(), hasIn: false, hasOut: false };
      const t = th[k];
      t.items.push(Object.assign({ _dir: dir }, m));
      if ((m.ts || 0) > t.last) t.last = m.ts || 0;
      if (!t.subject && m.subject) t.subject = m.subject.replace(/^re:\s*/i, "");
      if (dir === "in") { t.hasIn = true; t.who.add(m.from); if (!m.read) t.unread++; }
      else { t.hasOut = true; t.who.add(m.to === "__admins__" ? "Admins" : m.to); }
    };
    (d.received || []).forEach(m => add(m, "in"));
    (d.sent || []).forEach(m => add(m, "out"));
    Object.values(th).forEach(t => t.items.sort((a, b) => (a.ts || 0) - (b.ts || 0)));
    return Object.values(th).sort((a, b) => b.last - a.last);
  },
  render() {
    const list = $("#mailList"); if (!list) return;
    this._threads = this._mkThreads();
    const rows = this._threads.filter(t => this.folder === "inbox" ? t.hasIn : t.hasOut);
    { const n = $("#mfInboxN"); const u = this._threads.reduce((s, t) => s + t.unread, 0);
      if (n) { n.hidden = !u; n.textContent = u; } }
    list.textContent = "";
    if (!rows.length) { list.appendChild(el("div", { class: "empty", text: this.folder === "inbox" ? "Posteingang ist leer." : "Nichts gesendet." })); }
    rows.forEach(t => {
      const lastMsg = t.items[t.items.length - 1] || {};
      const row = el("div", { class: "mrow" + (t.unread ? " unread" : "") + (this.sel === t.id ? " sel" : ""), onclick: () => this.openThread(t.id) }, [
        el("div", { class: "mrow-top" }, [
          el("span", { class: "mrow-who ellipsis", text: Array.from(t.who).join(", ") || "—" }),
          el("span", { class: "mrow-date tnum", text: this._fmtDate(t.last) }),
        ]),
        el("div", { class: "mrow-subj ellipsis", text: (t.subject || "(kein Betreff)") + (t.items.length > 1 ? " (" + t.items.length + ")" : "") }),
        el("div", { class: "mrow-prev ellipsis muted", text: (lastMsg.body || "").slice(0, 90) }),
      ]);
      list.appendChild(row);
    });
    this.renderRead();
  },
  _fmtDate(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), now = new Date(), p = (n) => String(n).padStart(2, "0");
    return d.toDateString() === now.toDateString()
      ? p(d.getHours()) + ":" + p(d.getMinutes())
      : p(d.getDate()) + "." + p(d.getMonth() + 1) + ".";
  },
  openThread(id) {
    this.sel = id; this._composing = false;
    const t = (this._threads || []).find(x => x.id === id);

    const ids = t ? t.items.filter(m => m._dir === "in" && !m.read).map(m => m.id) : [];
    if (ids.length) jpost("/api/messages/read", { ids }).then(() => Msgs.poll());
    $("#mail3").classList.add("show-read");
    this.render();
  },
  renderRead() {
    const host = $("#mailRead"); if (!host) return; host.textContent = "";
    if (this._composing) { this.renderCompose(host); return; }
    const t = (this._threads || []).find(x => x.id === this.sel);
    if (!t) { host.appendChild(el("div", { class: "empty", text: "Nachricht auswählen" })); return; }
    host.appendChild(el("div", { class: "mr-head" }, [
      el("button", { class: "btn sm ghost mr-back", text: "← Liste", onclick: () => { $("#mail3").classList.remove("show-read"); this.sel = null; this.render(); } }),
      el("h3", { text: t.subject || "(kein Betreff)" }),
    ]));
    const box = el("div", { class: "mr-thread" });
    t.items.forEach(m => {
      const mine = m._dir === "out";
      const item = el("div", { class: "mr-msg" + (mine ? " mine" : "") }, [
        el("div", { class: "mr-meta" }, [
          el("b", { text: mine ? "Ich → " + (m.to === "__admins__" ? "Admins" : m.to) : m.from }),
          el("span", { class: "muted tnum", text: fmtWhen(m.ts) + (m.mailed && m.mailed.indexOf("sent") === 0 ? " · ✉️ per E-Mail" : "") }),
        ]),
      ]);
      const bodyEl = el("div", { class: "mr-body" }); bodyEl.innerHTML = mdRender(m.body);
      item.appendChild(bodyEl);
      box.appendChild(item);
    });
    host.appendChild(box);

    const lastIn = [...t.items].reverse().find(m => m._dir === "in");
    const replyTo = lastIn ? lastIn.id : t.items[t.items.length - 1].id;
    const ta = el("textarea", { class: "mr-reply-ta", rows: "3", placeholder: "Antworten … (Markdown: **fett** *kursiv* `code` - Liste)" });
    host.appendChild(el("div", { class: "mr-reply" }, [
      ta,
      el("button", { class: "btn sm", text: "Antworten", onclick: async () => {
        const body = (ta.value || "").trim(); if (!body) { toast("Antwort ist leer"); return; }
        const r = await jpost("/api/messages/send", { body, reply_to: replyTo });
        if (r && r.ok) { ta.value = ""; toast("Antwort gesendet"); Msgs.poll(); }
        else toast("Senden fehlgeschlagen: " + ((r && r.error) || "?"));
      } }),
    ]));
  },
  compose(presetUid) {
    this._composing = true; this.sel = null;
    $("#mail3").classList.add("show-read");
    this.render();
    if (presetUid && this._toSel) this._toSel.value = presetUid;
    this._presetUid = presetUid || null;
  },
  async renderCompose(host) {
    host.textContent = "";
    host.appendChild(el("div", { class: "mr-head" }, [
      el("button", { class: "btn sm ghost mr-back", text: "← Liste", onclick: () => { this._composing = false; $("#mail3").classList.remove("show-read"); this.render(); } }),
      el("h3", { text: "✏️ Neue Nachricht" }),
    ]));
    const form = el("div", { class: "mr-compose" });
    if (IS_ADMIN) {
      this._toSel = el("select", {}, [el("option", { value: "all", text: "📢 an alle Nutzer" })]);
      form.appendChild(el("label", {}, ["Empfänger", this._toSel]));
      let d; try { d = await aget("/api/admin/users"); } catch (e) { d = null; }
      const users = (d && (d.users || d)) || [];
      if (Array.isArray(users)) users.forEach(u => { if (u.uid && u.uid !== USER)
        this._toSel.appendChild(el("option", { value: u.uid, text: (u.name && u.name !== u.uid ? u.name + " (" + u.uid + ")" : u.uid) + (u.email ? " · ✉️" : " · keine E-Mail") })); });
      if (this._presetUid) this._toSel.value = this._presetUid;
    } else {
      this._toSel = null;
      form.appendChild(el("div", { class: "muted", text: "Empfänger: die Box-Admins" }));
    }
    const subj = el("input", { placeholder: "Betreff", autocomplete: "off" });
    const ta = el("textarea", { rows: "8", placeholder: "Nachricht … (Markdown: **fett** *kursiv* `code` - Liste, Links)" });
    form.appendChild(el("label", {}, ["Betreff", subj]));
    form.appendChild(ta);
    form.appendChild(el("div", { class: "mr-compose-foot" }, [
      el("span", { class: "muted", text: "Geht zusätzlich per E-Mail an Empfänger mit Adresse (Opt-out im Konto)." }),
      el("button", { class: "btn", text: "Senden", onclick: async () => {
        const body = (ta.value || "").trim(); if (!body) { toast("Nachricht ist leer"); return; }
        const payload = { subject: (subj.value || "").trim(), body };
        if (IS_ADMIN && this._toSel) payload.to = this._toSel.value;
        const r = await jpost("/api/messages/send", payload);
        if (r && r.ok) {
          const dl = r.delivery || {}; const mailed = Object.values(dl).filter(v => String(v).indexOf("sent") === 0).length;
          toast("Gesendet an " + (r.recipients || 1) + " Empfänger" + (mailed ? " (" + mailed + " ✉️)" : ""));
          this._composing = false; this.folder = "sent";
          $$(".mf-item").forEach(x => x.classList.toggle("on", x.getAttribute("data-mf") === "sent"));
          Msgs.poll();
        } else toast("Senden fehlgeschlagen: " + ((r && r.error) || "?"));
      } }),
    ]));
    host.appendChild(form);
  },
};

const Identity = {
  user: USER, role: ROLE,
  init() {
    const chip = $("#userChip"); if (!chip) return;
    this._apply();
    chip.addEventListener("click", (e) => { e.stopPropagation(); this.toggle(); });
    chip.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.toggle(); } });
    const menu = $("#userMenu"); if (menu) menu.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => this.close());
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.close(); });

    this.fetch();
  },
  async fetch() {
    const d = await jget("/api/whoami");
    if (!(d && d.ok && d.user)) return;
    this.user = d.user; this.role = d.role || "";
    reconcileRole(this.role);
    this._apply();
    { const w = $("#stWhoami"); if (w) w.textContent = "angemeldet als: " + this.user + (this.role && this.role !== "user" ? " · " + this.role : ""); }
  },
  _apply() {
    const chip = $("#userChip"); if (!chip) return;
    chip.textContent = this.user ? ("👤 " + this.user) : "👤 nicht angemeldet";
    chip.title = this.user
      ? ("Angemeldet als " + this.user + (this.role ? " · " + this.role : "") + " — klicken für Konto & Abmelden")
      : "Nicht angemeldet — zum Login";
    this._render();
  },
  _render() {
    const menu = $("#userMenu"); if (!menu) return;
    menu.textContent = "";
    menu.appendChild(el("div", { class: "um-id" }, [
      el("div", { class: "um-name", text: this.user || "—" }),
      el("div", { class: "um-role", text: this.role ? ("Rolle: " + this.role) : "angemeldet" })]));

    const mb = el("button", { class: "um-item", role: "menuitem",
      onclick: () => { this.close(); Msgs.open(); } }, ["📨 Nachrichten"]);
    this._msgBadgeEl = el("span", { class: "um-badge", hidden: true });
    mb.appendChild(this._msgBadgeEl);
    menu.appendChild(mb);
    menu.appendChild(el("button", { class: "um-item", role: "menuitem",
      text: "⚙️ Einstellungen", onclick: () => { this.close(); try { Router.go("settings"); } catch (e) {} } }));

    menu.appendChild(el("button", { class: "um-item", role: "menuitem",
      text: "🔑 Mein LLM-Konto", onclick: () => { this.close(); try { Router.go("settings:ki"); } catch (e) {} } }));
    menu.appendChild(el("button", { class: "um-item um-logout", role: "menuitem",
      text: "🚪 Abmelden", onclick: () => { this.close(); logout(); } }));

    this._verEl = el("div", { class: "um-version muted", text: "Stand: …" });
    menu.appendChild(this._verEl);
    this._version();
    this.msgBadge(Msgs.unread);
  },
  async _version() {
    if (this._ver) { this._verApply(); return; }
    let d; try { d = await jget("/api/version"); } catch (e) { d = null; }
    if (d && d.ok) { this._ver = d; this._verApply(); }
    else if (this._verEl) this._verEl.textContent = "Stand: unbekannt";
  },
  _verApply() {
    const d = this._ver; if (!(d && this._verEl)) return;
    const t = d.ts ? new Date(d.ts * 1000) : null;
    const p = (n) => String(n).padStart(2, "0");
    this._verEl.textContent = "Stand: " + (d.commit || "?") +
      (t ? " · " + p(t.getDate()) + "." + p(t.getMonth() + 1) + " " + p(t.getHours()) + ":" + p(t.getMinutes()) : "");
    this._verEl.title = d.subject || "";
  },
  msgBadge(n) {
    if (this._msgBadgeEl) { this._msgBadgeEl.hidden = !n; this._msgBadgeEl.textContent = n || ""; }

    const chip = $("#userChip"); if (!chip) return;
    let b = chip.querySelector(".chip-badge");
    if (n) {
      if (!b) { b = el("span", { class: "chip-badge" }); chip.appendChild(b); }
      b.textContent = "(" + n + ")";
    } else if (b) b.remove();
  },
  toggle() { const m = $("#userMenu"); if (m) m.hidden = !m.hidden; },
  close() { const m = $("#userMenu"); if (m && !m.hidden) m.hidden = true; },
};

const Messenger = {
  host: null, listEl: null, threadEl: null, bubbles: null, composer: null,
  open: null, cur: 0, turns: [], sending: false, timer: null, sid2title: {}, _vis: false,
  rail: false,
  _active() { return this.rail || Router.cur === "start" || Router.cur === "msgr"; },
  mount(host) {
    this.host = host || null; if (!this.host) return;
    if (!this._vis) { this._vis = true; document.addEventListener("visibilitychange", () => {
      if (document.hidden || !this.host || !this._active()) return;
      this.open ? this.loadThread(this.open) : this.loadList();
    }); }
    this.renderShell();
    if (this.open) { this.openThread(this.open); } else { this.loadList(); }
  },
  unmount() { if (this.timer) { clearTimeout(this.timer); this.timer = null; } },
  _schedule(fn) { if (this.timer) clearTimeout(this.timer); if (document.hidden || !this._active()) return; this.timer = setTimeout(fn, 12000); },
  renderShell() {
    this.host.textContent = "";
    if (!this.rail) {
      this.host.appendChild(el("div", { class: "msgr-head" }, [
        el("span", { class: "msgr-title", text: "💬 Nachrichten" }),
        el("button", { class: "btn xs ghost msgr-refresh", title: "Aktualisieren", text: "⟳", onclick: () => { this.open ? this.loadThread(this.open) : this.loadList(); } }),
      ]));
    }
    this.listEl = el("div", { class: "msgr-list" });
    this.threadEl = el("div", { class: "msgr-thread", hidden: true });
    this.host.appendChild(this.listEl); this.host.appendChild(this.threadEl);
  },
  async loadList() {
    if (!this.host || this.open) return;
    let unread = {}, recent = [];
    try { const d = await jget("/api/session/unread"); unread = (d && d.sessions) || {}; } catch (e) {}
    try { const o = await jget("/api/overview"); recent = (o && Array.isArray(o.recent)) ? o.recent : []; } catch (e) {}
    if (!this.host || this.open) return;
    const rows = {};
    recent.forEach(r => { if (r.sid) rows[r.sid] = { sid: r.sid, title: r.title || r.sid, warm: !!r.warm, unread: 0, preview: "", ts: 0 }; });
    for (const sid in unread) { const s = unread[sid]; rows[sid] = Object.assign(rows[sid] || { sid }, { title: s.title || (rows[sid] && rows[sid].title) || sid, unread: s.unread || 0, preview: s.preview || "", ts: s.ts || 0, last_seq: s.last_seq }); }
    const list = Object.values(rows).sort((a, b) => (b.unread || 0) - (a.unread || 0) || (b.ts || 0) - (a.ts || 0));
    list.forEach(r => this.sid2title[r.sid] = r.title);
    this.listEl.textContent = "";
    {
      const q = el("input", { class: "msgr-search", type: "search", placeholder: "🔎 Unterhaltung suchen…", value: this._q || "" });
      q.addEventListener("input", () => { this._q = q.value; this.loadListRender(list); });
      this.listEl.appendChild(q);
      this._listCache = list;
    }
    if (!list.length) { this.listEl.appendChild(el("div", { class: "msgr-empty", text: "Noch keine Unterhaltungen — im Reiter Sessions beginnt eine." })); }
    else this.loadListRender(list);
    this._schedule(() => this.loadList());
  },
  loadListRender(list) {

    Array.from(this.listEl.querySelectorAll(".msgr-row")).forEach(n => n.remove());
    const q = (this._q || "").toLowerCase();
    const rows = q ? list.filter(r => ((r.title || "") + " " + (r.preview || "")).toLowerCase().includes(q)) : list;
    if (!rows.length && q) { const e = el("div", { class: "msgr-empty msgr-row", text: "keine Treffer" }); this.listEl.appendChild(e); }
    rows.forEach(r => {
      const row = el("div", { class: "msgr-row" + (r.unread ? " unread" : "") }, [
        el("span", { class: "msgr-dot" + (r.warm ? " warm" : "") }),
        el("div", { class: "msgr-rmid" }, [
          el("div", { class: "msgr-rtitle ellipsis", text: r.title }),
          el("div", { class: "msgr-rprev ellipsis", text: r.preview || (r.warm ? "läuft" : "") }),
        ]),
        el("div", { class: "msgr-rmeta" }, [
          r.ts ? el("span", { class: "msgr-rts", text: fmtWhen(r.ts) }) : null,
          r.unread ? el("span", { class: "msgr-badge", text: (r.unread > 99 ? "99+" : r.unread) }) : null,
        ].filter(Boolean)),
      ]);
      row.onclick = () => this.openThread(r.sid);
      this.listEl.appendChild(row);
    });
  },

  openInStart(sid) {
    this.open = sid;
    if (isMobile()) { try { Router.go("msgr"); return; } catch (e) {} return void this.mount($("#msgrTabHost")); }
    try { Router.go("start"); } catch (e) {}
    this.mount($("#msgrHost"));
  },

  focusSession(sid) {
    if (!sid || sid === "__voice__") return;
    if (!this.host || !this.threadEl) return;
    try {
      if (this.open === sid) { if (this.composer) this.composer.focus(); return; }
      this.openThread(sid);
    } catch (e) {}
  },
  async openThread(sid) {
    if (!this.host) return;
    this.open = sid; this.cur = 0; this.turns = [];
    try { Notify.post("/api/session/seen", { sid }); Notify.last[sid] = (Notify.lastSessions && Notify.lastSessions[sid] && Notify.lastSessions[sid].last_seq) || Notify.last[sid]; setTimeout(() => Notify.poll(), 300); } catch (e) {}
    this.listEl.hidden = true; this.threadEl.hidden = false;
    this.renderThreadShell(sid);
    await this.loadThread(sid);
  },
  backToList() { this.open = null; if (this.timer) clearTimeout(this.timer); if (this.threadEl) this.threadEl.hidden = true; if (this.listEl) this.listEl.hidden = false; this.loadList(); },

  async explain(sid, btn) {
    if (this._explSid) return;
    this._explSid = sid;
    const reset = () => { this._explSid = null; if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = "🔍 Erklären"; } };
    if (btn) { btn.disabled = true; btn.textContent = "🔍 liest mit …"; }
    let r; try { r = await jpost("/api/session/erklaer", { sid }); } catch (e) { r = null; }
    if (!r || (r.ok === false && !r.running)) { reset(); toast((r && r.error) || "Beobachter nicht erreichbar"); return; }
    for (let i = 0; i < 90; i++) {
      await new Promise(res => setTimeout(res, 5000));
      let p; try { p = await jpost("/api/session/erklaer", { sid, poll: 1 }); } catch (e) { continue; }
      if (p && p.running) continue;
      reset();
      if (p && p.ok === false && p.error) toast(p.error);
      else if (this.open === sid) this.loadThread(sid);
      return;
    }
    reset(); toast("Der Beobachter braucht ungewöhnlich lange — die Antwort erscheint im Verlauf.");
  },
  renderThreadShell(sid) {
    this.threadEl.textContent = "";

    const nurLesen = (sid === "meldungen");
    const kopf = [
      el("button", { class: "btn xs ghost", title: "Zurück", text: "‹", onclick: () => this.backToList() }),
      el("span", { class: "msgr-ttitle ellipsis", text: nurLesen ? "📢 Meldungen" : (this.sid2title[sid] || sid) }),
    ];
    if (!nurLesen) {
      kopf.push(el("button", { class: "btn xs", title: "Erklär mir, was hier passiert: ein Beobachter liest die jüngsten Züge dieser Session und erklärt sie in 2–4 Sätzen ohne Jargon — die Antwort erscheint hier als 🔍-Blase. Läuft nur auf Knopfdruck.", text: "🔍 Erklären", onclick: (e) => this.explain(sid, e.currentTarget) }));
      kopf.push(el("button", { class: "btn xs", title: "Im Terminal öffnen", text: "▟", onclick: () => openSessionTerminal(sid) }));
    }
    this.threadEl.appendChild(el("div", { class: "msgr-thead" }, kopf));
    this.bubbles = el("div", { class: "msgr-bubbles", role: "log", "aria-live": "polite" });
    this.threadEl.appendChild(this.bubbles);
    if (nurLesen) { this.composer = null; return; }
    const inp = el("textarea", { class: "msgr-input", rows: "1", placeholder: "Antwort schreiben — Enter senden, Shift+Enter Zeile" });
    inp.addEventListener("keydown", (e) => {

      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(sid, inp); }
      else if (e.key === "Escape") this.backToList();
    });

    inp.addEventListener("input", () => { inp.style.height = "auto"; inp.style.height = inp.scrollHeight + "px"; });
    this.threadEl.appendChild(el("div", { class: "msgr-compose" }, [inp, el("button", { class: "btn sm primary", text: "Senden", onclick: () => this.send(sid, inp) })]));
    this.composer = inp; setTimeout(() => inp.focus(), 40);
  },
  async loadThread(sid) {
    if (this.open !== sid) return;
    let d; try { d = await jget("/api/transcript?sid=" + encodeURIComponent(sid) + "&since=" + (this.cur | 0)); } catch (e) { this._schedule(() => this.loadThread(sid)); return; }
    if (this.open !== sid) return;
    const t = ((d && d.turns) || []).filter(x => (x.text || "").trim());
    if (t.length) {

      const fresh = [];
      let folded = false;
      for (const x of t) {
        if (x.edit_of != null) {
          const o = this.turns.find(y => y.seq === x.edit_of);
          if (o && o.text !== x.text) { o.text = x.text; o.edited = true; folded = true; }
          continue;
        }
        fresh.push(x);
      }
      if (fresh.length) this.turns = this.turns.concat(fresh).slice(-200);
      if (fresh.length || folded) this.renderBubbles();
      this.cur = (d && d.next != null) ? d.next : this.cur;
      this._schedule(() => this.loadThread(sid));
      return;
    }
    this.renderBubbles();
    this._schedule(() => this.loadThread(sid));
  },
  _dayLabel(ts) {

    const d = new Date(ts * 1000), now = new Date(), y = new Date(now); y.setDate(y.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return "Heute";
    if (d.toDateString() === y.toDateString()) return "Gestern";
    return d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
  },
  renderBubbles() {
    if (!this.bubbles) return;

    const atBottom = Math.abs(this.bubbles.scrollHeight - this.bubbles.clientHeight - this.bubbles.scrollTop) < 80;
    const grew = this.turns.length > (this._lastLen || 0);
    this._lastLen = this.turns.length;
    const pend = Array.from(this.bubbles.querySelectorAll(".msgr-msg.pending, .msgr-msg.failed"));
    this.bubbles.textContent = "";
    if (!this.turns.length && !pend.length) { this.bubbles.appendChild(el("div", { class: "msgr-empty", text: "Noch kein Verlauf." })); }
    else {

      const win = this._win || 50;
      const shown = this.turns.slice(-win);
      if (this.turns.length > shown.length) {
        const more = el("button", { class: "btn xs ghost msgr-more", text: "↑ ältere anzeigen (" + (this.turns.length - shown.length) + ")" });
        more.onclick = () => {

          const fromBottom = this.bubbles.scrollHeight - this.bubbles.scrollTop;
          this._win = win + 75; this.renderBubbles();
          this.bubbles.scrollTop = this.bubbles.scrollHeight - fromBottom;
        };
        this.bubbles.appendChild(more);
      }
      let lastDay = "";
      shown.forEach(x => {
        if (x.ts) {
          const day = this._dayLabel(x.ts);
          if (day !== lastDay) { lastDay = day; this.bubbles.appendChild(el("div", { class: "msgr-daysep", text: day })); }
        }
        const who = x.role === "user" ? "Du" : (x.role === "observer" ? "🔍" : "🤖");

        const txt = el("span", { class: "msgr-txt" }); txt.innerHTML = mdRender(x.text);
        const b = el("div", { class: "msgr-msg " + (x.role === "user" ? "u" : (x.role === "observer" ? "o" : "a")) + (x.sticky ? " sticky" : "") }, [
          el("span", { class: "msgr-who", text: who }), txt,
        ]);

        const meta = [];
        if (x.ts) meta.push(el("span", { class: "msgr-time tnum", text: fmtWhen(x.ts) }));
        if (x.role !== "user" && x.model) meta.push(el("span", { class: "msgr-model", title: x.model, text: fmtModel(x.model) }));
        if (x.edited) meta.push(el("span", { class: "msgr-edited", text: "aktualisiert" }));
        if (meta.length) b.appendChild(el("div", { class: "msgr-meta" }, meta));
        if (x.ts) { const d = new Date(x.ts * 1000); b.title = d.toLocaleString("de-DE"); }
        this.bubbles.appendChild(b);
      });
      pend.forEach(p => this.bubbles.appendChild(p));
    }
    if (atBottom) { this.bubbles.scrollTop = this.bubbles.scrollHeight; this._hideNewPill(); }
    else if (grew) this._showNewPill();
  },
  async _trackDelivery(sid, opt, text) {

    for (let i = 0; i < 24; i++) {
      await new Promise(res => setTimeout(res, i < 6 ? 1500 : 4000));
      if (!opt.isConnected) return;
      let d; try { d = await jget("/api/session/delivery?sid=" + encodeURIComponent(sid)); } catch (e) { continue; }
      const s = d && (d.state || d.status);
      const st = opt.querySelector(".msgr-state"); if (!st) return;
      if (s === "delivered") { st.textContent = "✓"; st.title = "zugestellt"; return; }
      if (s === "no_reply") { st.textContent = "✓"; st.title = "zugestellt — noch keine Antwort"; return; }
      if (s === "failed") {
        opt.classList.add("failed");
        st.textContent = "⚠ " + ((d && (d.detail || d.reason)) || "Zustellung fehlgeschlagen") + " · erneut senden";
        st.onclick = () => { opt.remove(); if (this.composer) { this.composer.value = text; } this.send(sid, this.composer); };
        return;
      }
    }
  },
  _showNewPill() {
    if (this._pill && this._pill.isConnected) return;
    this._pill = el("button", { class: "msgr-newpill", text: "↓ neue Nachrichten", onclick: () => {
      this.bubbles.scrollTop = this.bubbles.scrollHeight; this._hideNewPill();
    } });
    if (this.threadEl) this.threadEl.appendChild(this._pill);

    if (!this._pillScroll && this.bubbles) {
      this._pillScroll = true;
      this.bubbles.addEventListener("scroll", () => {
        if (Math.abs(this.bubbles.scrollHeight - this.bubbles.clientHeight - this.bubbles.scrollTop) < 80) this._hideNewPill();
      }, { passive: true });
    }
  },
  _hideNewPill() { if (this._pill) { this._pill.remove(); this._pill = null; } },
  async send(sid, inp) {
    const text = (inp.value || "").trim(); if (!text || this.sending) return;
    this.sending = true; inp.value = "";
    const opt = el("div", { class: "msgr-msg u pending" }, [
      el("span", { class: "msgr-who", text: "Du" }), el("span", { class: "msgr-txt", text: text }),
      el("span", { class: "msgr-state", text: "sendet…" }),
    ]);
    this.bubbles.appendChild(opt); this.bubbles.scrollTop = this.bubbles.scrollHeight;
    let r; try { r = await jpost("/api/session/say", { sid, text }); } catch (e) { r = null; }
    this.sending = false;
    if (r && r.ok !== false) {
      opt.classList.remove("pending");
      const st = opt.querySelector(".msgr-state");
      if (st) { st.textContent = "🕐"; st.title = "angenommen — Zustellung läuft"; }
      this._trackDelivery(sid, opt, text);
      setTimeout(() => this.loadThread(sid), 700);
    } else {
      opt.classList.remove("pending"); opt.classList.add("failed");
      const st = opt.querySelector(".msgr-state"); if (st) { st.textContent = "fehlgeschlagen · erneut senden"; st.onclick = () => { opt.remove(); inp.value = text; this.send(sid, inp); }; }
    }
    if (this.composer) this.composer.focus();
  },
};

const Notify = {
  last: {}, toasts: {}, selSid: null, sndLast: 0, sndPer: {}, audio: null, total: 0,
  cfg() {
    return {
      on: localStorage.getItem("pp-nf-on") !== "0",
      toast: localStorage.getItem("pp-nf-toast") !== "0",
      sound: localStorage.getItem("pp-nf-sound") === "1",
      sys: localStorage.getItem("pp-nf-sys") === "1"
    };
  },
  post(u, b) {
    return fetch(u, { method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify(b || {}) }).catch(() => {});
  },
  init() {
    this.host = document.createElement("div"); this.host.className = "pn-notify-host";
    this.pill = document.createElement("div"); this.pill.className = "pn-npill";
    this.pill.onclick = (e) => { e.stopPropagation(); this.togglePanel(); };
    this.host.appendChild(this.pill);
    document.body.appendChild(this.host);
    window.addEventListener("message", (e) => {
      const d = e.data || {};
      if (d.type === "pp-session-selected") { this.selSid = d.sid || null; this.dismiss(d.sid); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) this.applyTitle(); });
    this.wireSettings();
    setInterval(() => { this.poll(); StandWache.tick(); }, 5000);
    this.poll();
  },
  async poll() {
    if (!this.cfg().on) {
      this.applyBadge(0); this.total = 0; this.applyTitle(); this.pill.classList.remove("on");

      const da = await jget("/api/session/unread");
      if (da && da.ok) {
        const sa = da.sessions || {};
        for (const sid in sa) {
          const s = sa[sid];
          if ((s.alert || 0) > 0 && s.last_seq > (this.last[sid] || 0)) {
            this.last[sid] = s.last_seq;
            this.notifyOne(sid, s, true);
          }
        }
      }
      return;
    }
    const d = await jget("/api/session/unread");
    if (!d || !d.ok) return;
    const ss = d.sessions || {}; let total = 0, alerts = 0;
    const onSessions = (location.hash || "").indexOf("sessions") >= 0;

    const inMsgr = !document.hidden && Router.cur === "msgr";
    for (const sid in ss) {
      const s = ss[sid];
      if (sid === this.selSid && onSessions && !document.hidden) {
        this.post("/api/session/seen", { sid });
        continue;
      }

      if (!document.hidden && Router.cur === "msgr" && Messenger.open === sid) {
        this.post("/api/session/seen", { sid });
        continue;
      }
      total += s.unread || 0; alerts += s.alert || 0;
      if (s.unread && s.last_seq > (this.last[sid] || 0)) {
        this.last[sid] = s.last_seq;

        if (!inMsgr || (s.alert || 0) > 0) this.notifyOne(sid, s, (s.alert || 0) > 0);
      }
    }
    this.total = total; this.lastSessions = ss;
    this.applyBadge(total, alerts); this.applyTitle();
    this.pill.textContent = "💬 " + (total > 50 ? "50+" : total) + " ungelesen";
    this.pill.classList.toggle("on", total > 0);
    if (this.panelOpen) this.renderPanel();
  },
  applyBadge(n, nAlert) {

    ["#sessBadge", "#msgrNavBadge"].forEach(sel => {
      const b = $(sel); if (!b) return;
      b.textContent = n > 50 ? "50+" : n;
      b.classList.toggle("on", n > 0);
      b.classList.toggle("alert", (nAlert || 0) > 0);
    });
  },
  applyTitle() {
    const base = document.title.replace(/^\(\d+\+?\)\s*/, "");
    document.title = (this.total > 0 ? "(" + (this.total > 50 ? "50+" : this.total) + ") " : "") + base;
  },
  notifyOne(sid, s, force) {
    const c = this.cfg();
    if ((c.toast || force) && !document.hidden) this.toastFor(sid, s, force);
    if ((c.sys || force) && document.hidden) this.sysFor(sid, s);
    if (c.sound || force) this.ping(sid);
  },
  toastFor(sid, s, alert) {
    if (!this.toasts[sid] && Object.keys(this.toasts).filter(k => k !== "__sum").length >= 3) { this.summaryToast(); return; }
    let t = this.toasts[sid];
    if (!t) t = this.toasts[sid] = this._mkToast(sid, () => { this.dismiss(sid); Messenger.openInStart(sid); });
    t.el.className = "pn-ntoast" + (alert ? " alert" : "");
    t.body.innerHTML = "<b>" + (alert ? "🚨 " : "💬 ") + esc(s.title || sid) + (s.unread > 1 ? " · " + (s.unread > 50 ? "50+" : s.unread) + " neu" : "") + "</b>" +
      esc((s.preview || "").slice(0, 140));
    clearTimeout(t.timer);
    t.timer = setTimeout(() => this.dismiss(sid), 6000);
  },

  _mkToast(sid, onOpen) {
    const d = document.createElement("div"); d.className = "pn-ntoast";
    const body = document.createElement("div"); body.className = "pn-ntoast-body"; d.appendChild(body);
    const x = document.createElement("button"); x.type = "button"; x.className = "pn-ntoast-x";
    x.setAttribute("aria-label", "schließen"); x.textContent = "×";
    x.onclick = (e) => { e.stopPropagation(); this.dismiss(sid); };
    d.appendChild(x);
    d.onclick = onOpen;
    this._swipeDismiss(d, sid);
    this.host.insertBefore(d, this.pill);
    return { el: d, body, timer: null };
  },

  _swipeDismiss(d, sid) {
    let x0 = null, y0 = null, dx = 0, moved = false;
    d.addEventListener("touchstart", (e) => { const p = e.touches[0]; x0 = p.clientX; y0 = p.clientY; dx = 0; moved = false; d.style.transition = "none"; }, { passive: true });
    d.addEventListener("touchmove", (e) => {
      if (x0 == null) return; const p = e.touches[0]; dx = p.clientX - x0; const dy = p.clientY - y0;
      if (Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) { moved = true; d.style.transform = "translateX(" + dx + "px)"; d.style.opacity = String(Math.max(0.15, 1 - Math.abs(dx) / 240)); }
    }, { passive: true });
    d.addEventListener("touchend", () => {
      d.style.transition = "transform .18s ease, opacity .18s ease";
      if (Math.abs(dx) > 60) { d.style.transform = "translateX(" + (dx > 0 ? 420 : -420) + "px)"; d.style.opacity = "0"; setTimeout(() => this.dismiss(sid), 160); }
      else { d.style.transform = ""; d.style.opacity = ""; }
      x0 = null;
    });
    d.addEventListener("click", (e) => { if (moved) { e.stopImmediatePropagation(); e.preventDefault(); moved = false; } }, true);
  },
  summaryToast() {
    let t = this.toasts.__sum;
    if (!t) t = this.toasts.__sum = this._mkToast("__sum", () => { this.dismiss("__sum"); Router.go("sessions"); });
    t.body.innerHTML = "<b>💬 Neue Nachrichten in mehreren Sessions</b>";
    clearTimeout(t.timer);
    t.timer = setTimeout(() => this.dismiss("__sum"), 6000);
  },
  dismiss(sid) {
    const t = this.toasts[sid]; if (!t) return;
    clearTimeout(t.timer); t.el.remove(); delete this.toasts[sid];
  },
  togglePanel() { if (this.panelOpen) this.closePanel(); else this.openPanel(); },
  openPanel() {

    this.panelOpen = true;
    if (!this.panel) {
      this.panel = document.createElement("div"); this.panel.className = "pn-npanel";
      this.host.insertBefore(this.panel, this.pill);
    }
    document.addEventListener("click", this._closeOnOut = (ev) => {
      if (this.panel && !this.panel.contains(ev.target) && ev.target !== this.pill) this.closePanel();
    });
    this.renderPanel();
  },
  closePanel() {
    this.panelOpen = false;
    if (this.panel) { this.panel.remove(); this.panel = null; }
    if (this._closeOnOut) { document.removeEventListener("click", this._closeOnOut); this._closeOnOut = null; }
  },
  renderPanel() {
    if (!this.panel) return;
    const ss = this.lastSessions || {};
    const ids = Object.keys(ss).filter(k => (ss[k].unread || 0) > 0)
      .sort((a, b) => (ss[b].ts || 0) - (ss[a].ts || 0));
    this.panel.textContent = "";
    const head = el("div", { class: "pn-nphead" }, [
      el("span", { text: "Ungelesene Nachrichten" }),
      el("button", { class: "pn-npall", text: "alle gelesen", onclick: () => this.markAllSeen() })
    ]);
    this.panel.appendChild(head);
    if (!ids.length) { this.panel.appendChild(el("div", { class: "pn-npempty muted", text: "nichts Ungelesenes" })); return; }
    ids.forEach(sid => {
      const s = ss[sid];
      const row = el("div", { class: "pn-nprow" }, [
        el("span", { class: "pn-npct", text: (s.unread > 50 ? "50+" : s.unread) }),
        el("span", { class: "pn-npttl ellipsis", text: s.title || sid }),
      ]);
      row.title = s.preview || "";
      row.onclick = () => { this.last[sid] = s.last_seq; Messenger.openInStart(sid); this.closePanel(); };
      this.panel.appendChild(row);
    });
  },
  markAllSeen() {
    const ss = this.lastSessions || {};
    Object.keys(ss).forEach(sid => { if ((ss[sid].unread || 0) > 0) { this.post("/api/session/seen", { sid }); this.last[sid] = ss[sid].last_seq; } });
    this.closePanel(); setTimeout(() => this.poll(), 300);
  },
  ping(sid) {
    const now = Date.now();
    if (now - this.sndLast < 1000) return;
    if (now - (this.sndPer[sid] || 0) < 45000) return;
    this.sndLast = now; this.sndPer[sid] = now;
    try {
      this.audio = this.audio || new (window.AudioContext || window.webkitAudioContext)();
      const ctx = this.audio, o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.06, ctx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.3);
    } catch (e) {}
  },
  sysFor(sid, s) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const n = new Notification(s.title || "Session", {
        body: (s.preview || "neue Nachricht").slice(0, 120), tag: "pn-" + sid });
      n.onclick = () => { window.focus(); openSessionTerminal(sid); n.close(); };
    } catch (e) {}
  },
  wireSettings() {
    const c = this.cfg();
    const map = { nfOn: ["pp-nf-on", c.on], nfToast: ["pp-nf-toast", c.toast],
                  nfSound: ["pp-nf-sound", c.sound], nfSys: ["pp-nf-sys", c.sys] };
    for (const id in map) {
      const el2 = $("#" + id); if (!el2) continue;
      el2.checked = map[id][1];
      el2.onchange = () => {
        localStorage.setItem(map[id][0], el2.checked ? "1" : "0");
        if (id === "nfSys" && el2.checked) this.askPerm();
        this.status();
      };
    }
    const pb = $("#nfSysPerm"); if (pb) pb.onclick = () => this.askPerm();

    const bimap = { biHover: "pp-bi-hover", biOverview: "pp-bi-overview", biWorkload: "pp-bi-workload" };
    for (const id in bimap) {
      const e2 = $("#" + id); if (!e2) continue;
      e2.checked = localStorage.getItem(bimap[id]) !== "0";
      e2.onchange = () => localStorage.setItem(bimap[id], e2.checked ? "1" : "0");
    }
    this.status();
  },
  askPerm() {
    if (!("Notification" in window)) { const s = $("#nfStatus"); if (s) s.textContent = "Dieser Browser unterstützt keine System-Benachrichtigungen."; return; }
    try { Notification.requestPermission().then(() => this.status()); } catch (e) {}
  },
  status() {
    const s = $("#nfStatus"); if (!s) return;
    const c = this.cfg();
    const perm = ("Notification" in window) ? Notification.permission : "n/a";
    s.textContent = (c.on ? "Aktiv" : "Aus") + " · Popups " + (c.toast ? "an" : "aus") +
      " · Ton " + (c.sound ? "an" : "aus (stumm)") +
      " · System " + (c.sys ? "an (Berechtigung: " + perm + ")" : "aus");
    const pb = $("#nfSysPerm"); if (pb) pb.hidden = !(c.sys && perm !== "granted");
  },
};

function boot() {

  window.addEventListener("message", async (e) => {
    const d = e.data || {};
    if (d.type !== "pp-show-screen" || !d.sid) return;
    const st = await jget("/api/session/desktop?sid=" + encodeURIComponent(d.sid)) || {};
    if (st.active && st.cell) window.open("/vnc3?cell=" + encodeURIComponent(st.cell), "_blank");
    else toast("Diese Session hat keinen aktiven Desktop — 🖥 Desktop in der Session-Übersicht startet einen.");
  });
  Theme.init();
  Convo.init(); NavVoice.init();
  UIP.applyGlobal();
  Start.init(); Screen.init(); Console.init(); Work.init(); Settings.init();
  Overlay.init(); Palette.init(); Admin.init(); Stats.init(); Identity.init(); Secrets.init();
  LENSES.mail = Mail;
  Msgs.init();
  Mail.init();
  Rail.init();
  WA.boot();
  Notify.init();
  Router.init();
  jget("/api/status").then(d => { if (d && d.lan) $("#lanStamp").textContent = d.lan; });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

})();
