
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
