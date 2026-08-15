
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
