
import { Keystore, WrappedIdbBackend, migrateLocalStorage } from '../src/keystore.js';

class MapKv {
  constructor() { this.m = new Map(); }
  async get(k) { return this.m.has(k) ? this.m.get(k) : undefined; }
  async set(k, v) { this.m.set(k, v); }
  async del(k) { this.m.delete(k); }
  async keys() { return Array.from(this.m.keys()); }
}

export async function keystoreTests(ok) {
  console.log('\n== keystore: wrapped-at-rest (audit E3) ==');
  const TOKEN = 'tok_e3_9c2f4d8a1b_SECRET';
  const kv = new MapKv();
  const be = new WrappedIdbBackend({ kv });
  const ks = new Keystore(be);

  await ks.saveAlliance('home', { principal: 'chris', token: TOKEN });

  const rec = await ks.alliance('home');
  ok('token round-trips through the wrapped backend', rec && rec.token === TOKEN);
  ok('isPaired sees the wrapped token', await ks.isPaired('home'));

  const dump = JSON.stringify(Array.from(kv.m.entries()), (k, v) =>
    (v && v.constructor && v.constructor.name === 'CryptoKey') ? '[CryptoKey]' : v);
  ok('cleartext token NEVER appears in the backing store', !dump.includes(TOKEN));
  const stored = kv.m.get('alliance:home');
  ok('stored record carries token_wrapped (iv+ct), not token',
    stored && !('token' in stored) && stored.token_wrapped
    && Array.isArray(stored.token_wrapped.iv) && Array.isArray(stored.token_wrapped.ct));

  const wrapKey = kv.m.get('__wrapkey__');
  ok('wrap key is non-extractable', wrapKey && wrapKey.extractable === false);
  const exportRefused = await crypto.subtle.exportKey('raw', wrapKey).then(() => false, () => true);
  ok('exportKey on the wrap key is refused by the platform', exportRefused);

  const broken = { ...stored, token_wrapped: { iv: stored.token_wrapped.iv,
    ct: stored.token_wrapped.ct.map((b) => (b + 1) % 256) } };
  kv.m.set('alliance:home', broken);
  const rec2 = await ks.alliance('home');
  ok('corrupted ciphertext -> record without token (re-pair, not crash)',
    rec2 && !rec2.token);
  ok('isPaired is false on corrupted ciphertext', !(await ks.isPaired('home')));

  const lsData = {};
  globalThis.localStorage = {
    getItem: (k) => (k in lsData ? lsData[k] : null),
    setItem: (k, v) => { lsData[k] = String(v); },
    removeItem: (k) => { delete lsData[k]; },
  };
  lsData['brainarbeit.connect.alliances'] =
    JSON.stringify({ alliances: { alt: { box_label: 'alt', principal: 'p', token: TOKEN } } });
  const kv2 = new MapKv();
  const be2 = new WrappedIdbBackend({ kv: kv2 });
  be2._migrated = migrateLocalStorage(be2);
  const ks2 = new Keystore(be2);
  const migriert = await ks2.alliance('alt');
  ok('legacy localStorage record migrates into the wrapped store', migriert && migriert.token === TOKEN);
  ok('legacy cleartext copy is REMOVED after migration',
    localStorage.getItem('brainarbeit.connect.alliances') === null);
  const dump2 = JSON.stringify(Array.from(kv2.m.entries()), (k, v) =>
    (v && v.constructor && v.constructor.name === 'CryptoKey') ? '[CryptoKey]' : v);
  ok('migrated token is wrapped in the new store, not cleartext', !dump2.includes(TOKEN));
  delete globalThis.localStorage;

  ok('wrapped backend reports strength "wrapped-at-rest"', ks.strength() === 'wrapped-at-rest');
  const fallback = new Keystore();
  ok('fallback (no IndexedDB) honestly reports "cleartext"', fallback.strength() === 'cleartext');
}
