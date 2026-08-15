
const STORE_KEY = 'brainarbeit.connect.alliances';
const IDB_NAME = 'brainarbeit.connect';
const IDB_STORE = 'kv';
const KEY_SLOT = '__wrapkey__';
const REC_PREFIX = 'alliance:';

class MemoryBackend {
  constructor() {
    this.strength = 'cleartext';
    this.data = this._load();
  }
  _load() {
    try {
      const raw = (globalThis.localStorage && localStorage.getItem(STORE_KEY)) || null;
      return raw ? JSON.parse(raw) : { alliances: {} };
    } catch { return { alliances: {} }; }
  }
  _save() {
    try { globalThis.localStorage && localStorage.setItem(STORE_KEY, JSON.stringify(this.data)); }
    catch {   }
  }
  async get(label) { return this.data.alliances[label] || null; }
  async set(label, rec) { this.data.alliances[label] = rec; this._save(); }
  async del(label) { delete this.data.alliances[label]; this._save(); }
  async list() { return Object.values(this.data.alliances); }
}

async function makeWrapKey(subtle) {
  return subtle.generateKey({ name: 'AES-GCM', length: 256 },   false,
    ['encrypt', 'decrypt']);
}

async function wrapToken(subtle, key, token) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(token));
  return { iv: Array.from(iv), ct: Array.from(new Uint8Array(ct)) };
}

async function unwrapToken(subtle, key, wrapped) {
  const pt = await subtle.decrypt({ name: 'AES-GCM', iv: new Uint8Array(wrapped.iv) },
    key, new Uint8Array(wrapped.ct));
  return new TextDecoder().decode(pt);
}

class IdbKv {
  async _db() {
    if (this._d) return this._d;
    this._d = await new Promise((res, rej) => {
      const rq = indexedDB.open(IDB_NAME, 1);
      rq.onupgradeneeded = () => rq.result.createObjectStore(IDB_STORE);
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
    });
    return this._d;
  }
  async _tx(mode, fn) {
    const db = await this._db();
    return new Promise((res, rej) => {
      const tx = db.transaction(IDB_STORE, mode);
      const out = fn(tx.objectStore(IDB_STORE));
      tx.oncomplete = () => res(out.result !== undefined ? out.result : out._value);
      tx.onerror = () => rej(tx.error);
    });
  }
  get(k) { return this._tx('readonly', (s) => s.get(k)); }
  set(k, v) { return this._tx('readwrite', (s) => { s.put(v, k); return { _value: v }; }); }
  del(k) { return this._tx('readwrite', (s) => { s.delete(k); return { _value: null }; }); }
  keys() { return this._tx('readonly', (s) => s.getAllKeys()); }
}

export class WrappedIdbBackend {
  constructor({ kv = null, subtle = null } = {}) {
    this.strength = 'wrapped-at-rest';
    this.kv = kv || new IdbKv();
    this.subtle = subtle || crypto.subtle;
  }
  async _key() {
    if (this._k) return this._k;
    let k = await this.kv.get(KEY_SLOT);
    if (!k) { k = await makeWrapKey(this.subtle); await this.kv.set(KEY_SLOT, k); }
    this._k = k;
    return k;
  }
  async get(label) {
    const rec = await this.kv.get(REC_PREFIX + label);
    if (!rec) return null;
    const out = { ...rec };
    if (out.token_wrapped) {

      try { out.token = await unwrapToken(this.subtle, await this._key(), out.token_wrapped); }
      catch {   }
      delete out.token_wrapped;
    }
    return out;
  }
  async set(label, rec) {
    const stored = { ...rec };
    if (typeof stored.token === 'string' && stored.token) {
      stored.token_wrapped = await wrapToken(this.subtle, await this._key(), stored.token);
      delete stored.token;
    }
    await this.kv.set(REC_PREFIX + label, stored);
  }
  async del(label) { return this.kv.del(REC_PREFIX + label); }
  async list() {
    const keys = (await this.kv.keys()) || [];
    const out = [];
    for (const k of keys) {
      if (typeof k === 'string' && k.startsWith(REC_PREFIX)) {
        out.push(await this.get(k.slice(REC_PREFIX.length)));
      }
    }
    return out;
  }
}

export async function migrateLocalStorage(backend) {
  try {
    const raw = globalThis.localStorage && localStorage.getItem(STORE_KEY);
    if (!raw) return;
    const old = JSON.parse(raw);
    for (const [label, rec] of Object.entries(old.alliances || {})) {
      if (!(await backend.get(label))) await backend.set(label, rec);
    }
    localStorage.removeItem(STORE_KEY);
  } catch {   }
}

function pickBackend() {
  const hasIdb = typeof indexedDB !== 'undefined';
  const hasSubtle = typeof crypto !== 'undefined' && !!crypto.subtle;
  if (hasIdb && hasSubtle) {
    const b = new WrappedIdbBackend();
    b._migrated = migrateLocalStorage(b);
    return b;
  }
  return new MemoryBackend();
}

export class Keystore {
  constructor(backend) { this.backend = backend || pickBackend(); }
  setBackend(b) { this.backend = b; }

  strength() { return this.backend.strength || 'os-keychain'; }
  async _settled() { if (this.backend._migrated) await this.backend._migrated; }

  async saveBoxKeys(label, { relayUrl, applianceIdPubkey, applianceXPubkey,
    rendezvousTopic = null, principal = null } = {}) {
    await this._settled();
    const rec = (await this.backend.get(label)) || { box_label: label };
    Object.assign(rec, { relay_url: relayUrl, appliance_id_pubkey: applianceIdPubkey,
      appliance_x_pubkey: applianceXPubkey });
    if (rendezvousTopic) rec.rendezvous_topic = rendezvousTopic;
    if (principal) rec.principal = principal;
    await this.backend.set(label, rec);
    return rec;
  }

  async saveAlliance(label, { principal, token, did = null, caps = null } = {}) {
    await this._settled();
    const rec = (await this.backend.get(label)) || { box_label: label };
    Object.assign(rec, { principal, token, paired_at: Date.now() / 1000 });
    if (did) rec.did = did;
    if (caps) rec.caps = caps;
    await this.backend.set(label, rec);
    return rec;
  }

  async alliance(label) { await this._settled(); return this.backend.get(label); }
  async isPaired(label) {
    await this._settled();
    const a = await this.backend.get(label); return !!(a && a.token);
  }

  async forget(label) { await this._settled(); return this.backend.del(label); }
  async list() { await this._settled(); return this.backend.list(); }
}
