
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
