
"use strict";

const CVMRender = {

  title(cvm) {
    const ar = cvm.approval_request || {};
    return ar.summary || cvm.task_type || ("job #" + cvm.id);
  },

  actionLine(cvm) {
    const ar = cvm.approval_request || {};
    if (ar.action) return { text: ar.action, brick: ar.brick_warning || null };

    if (cvm.task_type && cvm.task_type !== "(raw)") return { text: cvm.task_type, brick: null };
    return null;
  },

  digest(cvm) {
    const ar = cvm.approval_request || {};
    if (ar.digest) return ar.digest;
    if (ar.preview) return ar.preview;
    if (cvm.partial) return typeof cvm.partial === "string"
      ? cvm.partial : JSON.stringify(cvm.partial, null, 2);
    return null;
  },

  diff(cvm) {
    const ar = cvm.approval_request || {};
    return ar.diff || null;
  },

  isAwaiting(cvm) {
    return cvm.approval_state === "pending" &&
           (cvm.state === "staged" || cvm.state === "awaiting_approval");
  },

  approvalSummary(cvm) {
    const act = this.actionLine(cvm);
    const a = act ? (" — " + act.text + (act.brick ? " [BRICK RISK]" : "")) : "";
    return `Approval needed: ${this.title(cvm)}${a}`;
  },
};

if (typeof module !== "undefined") module.exports = { CVMRender };
if (typeof window !== "undefined") window.CVMRender = CVMRender;

if (typeof window !== "undefined") (function () {

const SHELL_NATIVE = new URLSearchParams(location.search).get("shell") === "native";
const bridge = (SHELL_NATIVE && window.pncockpit) ? window.pncockpit : null;

const state = {
  cursor: 0,
  jobs: new Map(),
  ws: null,
  connected: false,
  backoff: 500,
};

function myTopics() {
  const q = new URLSearchParams(location.search).get("topics");
  if (q) return q.split(",");
  return ["user/me"];
}

function wsURL() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const t = encodeURIComponent(myTopics().join(","));

  return `${proto}://${location.host}/ws/events?topics=${t}&after_id=${state.cursor}`;
}

function connect() {
  setConn("connecting");
  let ws;
  try { ws = new WebSocket(wsURL()); }
  catch (e) { return scheduleReconnect(); }
  state.ws = ws;

  ws.onopen = () => { state.backoff = 500; };
  ws.onmessage = (ev) => {
    let frame;
    try { frame = JSON.parse(ev.data); } catch (e) { return; }
    handleFrame(frame);
  };
  ws.onclose = () => { setConn("offline"); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function scheduleReconnect() {
  state.connected = false;
  setTimeout(connect, state.backoff);
  state.backoff = Math.min(state.backoff * 2, 8000);
}

function handleFrame(frame) {
  if (frame.type === "subscribed") {
    setConn("live");
    state.connected = true;

    if (typeof frame.cursor === "number" && state.cursor === 0) state.cursor = frame.cursor;
    return;
  }
  if (frame.type === "ping") { return; }
  if (frame.type === "event") {
    applyEvent(frame.event);
    return;
  }
}

function applyEvent(e) {
  if (typeof e.id === "number") state.cursor = Math.max(state.cursor, e.id);
  setCursorPill(state.cursor);
  const jid = e.job_id;
  if (jid == null) return;
  let cvm = state.jobs.get(jid);
  if (!cvm) { cvm = { id: jid, state: "?", approval_state: null }; state.jobs.set(jid, cvm); }

  let data = e.data;
  if (typeof data === "string") { try { data = JSON.parse(data); } catch (x) { data = e.data; } }

  switch (e.kind) {
    case "approval-request":
      cvm.approval_request = data || {};
      cvm.task_type = (data && data.task_type) || cvm.task_type;
      cvm.state = "staged";
      cvm.approval_state = "pending";
      cvm.nonce = data && data.nonce;
      cvm.needs_confirmation = true;
      onNewApproval(cvm);
      break;
    case "state":
      cvm.state = (data && data.state) || cvm.state;
      if (data && data.decision) {
        cvm.approval_state = data.decision;
      }
      break;
    case "progress":
      cvm.progress = data || cvm.progress;
      break;
    case "checkpoint":
    case "partial":
      cvm.partial = data;
      break;
    default: break;
  }
  render();
}

async function verb(body) {
  const r = await fetch("/api/verb", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function decide(cvm, decision, feedback) {
  const card = document.getElementById("card-" + cvm.id);
  if (card) card.classList.add("resolving");

  if (decision === "revise") {
    if (cvm.state === "running") {
      await verb({ verb: "steer", id: cvm.id, input: { feedback } });
    } else {

      const r = await verb({ verb: "approve", nonce: cvm.nonce });
      if (r.ok) await verb({ verb: "steer", id: cvm.id, input: { feedback } }).catch(() => {});
    }
    toast("Sent revision");
    return;
  }
  const v = decision === "approve" ? "approve" : "deny";
  const res = await verb({ verb: v, nonce: cvm.nonce });
  if (!res.ok && !res.idempotent) {
    if (card) card.classList.remove("resolving");
    toast("Decision failed: " + (res.error || "?"), true);
    return;
  }

  toast(decision === "approve" ? "Approved" : "Rejected");
}

function awaiting() {
  return [...state.jobs.values()].filter(c => CVMRender.isAwaiting(c))
         .sort((a, b) => a.id - b.id);
}

function render() {
  renderInbox();
  renderQueue();
  const n = awaiting().length;
  const badge = document.getElementById("inbox-badge");
  badge.textContent = n; badge.classList.toggle("hidden", n === 0);
  document.getElementById("inbox-empty").classList.toggle("hidden", n !== 0);
  if (bridge && bridge.setBadge) try { bridge.setBadge(n); } catch (e) {}
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderDiff(diff) {
  return esc(diff).split("\n").map(l => {
    const cls = l.startsWith("+") ? "diff-add" : l.startsWith("-") ? "diff-del" : "";
    return `<span class="${cls}">${l}</span>`;
  }).join("\n");
}

function approvalCardHTML(cvm) {
  const act = CVMRender.actionLine(cvm);
  const digest = CVMRender.digest(cvm);
  const diff = CVMRender.diff(cvm);
  const decided = cvm.approval_state === "approved" || cvm.approval_state === "denied";
  let body = "";
  body += `<div class="card-head">
      <span class="card-title">${esc(CVMRender.title(cvm))}</span>
      <span class="state ${esc(cvm.state)}">${esc(cvm.state)}</span>
      <span class="card-meta">job #${esc(cvm.id)} · ${esc(cvm.task_type || "(raw)")}</span>
    </div>`;
  if (act) {
    body += `<div class="action-line">about to: <b>${esc(act.text)}</b>`;
    if (act.brick) body += ` <span class="warn">⚠ ${esc(act.brick)}</span>`;
    body += `</div>`;
  }
  if (digest) body += `<div class="digest"><div class="label">result / artifact</div>
      <div class="preview">${esc(digest)}</div></div>`;
  if (diff) body += `<div class="digest"><div class="label">proposed diff</div>
      <div class="preview">${renderDiff(diff)}</div></div>`;
  if (decided) {
    const cls = cvm.approval_state === "approved" ? "approve" : "deny";
    const word = cvm.approval_state === "approved" ? "Approved ✓" : "Rejected ✕";
    body += `<div class="resolved-note ${cls}">${word} — cleared on all devices</div>`;
  } else {
    body += `<div class="actions">
        <button class="act approve" data-act="approve" data-id="${cvm.id}">Approve</button>
        <button class="act reject"  data-act="reject"  data-id="${cvm.id}">Reject</button>
        <button class="act revise"  data-act="revise"  data-id="${cvm.id}">Revise…</button>
      </div>
      <div class="revise-box" id="revise-${cvm.id}">
        <textarea placeholder="What should change?"></textarea>
        <div class="actions"><button class="act revise" data-act="revise-send" data-id="${cvm.id}">Send revision</button></div>
      </div>`;
  }
  return body;
}

function renderInbox() {
  const list = document.getElementById("inbox-list");
  const items = [...state.jobs.values()]
    .filter(c => CVMRender.isAwaiting(c) || c.approval_state === "approved" || c.approval_state === "denied")
    .filter(c => c.needs_confirmation)
    .sort((a, b) => a.id - b.id);

  const seen = new Set();
  for (const cvm of items) {
    seen.add("card-" + cvm.id);
    let card = document.getElementById("card-" + cvm.id);
    if (!card) {
      card = document.createElement("div");
      card.className = "card approval";
      card.id = "card-" + cvm.id;
      list.appendChild(card);
    }
    card.innerHTML = approvalCardHTML(cvm);
  }

  [...list.children].forEach(c => { if (!seen.has(c.id)) c.remove(); });
}

function renderQueue() {
  const list = document.getElementById("queue-list");
  const items = [...state.jobs.values()].sort((a, b) => b.id - a.id);
  list.innerHTML = items.map(cvm => {
    const p = cvm.progress;
    const pct = (p && p.total) ? Math.round(100 * (p.done || 0) / p.total) : null;
    return `<div class="card">
      <div class="card-head">
        <span class="card-title">${esc(CVMRender.title(cvm))}</span>
        <span class="state ${esc(cvm.state)}">${esc(cvm.state)}</span>
        <span class="card-meta">job #${esc(cvm.id)}</span>
      </div>
      ${pct != null ? `<div class="progress"><i style="width:${pct}%"></i></div>
        <div class="card-meta">${esc((p.msg)||"")} ${pct}%</div>` : ""}
    </div>`;
  }).join("") || `<p class="hint">No jobs yet.</p>`;
}

function setConn(s) {
  const el = document.getElementById("health-conn");
  el.textContent = "conn " + s;
  el.className = "pill " + (s === "live" ? "ok" : s === "offline" ? "bad" : "warn");
}
function setCursorPill(c) { document.getElementById("health-cursor").textContent = "cursor " + c; }

let toastTimer = null;
function toast(msg, bad) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

function onNewApproval(cvm) {

  if (bridge && bridge.notify) {
    try { bridge.notify("Approval needed", CVMRender.title(cvm)); } catch (e) {}
  } else if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    try { new Notification("Approval needed", { body: CVMRender.title(cvm) }); } catch (e) {}
  }
}

async function refreshHealth() {
  try {
    const r = await verb({ verb: "status" });
    document.getElementById("health-detail").textContent = JSON.stringify(r, null, 2);
    const ok = r.ok;
    const b = document.getElementById("health-brain");
    b.textContent = ok ? "brain ok" : "brain ?";
    b.className = "pill " + (ok ? "ok" : "warn");
  } catch (e) {   }
}

document.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.classList && t.classList.contains("tab")) {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".view").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("view-" + t.dataset.view).classList.add("active");
    if (t.dataset.view === "health") refreshHealth();
    return;
  }
  const act = t.dataset && t.dataset.act;
  if (!act) return;
  const id = Number(t.dataset.id);
  const cvm = state.jobs.get(id);
  if (!cvm) return;
  if (act === "approve") decide(cvm, "approve");
  else if (act === "reject") decide(cvm, "reject");
  else if (act === "revise") document.getElementById("revise-" + id).classList.toggle("open");
  else if (act === "revise-send") {
    const ta = document.querySelector("#revise-" + id + " textarea");
    decide(cvm, "revise", ta ? ta.value : "");
  }
});

if (bridge && bridge.onDeepLink) {
  try { bridge.onDeepLink((url) => {
    const m = /job\/(\d+)/.exec(url || "");
    if (m) {   }
  }); } catch (e) {}
}

if (!bridge && typeof Notification !== "undefined" && Notification.permission === "default") {
  Notification.requestPermission().catch(() => {});
}

connect();
refreshHealth();
setInterval(refreshHealth, 20000);

})();
