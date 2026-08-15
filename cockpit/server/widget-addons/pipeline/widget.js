
(function () {
  "use strict";
  var B = window.BBW;
  if (!B) return;

  var SEV = { good: "🟢", calm: "⚪", watch: "🟡" };
  var STATES = {
    running: { dot: "⚪", mean: "Diese Bahn arbeitet gerade." },
    queued: { dot: "⚪", mean: "Eingereiht — läuft, sobald Hintergrund-Kapazität frei ist." },
    done: { dot: "🟢", mean: "Letzter Lauf sauber beendet." },
    idle: { dot: "⚪", mean: "Wartet auf den nächsten geplanten Lauf." },
    failed: { dot: "🟡", mean: "Ein Lauf endete mit Fehler — wird beim nächsten Takt automatisch wiederholt." },
    timeout: { dot: "🟡", mean: "Ein Lauf lief in die Zeitgrenze — wird beim nächsten Takt erneut versucht." },
    paused: { dot: "⚪", mean: "Diese Bahn ist von dir pausiert." },
    off: { dot: "⚪", mean: "Pipeline ist aus." },
  };
  var GT = { qc: "Daten-QC", extraction: "Extraktion (LLM)", inventory: "Inventar", deep: "Deep-QC", multiomics: "Multi-Omics", enum: "Enumeration", metabo: "Metabolomics", other: "Sonstige" };
  var ORDER = ["qc", "extraction", "inventory", "deep", "multiomics", "enum", "metabo", "other"];

  function cad(s) {
    s = +s || 0;
    if (s < 90) return "alle " + s + " s";
    if (s < 3600) return "alle " + Math.round(s / 60) + " min";
    return "alle " + Math.round(s / 3600) + " h";
  }
  function when(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000), n = new Date();
    return d.toDateString() === n.toDateString()
      ? d.toLocaleTimeString("de", { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString("de");
  }
  function laneState(l) {
    if (l.paused) return "paused";
    if (l.running) return l.last_state === "running" ? "running" : "queued";
    if (l.last_state === "failed" || l.last_state === "timeout") return l.last_state;
    if (l.last_state === "done") return "done";
    return "idle";
  }

  var W = {
    title: "🔧 Datenpipeline",
    _open: {},
    async fill(host) {

      if (host.contains(document.activeElement) && document.activeElement !== document.body) return;
      var keep = host.scrollTop;
      var d; try { d = await B.jget("/api/wa/pipeline/status"); } catch (e) { d = null; }
      host.textContent = "";
      if (!d || d.ok === false) { host.appendChild(B.el("div", { class: "empty", text: (d && d.error) || "nicht erreichbar" })); return; }
      if (!d.lanes || !d.lanes.length) { host.appendChild(B.el("div", { class: "empty", text: "Keine Bahnen definiert — die Pipeline dieser Box ist leer." })); return; }
      var self = this, enabled = !!d.enabled, problems = d.problems || 0, active = d.active || 0;
      host.appendChild(B.el("div", { class: "rw-row" }, [
        B.el("span", { class: "ellipsis", text: (enabled ? (problems ? "🟡 " : "🟢 ") : "⏸ ") + d.count + " Bahnen · " + active + " aktiv" + (problems ? " · " + problems + " wiederholen" : "") }),
        B.el("button", { class: "rw-btn", title: enabled ? "Pipeline global anhalten" : "Pipeline global starten", text: enabled ? "⏸ anhalten" : "▶ starten", onclick: function () { self.act(enabled ? "disable" : "enable", null, host); } }),
      ]));
      var groups = {};
      d.lanes.forEach(function (l) { (groups[l.group || "other"] = groups[l.group || "other"] || []).push(l); });
      Object.keys(groups).sort(function (a, b) { return ((ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99)); }).forEach(function (g) {
        var lanes = groups[g], open = !!self._open[g];
        var gProb = lanes.filter(function (l) { return ["failed", "timeout"].indexOf(laneState(l)) >= 0; }).length;
        var gAct = lanes.filter(function (l) { return l.running; }).length;
        host.appendChild(B.el("div", { class: "rw-row", onclick: function () { self._open[g] = !open; self.fill(host); } }, [
          B.el("span", { text: gProb ? SEV.watch : (gAct ? SEV.good : SEV.calm) }),
          B.el("span", { class: "ellipsis", text: (GT[g] || g) + " · " + lanes.length + " Bahnen" + (gAct ? " · " + gAct + " aktiv" : "") }),
          B.el("span", { class: "muted", text: open ? "▲" : "▼" }),
        ]));
        if (open) lanes.forEach(function (l) {
          var st = laneState(l), ex = STATES[st] || STATES.idle;
          var r = B.el("div", { class: "rw-row", title: ex.mean + (l.note ? " · " + l.note : "") + (l.last_job_id ? " · Job " + l.last_job_id : "") }, [
            B.el("span", { text: ex.dot }),
            B.el("span", { class: "ellipsis", text: (l.title || l.name) + " · " + cad(l.every_s) + (l.last_ts ? " · zuletzt " + when(l.last_ts) : "") }),
          ]);
          r.appendChild(B.el("button", { class: "rw-btn", text: "Jetzt", title: "Diese Bahn sofort einreihen", onclick: function (e) { e.stopPropagation(); self.act("lane", { name: l.name, action: "run" }, host); } }));
          r.appendChild(B.el("button", { class: "rw-btn", text: l.paused ? "▶" : "⏸", title: l.paused ? "Bahn fortsetzen" : "Bahn pausieren", onclick: function (e) { e.stopPropagation(); self.act("lane", { name: l.name, action: l.paused ? "resume" : "pause" }, host); } }));
          host.appendChild(r);
        });
      });
      if (keep) host.scrollTop = keep;
    },
    act(verb, body, host) {
      var self = this;
      B.jpost("/api/wa/pipeline/" + verb, body || {}).then(function (r) {
        if (r && (r.note || r.error)) B.toast(r.note || r.error);
        self.fill(host);
      }).catch(function () {});
    },
  };
  B.register("pipeline", W);
})();
