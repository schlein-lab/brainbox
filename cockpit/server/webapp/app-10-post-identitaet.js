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
