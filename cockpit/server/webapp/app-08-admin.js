
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
    const wrap = el("div", { style: "margin-top:10px;display:flex;flex-direction:column;gap:8px;max-width:440px" });
    const inp = (ph, val, type) => el("input", { placeholder: ph, value: val || "", type: type || "text", autocomplete: "off", spellcheck: "false", style: "padding:9px 11px;border:1px solid var(--line);border-radius:9px;background:var(--panel2,#0f1520);color:inherit;font-size:14px" });
    const kApi = inp("Mailjet API-Key", "");
    const kSec = inp("Mailjet API-Secret", "", "password");
    const kSnd = inp("Absender-E-Mail", d.sender || "");
    const kNam = inp("Absender-Name", d.sender_name || "");
    const save = el("button", { class: "btn sm", text: "Speichern" });
    save.addEventListener("click", async () => {
      save.disabled = true;
      const r = await apost("/api/admin/mail/config", { mailjet_apikey: kApi.value.trim(), mailjet_apisecret: kSec.value.trim(), sender: kSnd.value.trim(), sender_name: kNam.value.trim() });
      save.disabled = false;
      if (!r || r._forbidden) return;
      if (r._neterr) { toast("Netzfehler"); return; }
      toast(r.ok ? ("Gespeichert (" + (r.updated || 0) + " Felder)") : ("Fehler: " + (r.error || "?")));
      if (r.ok) this.loadMail();
    });
    [el("div", { class: "muted", style: "font-size:12px", text: "Mailjet-Zugang (System-Ebene). Key/Secret leer lassen = unveraendert:" }), kApi, kSec, kSnd, kNam, save].forEach((x) => wrap.appendChild(x));
    box.appendChild(wrap);
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
