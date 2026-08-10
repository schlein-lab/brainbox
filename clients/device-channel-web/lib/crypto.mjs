
import { blake2s } from '@noble/hashes/blake2s';
import { hmac } from '@noble/hashes/hmac';
import { x25519, ed25519 } from '@noble/curves/ed25519';
import { chacha20poly1305 } from '@noble/ciphers/chacha';

const te = new TextEncoder();
export const PROTOCOL = te.encode('Brainarbeit-relay/Noise_XXsig_25519_ChaChaPoly_BLAKE2s/1');

export const b2s = (msg, dkLen = 32) => blake2s(msg, { dkLen });
export const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, '0')).join('');
export const unhex = (s) => { const u = new Uint8Array(s.length / 2); for (let i = 0; i < u.length; i++) u[i] = parseInt(s.substr(i * 2, 2), 16); return u; };
export const cat = (...a) => { let n = 0; for (const x of a) n += x.length; const o = new Uint8Array(n); let k = 0; for (const x of a) { o.set(x, k); k += x.length; } return o; };
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

export const didFor = (idPub) => 'did:key:b2:' + hex(b2s(idPub, 16));
export const rendezvousTopic = (boxXPub) => 'rz_' + hex(b2s(cat(te.encode('rendezvous|'), boxXPub), 16));

export function hkdf(chain, ikm, n) {
  const tmp = hmac(blake2s, chain, ikm);
  const out = []; let prev = new Uint8Array(0);
  for (let i = 1; i <= n; i++) { prev = hmac(blake2s, tmp, cat(prev, Uint8Array.of(i))); out.push(prev); }
  return out;
}

const Z12 = new Uint8Array(12);
const enc = (key, nonce, pt, aad) => chacha20poly1305(key, nonce, aad).encrypt(pt);
const dec = (key, nonce, ct, aad) => chacha20poly1305(key, nonce, aad).decrypt(ct);

class Symmetric {
  constructor() { this.ck = b2s(PROTOCOL); this.h = this.ck; }
  mixHash(d) { this.h = b2s(cat(this.h, d)); }
  mixKey(ikm) { const [ck, k] = hkdf(this.ck, ikm, 2); this.ck = ck; return k; }
  split() { return hkdf(this.ck, new Uint8Array(0), 2); }
}

export class Session {
  constructor(kSend, kRecv, h) { this.kSend = kSend; this.kRecv = kRecv; this.h = h; this.nSend = 0n; this.nRecv = 0n; }
  _nonce(ctr) { const n = new Uint8Array(12); new DataView(n.buffer).setBigUint64(0, ctr, true); return n; }
  encrypt(pt) { const ct = enc(this.kSend, this._nonce(this.nSend), pt, this.h); this.nSend++; return ct; }
  decrypt(ct) { const pt = dec(this.kRecv, this._nonce(this.nRecv), ct, this.h); this.nRecv++; return pt; }
}

export class Handshake {
  constructor({ initiator, sxPriv, sxPub, idPriv, idPub }) {
    this.initiator = initiator; this.sxPriv = sxPriv; this.sxPub = sxPub; this.idPriv = idPriv; this.idPub = idPub;
    this.sym = new Symmetric();
    this.setEphemeral(x25519.utils.randomPrivateKey());
    this.sym.mixHash(new Uint8Array(0));
  }
  setEphemeral(priv) { this.exPriv = priv; this.exPub = x25519.getPublicKey(priv); }
  _authBlob(staticPub) { return cat(this.sym.h, staticPub); }
  _encStatic(pub) { const ct = enc(this.sym.ck, Z12, pub, this.sym.h); this.sym.mixHash(ct); return ct; }
  _decStatic(ct) { if (ct.length !== 48) throw new Error('bad static len'); const pt = dec(this.sym.ck, Z12, ct, this.sym.h); this.sym.mixHash(ct); return pt; }
  writeMsg1() { this.sym.mixHash(this.exPub); return this.exPub; }
  readMsg2(msg) {
    if (msg.length !== 32 + 48 + 32 + 64) throw new Error('malformed msg2');
    let o = 0;
    this.peerExPub = msg.slice(o, o + 32); o += 32; this.sym.mixHash(this.peerExPub);
    this.sym.mixKey(x25519.getSharedSecret(this.exPriv, this.peerExPub));
    const encS = msg.slice(o, o + 48); o += 48; this.peerSxPub = this._decStatic(encS);
    this.sym.mixKey(x25519.getSharedSecret(this.exPriv, this.peerSxPub));
    this.peerIdPub = msg.slice(o, o + 32); o += 32;
    const sig = msg.slice(o, o + 64); o += 64;
    if (!ed25519.verify(sig, this._authBlob(this.peerSxPub), this.peerIdPub)) throw new Error('box identity sig invalid');
  }
  writeMsg3() {
    const encS = this._encStatic(this.sxPub);
    this.sym.mixKey(x25519.getSharedSecret(this.sxPriv, this.peerExPub));
    const sig = ed25519.sign(this._authBlob(this.sxPub), this.idPriv);
    this.sym.mixHash(cat(this.idPub, sig));
    return cat(encS, this.idPub, sig);
  }
  assertPeer(boxIdPub, boxXPub) {
    if (!eq(this.peerIdPub, boxIdPub) || !eq(this.peerSxPub, boxXPub)) throw new Error('box identity mismatch (relay impersonation?)');
  }
  session() { const [k1, k2] = this.sym.split(); return this.initiator ? new Session(k1, k2, this.sym.h) : new Session(k2, k1, this.sym.h); }
}
