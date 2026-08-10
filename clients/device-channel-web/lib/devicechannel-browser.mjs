
import { DeviceChannel, generateKeys } from './devicechannel.mjs';
import { rendezvousTopic } from './crypto.mjs';

const DB = 'brainbox-device', STORE = 'kv', KEY = 'identity';

function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function kvGet(k) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readonly').objectStore(STORE).get(k);
    tx.onsuccess = () => res(tx.result || null); tx.onerror = () => rej(tx.error);
  });
}
async function kvSet(k, v) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).put(v, k);
    tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error);
  });
}

export const keystore = {
  async load() { return kvGet(KEY); },
  async save(rec) { await kvSet(KEY, rec); return rec; },
  async ensureKeys() {
    let rec = await kvGet(KEY);
    if (!rec) { rec = { keys: generateKeys() }; await kvSet(KEY, rec); }
    else if (!rec.keys) { rec.keys = generateKeys(); await kvSet(KEY, rec); }
    return rec;
  },
  isPaired: (rec) => !!(rec && rec.token && rec.boxIdPub && rec.boxSxPub),
};

export async function openChannel({ relayUrl = 'wss://rz.brainarbeit.com/', origin = location.origin } = {}) {
  const rec = await keystore.load();
  if (!keystore.isPaired(rec)) throw new Error('device not paired yet — pair on-LAN first');
  const dc = new DeviceChannel({
    relayUrl, topic: rendezvousTopic(hexToBytes(rec.boxSxPub)),
    boxIdPub: rec.boxIdPub, boxSxPub: rec.boxSxPub, keys: rec.keys,
    WebSocketImpl: globalThis.WebSocket, origin,
  });
  await dc.connect();
  const hi = await dc.hello(rec.token);
  if (hi.t !== 'hello_ok') { dc.close(); throw new Error('reconnect rejected: ' + (hi.error || hi.t)); }
  return { dc, principal: hi.principal, caps: hi.caps };
}

export async function pairDevice({ code, totp, label, boxIdPub, boxSxPub, relayUrl = 'wss://rz.brainarbeit.com/', origin = location.origin }) {
  const rec = await keystore.ensureKeys();
  const dc = new DeviceChannel({
    relayUrl, topic: rendezvousTopic(hexToBytes(boxSxPub)),
    boxIdPub, boxSxPub, keys: rec.keys, WebSocketImpl: globalThis.WebSocket, origin,
  });
  await dc.connect();
  const r = await dc.pair(code, totp, label);
  dc.close();
  if (r.t !== 'pair_ok') throw new Error(r.error || 'pairing failed');
  await keystore.save({ keys: rec.keys, token: r.token, boxIdPub, boxSxPub });
  return { principal: r.principal, caps: r.caps };
}

function hexToBytes(s) { const u = new Uint8Array(s.length / 2); for (let i = 0; i < u.length; i++) u[i] = parseInt(s.substr(i * 2, 2), 16); return u; }
