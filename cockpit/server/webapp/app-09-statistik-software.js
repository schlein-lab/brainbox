
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
