
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
      this.brainStatus();
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

    if (IS_ADMIN) ["#stgBoxHead", "#stgBoxCard", "#stDevCard", "#stFrgCard", "#stShellCard", "#stgTabBox"].forEach(id => { const n = $(id); if (n) n.hidden = false; });
    { const b = $("#shellAdd"); if (b) b.addEventListener("click", () => this.addShellKey()); }
    if (IS_ADMIN) this.loadShellKeys();

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
