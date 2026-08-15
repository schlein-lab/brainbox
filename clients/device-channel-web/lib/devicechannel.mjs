
import { x25519, ed25519 } from '@noble/curves/ed25519';
import { Handshake, hex, unhex, didFor } from './crypto.mjs';

const te = new TextEncoder(), td = new TextDecoder();
const jenc = (o) => te.encode(JSON.stringify(o));
const jdec = (b) => JSON.parse(td.decode(b));

function canonicalBytes(obj) {
  const keys = ['t', 'task_type', 'params', 'cmd', 'class', 'nonce', 'ts', 'counter', 'session_token'];
  const present = {};
  for (const k of keys) if (k in obj) present[k] = obj[k];
  const ser = (v) => {
    if (v === null || typeof v !== 'object') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(ser).join(',') + ']';
    return '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + ser(v[k])).join(',') + '}';
  };
  return te.encode(ser(present));
}

export function generateKeys() {
  const idPriv = ed25519.utils.randomPrivateKey();
  const sxPriv = x25519.utils.randomPrivateKey();
  return {
    idPriv: hex(idPriv), idPub: hex(ed25519.getPublicKey(idPriv)),
    sxPriv: hex(sxPriv), sxPub: hex(x25519.getPublicKey(sxPriv)),
  };
}

export class DeviceChannel {

  constructor(opts) {
    this.o = { recvTimeoutMs: 10000, ...opts };
    this.boxIdPub = unhex(opts.boxIdPub);
    this.boxSxPub = unhex(opts.boxSxPub);
    this.idPriv = unhex(opts.keys.idPriv); this.idPub = unhex(opts.keys.idPub);
    this.sxPriv = unhex(opts.keys.sxPriv); this.sxPub = unhex(opts.keys.sxPub);
    this.did = didFor(this.idPub);
    this.ses = null; this._ws = null;
    this.sessionToken = null; this.counter = 0;
  }

  static rendezvousTopicFor(boxSxPubHex) {

    throw new Error('use crypto.rendezvousTopic(boxSxPub) — exposed there');
  }

  async connect() {
    const WS = this.o.WebSocketImpl;
    const ws = this.o.origin ? new WS(this.o.relayUrl, { origin: this.o.origin }) : new WS(this.o.relayUrl);
    if ('binaryType' in ws) ws.binaryType = 'arraybuffer';
    this._ws = ws;
    const q = [], waiters = [];
    const onMsg = (data) => {
      let f; try { f = JSON.parse(typeof data === 'string' ? data : new TextDecoder().decode(data)); } catch { return; }
      if (f.t !== 'data') return;
      if (waiters.length) waiters.shift()(f.blob); else q.push(f.blob);
    };

    if (ws.on) ws.on('message', onMsg); else ws.onmessage = (ev) => onMsg(ev.data);
    await new Promise((res, rej) => {
      const to = setTimeout(() => rej(new Error('ws open timeout')), 12000);
      const ok = () => { clearTimeout(to); res(); };
      if (ws.on) { ws.once('open', ok); ws.once('error', rej); }
      else { ws.onopen = ok; ws.onerror = () => rej(new Error('ws error')); }
    });
    this._recv = (ms = this.o.recvTimeoutMs) => new Promise((res, rej) => {
      if (q.length) return res(q.shift());
      const to = setTimeout(() => rej(new Error('recv timeout')), ms);
      waiters.push((b) => { clearTimeout(to); res(b); });
    });
    this._send = (o) => ws.send(JSON.stringify(o));

    this._send({ t: 'dial', rz: this.o.topic });
    const hs = new Handshake({ initiator: true, sxPriv: this.sxPriv, sxPub: this.sxPub, idPriv: this.idPriv, idPub: this.idPub });
    this._send({ t: 'data', rz: this.o.topic, blob: hex(hs.writeMsg1()) });
    hs.readMsg2(unhex(await this._recv()));
    hs.assertPeer(this.boxIdPub, this.boxSxPub);
    this._send({ t: 'data', rz: this.o.topic, blob: hex(hs.writeMsg3()) });
    this._hs = hs; this.ses = hs.session();
    return this;
  }

  async _rpc(obj) {
    this._send({ t: 'data', rz: this.o.topic, blob: hex(this.ses.encrypt(jenc(obj))) });
    const resp = jdec(this.ses.decrypt(unhex(await this._recv())));
    if (resp && resp.session_token) this.sessionToken = resp.session_token;
    return resp;
  }

  async pair(code, totp, label) {
    const proof = ed25519.sign(this.ses.h, this.idPriv);
    const r = await this._rpc({ t: 'pair_request', code, device_id_pubkey: hex(this.idPub), proof: hex(proof), totp, label });
    if (r.t === 'pair_ok') this.token = r.token;
    return r;
  }

  async hello(token) {
    return this._rpc({ t: 'hello', did: this.did, token: token || this.token });
  }

  async submit({ task_type = null, params = null, cmd = null, cls = 'worker', stepUp2fa = null } = {}) {
    this.counter += 1;
    const p = { t: 'submit' };
    if (task_type != null) { p.task_type = task_type; p.params = params || {}; }
    if (cmd != null) p.cmd = cmd;
    p['class'] = cls;
    p.nonce = hex(ed25519.utils.randomPrivateKey().slice(0, 8));
    p.ts = Date.now() / 1000;
    p.counter = this.counter;
    if (this.sessionToken) p.session_token = this.sessionToken;
    const sig = ed25519.sign(canonicalBytes(p), this.idPriv);
    if (stepUp2fa != null) p.step_up_2fa = stepUp2fa;
    p.sig = hex(sig);
    return this._rpc(p);
  }

  close() { try { this._send({ t: 'bye', rz: this.o.topic }); } catch {} try { this._ws.close(); } catch {} }
}
