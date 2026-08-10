
'use strict';

let nodeCrypto = null;
try { nodeCrypto = require('crypto'); } catch (_) {   }

const LEAF_PREFIX = Buffer.from([0x00]);
const NODE_PREFIX = Buffer.from([0x01]);
const STH_DOMAIN = Buffer.from('brainarbeit/ledger/sth/1', 'utf8');

class WitnessAlarm extends Error {
  constructor(kind, reason, sth) {
    super(`[${kind}] ${reason}`);
    this.name = 'WitnessAlarm';
    this.kind = kind;
    this.reason = reason;
    this.sth = sth || null;
  }
}

const KIND_BAD_SIGNATURE = 'BAD_SIGNATURE';
const KIND_TRUNCATION = 'TRUNCATION';
const KIND_INCONSISTENT = 'INCONSISTENT';
const KIND_LOG_ID_MISMATCH = 'LOG_ID_MISMATCH';

function sha256(buf, opts) {
  if (opts && opts.hash) return Buffer.from(opts.hash(buf));
  return nodeCrypto.createHash('sha256').update(buf).digest();
}

function hashChildren(left, right, opts) {
  return sha256(Buffer.concat([NODE_PREFIX, left, right]), opts);
}

function verifyConsistency(first, second, proof, firstRoot, secondRoot, opts) {
  if (first < 0 || second < 0 || first > second) return false;
  if (first === 0) return proof.length === 0;
  if (first === second) return proof.length === 0 && firstRoot.equals(secondRoot);

  let path = proof.slice();

  if ((first & (first - 1)) === 0) path = [firstRoot].concat(path);

  let fn = first - 1;
  let sn = second - 1;
  while (fn & 1) { fn >>= 1; sn >>= 1; }

  if (path.length === 0) return false;
  let fr = path[0];
  let sr = path[0];
  for (let i = 1; i < path.length; i++) {
    const c = path[i];
    if (sn === 0) return false;
    if ((fn & 1) || (fn === sn)) {
      fr = hashChildren(c, fr, opts);
      sr = hashChildren(c, sr, opts);
      if (!(fn & 1)) {
        while (!(fn & 1) && fn !== 0) { fn >>= 1; sn >>= 1; }
      }
    } else {
      sr = hashChildren(sr, c, opts);
    }
    fn >>= 1;
    sn >>= 1;
  }
  return fr.equals(firstRoot) && sr.equals(secondRoot) && sn === 0;
}

function sthSigningBytes(treeSize, rootHash, timestamp) {
  const size = Buffer.alloc(8);
  size.writeBigUInt64BE(BigInt(treeSize));
  const ts = Buffer.alloc(8);
  ts.writeDoubleBE(timestamp);
  return Buffer.concat([STH_DOMAIN, size, rootHash, ts]);
}

function rawEd25519PubToKeyObject(pub32) {

  const header = Buffer.from('302a300506032b6570032100', 'hex');
  const der = Buffer.concat([header, pub32]);
  return nodeCrypto.createPublicKey({ key: der, format: 'der', type: 'spki' });
}

function edVerify(pub32, sig, msg, opts) {
  if (opts && opts.verify) return !!opts.verify(pub32, sig, msg);
  try {
    return nodeCrypto.verify(null, msg, rawEd25519PubToKeyObject(pub32), sig);
  } catch (_) {
    return false;
  }
}

function logIdForPubkey(pub32, opts) {
  if (pub32.length !== 32) throw new Error('ledger public key must be 32 raw bytes');
  return sha256(pub32, opts).toString('hex');
}

function verifySth(pub32, sth, opts) {
  try {
    if (logIdForPubkey(pub32, opts) !== sth.log_id) return false;
    const msg = sthSigningBytes(sth.tree_size, Buffer.from(sth.root_hash, 'hex'), sth.timestamp);
    return edVerify(pub32, Buffer.from(sth.signature, 'hex'), msg, opts);
  } catch (_) {
    return false;
  }
}

class Witness {

  constructor(pinnedPubkeyHex, opts = {}) {
    this.opts = opts;
    this.pinnedPubkeyHex = String(pinnedPubkeyHex).trim().toLowerCase();
    this.pinnedPub = Buffer.from(this.pinnedPubkeyHex, 'hex');
    this.logId = logIdForPubkey(this.pinnedPub, opts);
    this.onAlarm = opts.onAlarm || null;
    this.sths = [];
    this.alarms = [];

    this.store = opts.store || null;
    if (this.store) {
      const st = this.store.load();
      if (st) {
        if (st.pinned_pubkey !== this.pinnedPubkeyHex || st.log_id !== this.logId) {
          throw new WitnessAlarm(KIND_LOG_ID_MISMATCH,
            'stored witness state was pinned to a different ledger key');
        }
        this.sths = st.sths || [];
        this.alarms = st.alarms || [];
      }
    }
  }

  static enroll(pinnedPubkeyHex, opts = {}) {
    const w = new Witness(pinnedPubkeyHex, opts);
    w._persist();
    return w;
  }

  get latest() { return this.sths.length ? this.sths[this.sths.length - 1] : null; }
  get size() { return this.latest ? this.latest.tree_size : 0; }

  _persist() {
    if (!this.store) return;
    this.store.save({
      version: 1,
      pinned_pubkey: this.pinnedPubkeyHex,
      log_id: this.logId,
      sths: this.sths,
      alarms: this.alarms,
    });
  }

  _raiseAlarm(kind, reason, sth) {
    const rec = { kind, reason, ts: Date.now() / 1000, witnessed_size: this.size, sth: sth || null };
    this.alarms.push(rec);
    this._persist();
    if (this.onAlarm) { try { this.onAlarm(rec); } catch (_) {   } }
    throw new WitnessAlarm(kind, reason, sth);
  }

  poll(source) {
    const sth = source.getSth();

    if (sth.log_id !== this.logId) {
      this._raiseAlarm(KIND_LOG_ID_MISMATCH,
        `STH log_id ${sth.log_id.slice(0, 16)}... != pinned ${this.logId.slice(0, 16)}...`, sth);
    }
    if (!verifySth(this.pinnedPub, sth, this.opts)) {
      this._raiseAlarm(KIND_BAD_SIGNATURE,
        'STH signature does not verify under the pinned ledger key', sth);
    }

    const prev = this.latest;
    if (!prev) { this.sths.push(sth); this._persist(); return sth; }

    if (sth.tree_size < prev.tree_size) {
      this._raiseAlarm(KIND_TRUNCATION,
        `presented tree_size ${sth.tree_size} < witnessed ${prev.tree_size} `
        + '(append-only log cannot shrink -> tail truncated)', sth);
    }

    const proof = source.getConsistencyProof(prev.tree_size, sth.tree_size)
      .map((h) => (Buffer.isBuffer(h) ? h : Buffer.from(h, 'hex')));
    const ok = verifyConsistency(
      prev.tree_size, sth.tree_size, proof,
      Buffer.from(prev.root_hash, 'hex'), Buffer.from(sth.root_hash, 'hex'), this.opts);
    if (!ok) {
      this._raiseAlarm(KIND_INCONSISTENT,
        `consistency proof ${prev.tree_size}->${sth.tree_size} does not reconstruct the pinned `
        + 'root (history was rewritten)', sth);
    }

    if (sth.tree_size !== prev.tree_size || sth.root_hash !== prev.root_hash) {
      this.sths.push(sth);
      this._persist();
    }
    return sth;
  }
}

module.exports = {
  Witness, WitnessAlarm, verifyConsistency, verifySth, logIdForPubkey, sthSigningBytes,
  KIND_BAD_SIGNATURE, KIND_TRUNCATION, KIND_INCONSISTENT, KIND_LOG_ID_MISMATCH,
};
