
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
