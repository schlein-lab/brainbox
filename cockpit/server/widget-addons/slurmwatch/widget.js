
(function () {
  "use strict";
  var B = window.BBW;
  if (!B) return;

  function chip(txt, on, urgent, onclick) {
    return B.el("button", { class: "acct-chip" + (on ? " on" : "") + (urgent ? " urgent" : ""), text: txt, onclick: onclick });
  }

  function kvRows(obj, keys) {
    var wrap = B.el("div", { class: "stack" });
    keys.forEach(function (k) {
      if (obj[k] == null || obj[k] === "") return;
      wrap.appendChild(B.el("div", { class: "rw-row" }, [
        B.el("span", { class: "muted", text: k }),
        B.el("span", { class: "ellipsis tnum", text: String(obj[k]) }),
      ]));
    });
    return wrap;
  }

  function jobSheet(jid) {
    var wrap = B.el("div", { class: "stack" }, [B.el("div", { class: "muted", text: "lädt …" })]);
    B.overlay("Slurm-Job " + jid, wrap);
    B.jget("/api/wa/slurmwatch/job?id=" + encodeURIComponent(jid)).then(function (d) {
      wrap.textContent = "";
      if (!d || d.ok === false) { wrap.appendChild(B.el("div", { class: "empty", text: (d && d.error) || "keine Daten" })); return; }
      var p = d.props || {};
      var head = B.el("div", { class: "rw-row" }, [
        B.el("span", { class: "badge s-" + String(p.JobState || "?").toLowerCase(), text: p.JobState || "?" }),
        B.el("span", { class: "ellipsis", text: p.JobName || jid }),
      ]);
      wrap.appendChild(head);
      wrap.appendChild(B.el("div", { class: "subhead", text: "Eigenschaften" }));
      wrap.appendChild(kvRows(p, ["JobId", "UserId", "Account", "Partition", "Priority", "QOS", "Reason",
        "SubmitTime", "StartTime", "RunTime", "TimeLimit", "NumNodes", "NumCPUs", "NumTasks",
        "mem", "NodeList", "BatchHost", "WorkDir", "StdOut", "StdErr", "Command", "Dependency", "ExitCode"]));
      if ((d.sacct || []).length) {
        wrap.appendChild(B.el("div", { class: "subhead", text: "Abrechnung (sacct)" }));
        d.sacct.forEach(function (s) {
          wrap.appendChild(B.el("div", { class: "rw-row" }, [
            B.el("span", { class: "muted ellipsis", text: s.step }),
            B.el("span", { class: "tnum", text: s.state + " · " + s.elapsed + (s.maxrss ? " · " + s.maxrss : "") }),
          ]));
        });
      }
      wrap.appendChild(B.el("div", { class: "subhead", text: "scontrol (roh)" }));
      wrap.appendChild(B.el("pre", { class: "sheet-log", text: d.raw || "" }));
    }).catch(function () {
      wrap.textContent = ""; wrap.appendChild(B.el("div", { class: "empty", text: "nicht erreichbar" }));
    });
  }

  function procSheet(pid) {
    var wrap = B.el("div", { class: "stack" }, [B.el("div", { class: "muted", text: "lädt …" })]);
    B.overlay("Prozess " + pid, wrap);
    B.jget("/api/wa/slurmwatch/proc?pid=" + encodeURIComponent(pid)).then(function (d) {
      wrap.textContent = "";
      if (!d || d.ok === false) { wrap.appendChild(B.el("div", { class: "empty", text: (d && d.error) || "keine Daten" })); return; }
      wrap.appendChild(B.el("pre", { class: "sheet-log", text: d.raw || "" }));
    }).catch(function () {
      wrap.textContent = ""; wrap.appendChild(B.el("div", { class: "empty", text: "nicht erreichbar" }));
    });
  }

  var W = {
    title: "🧮 SlurmWatch",
    _filter: "alle",
    async fill(host) {

      if (host.contains(document.activeElement) && document.activeElement !== document.body) return;
      var keepScroll = host.scrollTop;
      var d; try { d = await B.jget("/api/wa/slurmwatch/activity"); } catch (e) { d = null; }
      host.textContent = "";
      if (!d) { host.appendChild(B.el("div", { class: "empty", text: "nicht erreichbar" })); return; }
      if (d.ok === false) {
        host.appendChild(B.el("div", { class: "empty", text: d.error || "Cluster nicht erreichbar" }));
        return;
      }
      var jobs = d.jobs || [], procs = d.procs || [];
      var self = this;
      var counts = { alle: jobs.length + procs.length, slurm: jobs.length, andere: procs.length };
      var bar = B.el("div", { class: "acct-bar" });
      [["alle", "Alle"], ["slurm", "Slurm"], ["andere", "Andere"]].forEach(function (kv) {
        bar.appendChild(chip(kv[1] + " (" + counts[kv[0]] + ")", self._filter === kv[0], false, function () {
          self._filter = kv[0]; self.fill(host);
        }));
      });
      host.appendChild(bar);
      var shown = 0;
      if (self._filter !== "andere") {
        jobs.slice(0, 14).forEach(function (j) {
          var st = String(j.state || "").toUpperCase();
          host.appendChild(B.el("div", { class: "rw-row", onclick: function () { jobSheet(j.id); } }, [
            B.el("span", { class: "badge s-" + st.toLowerCase(), text: st.slice(0, 4) }),
            B.el("span", { class: "ellipsis", title: j.name, text: j.id + " · " + j.name }),
            B.el("span", { class: "muted tnum", text: j.time || "" }),
          ]));
          shown++;
        });
      }
      if (self._filter !== "slurm") {
        procs.slice(0, 10).forEach(function (pcs) {
          host.appendChild(B.el("div", { class: "rw-row", onclick: function () { procSheet(pcs.pid); } }, [
            B.el("span", { class: "as-dot" }),
            B.el("span", { class: "ellipsis", title: pcs.cmd, text: pcs.cmd }),
            B.el("span", { class: "muted tnum", text: pcs.etime || "" }),
          ]));
          shown++;
        });
      }
      if (!shown) host.appendChild(B.el("div", { class: "empty", text: "keine Aktivität auf " + (d.cluster || "dem Cluster") }));
      else host.appendChild(B.el("div", { class: "bp-foot muted tnum", text: (d.cluster || "") + " · Stand " + new Date((d.ts || 0) * 1000).toLocaleTimeString() }));
      if (keepScroll) host.scrollTop = keepScroll;
    },
  };
  B.register("slurmwatch", W);
})();
