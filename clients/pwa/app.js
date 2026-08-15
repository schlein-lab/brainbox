
import { ConnectClient, Keystore, contract } from '../core/src/index.js';

const $ = (id) => document.getElementById(id);
const BOX_LABEL = 'home';
const ks = new Keystore();
let client = null;

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

async function boot() {
  const al = await ks.alliance(BOX_LABEL);
  if (al && al.token && al.base_url) {
    await startApp(al.base_url);
  } else {
    $('pair-card').classList.remove('hidden');
  }
}

$('pair-btn').onclick = async () => {
  const baseUrl = $('pair-base').value.trim();
  const code = $('pair-code').value.trim();
  const totpCode = $('pair-totp').value.trim();
  const label = $('pair-label').value.trim();
  if (!baseUrl || !code || !totpCode) {
    return setMsg('pair-msg', 'box address, code and 2FA code are all required', true);
  }
  setMsg('pair-msg', 'pairing…');
  try {
    const c = new ConnectClient({ boxLabel: BOX_LABEL, baseUrl, keystore: ks });
    await c.pair({ code, totpCode, label });

    const rec = await ks.alliance(BOX_LABEL);
    await ks.backend.set(BOX_LABEL, { ...rec, base_url: baseUrl });
    setMsg('pair-msg', 'paired ✓');
    $('pair-card').classList.add('hidden');
    await startApp(baseUrl);
  } catch (e) {
    setMsg('pair-msg', (e.need2fa ? '2FA required/incorrect: ' : '') + e.message, true);
  }
};

async function startApp(baseUrl) {
  client = new ConnectClient({ boxLabel: BOX_LABEL, baseUrl, keystore: ks });
  await client.connect();
  ensurePush(baseUrl);
  $('app').classList.remove('hidden');
  client.onStatus((s) => { $('status').textContent = s; $('status').className = 'status ' + s; });
  client.onUpdate(() => renderInbox());
  wireIntake();
  renderInbox();
}

function b64uToBytes(s) {
  const raw = atob(s.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
async function ensurePush(baseUrl) {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in self)) return;
    if (!('Notification' in self) || Notification.permission === 'denied') return;
    if (Notification.permission === 'default') {
      if ((await Notification.requestPermission()) !== 'granted') return;
    }
    const al = await ks.alliance(BOX_LABEL);
    const hdr = al && al.token ? { 'Authorization': 'Bearer ' + al.token } : {};
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      const vp = await fetch(baseUrl + '/api/push/vapid-pub', { headers: hdr });
      if (!vp.ok) return;
      const { key } = await vp.json();
      if (!key) return;
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64uToBytes(key),
      });
    }
    await fetch(baseUrl + '/api/push/subscribe', {
      method: 'POST',
      headers: { ...hdr, 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub.toJSON() }),
    });
  } catch {   }
}

function renderInbox() {
  if (!client || !client.reality) return;
  const items = client.reality.pendingApprovals();
  $('inbox-count').textContent = items.length;
  $('inbox-empty').classList.toggle('hidden', items.length > 0);
  const box = $('inbox');
  box.innerHTML = '';
  for (const cvm of items) {
    const card = document.createElement('div');
    card.className = 'approval' + (contract.actionLine(cvm)?.brick ? ' brick' : '');
    const act = contract.actionLine(cvm);
    card.innerHTML = `
      <div class="atitle">${esc(contract.title(cvm))}</div>
      ${act ? `<div class="aaction">about to: ${esc(act.text)}${act.brick ? ' ⚠️ ' + esc(act.brick) : ''}</div>` : ''}
      ${contract.digest(cvm) ? `<pre class="apreview">${esc(contract.digest(cvm))}</pre>` : ''}
      <div class="arow">
        <button class="primary ap">Approve</button>
        <button class="danger rj">Reject</button>
        <button class="rv">Revise…</button>
      </div>`;
    card.querySelector('.ap').onclick = () => client.approve(cvm);
    card.querySelector('.rj').onclick = () => client.reject(cvm);
    card.querySelector('.rv').onclick = () => {
      const fb = prompt('Revise — what should change?');
      if (fb) client.revise(cvm, fb);
    };
    box.appendChild(card);
  }
}

function wireIntake() {
  $('btn-send').onclick = async () => {
    const text = $('compose-text').value.trim();
    const path = $('compose-path').value.trim();
    if (path) await client.attach(text, { paths: [path] });
    else if (text) await client.sendText(text);
    $('compose-text').value = ''; $('compose-path').value = '';
    setMsg('compose-msg', 'sent ✓');
  };
  $('file-input').onchange = async (e) => {
    const files = [...e.target.files];
    await client.attach($('compose-text').value.trim() || '(file)', { files });
    setMsg('compose-msg', `sent ${files.length} file(s) ✓`);
  };
  let voiceRec = null;
  $('btn-mic').onclick = async () => {
    if (!voiceRec) {
      voiceRec = await client.sendVoiceMessage({ maxMs: 60000 });
      $('btn-mic').textContent = '⏹ Stop & send';
    } else {
      await voiceRec.finish(); voiceRec = null;
      $('btn-mic').textContent = '🎤 Voice msg';
      setMsg('compose-msg', 'voice message sent ✓');
    }
  };
  $('btn-call').onclick = () => startMedia(() => client.startCall({ video: false }));
  $('btn-video').onclick = () => startMedia(() => client.startCall({ video: true }));
  $('btn-screen').onclick = () => startMedia(() => client.shareScreen());
  $('btn-photo').onclick = async () => { await client.snapAndSend(); setMsg('compose-msg', 'photo sent ✓'); };
  $('btn-watch-mjpeg').onclick = async () => { $('watch-img').src = await client.watchUrl(); };
  $('btn-hangup').onclick = hangup;
  for (const b of document.querySelectorAll('.tabs button')) b.onclick = () => showView(b.dataset.view);
}

let liveSession = null;
async function startMedia(opener) {
  showView('watch');
  liveSession = await opener();
  liveSession.onRemoteStream((stream) => { $('remote-video').srcObject = stream; });
  $('btn-hangup').classList.remove('hidden');
}
function hangup() {
  if (liveSession) { liveSession.close(); liveSession = null; }
  $('btn-hangup').classList.add('hidden');
}

function showView(v) {
  for (const s of document.querySelectorAll('.view')) s.classList.add('hidden');
  $('view-' + v).classList.remove('hidden');
  for (const b of document.querySelectorAll('.tabs button')) b.classList.toggle('active', b.dataset.view === v);
}

function setMsg(id, text, err = false) { const el = $(id); el.textContent = text; el.className = 'msg' + (err ? ' err' : ''); }
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

boot();
