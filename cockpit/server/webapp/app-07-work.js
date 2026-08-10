
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
