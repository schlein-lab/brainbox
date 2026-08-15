
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
