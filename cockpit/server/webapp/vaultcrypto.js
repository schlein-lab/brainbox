
(function (root) {
  "use strict";

  var _wc = (typeof globalThis !== "undefined" && globalThis.crypto && globalThis.crypto.subtle)
    ? globalThis.crypto
    : (typeof require !== "undefined" ? require("node:crypto").webcrypto : null);
  if (!_wc || !_wc.subtle) throw new Error("WebCrypto unavailable");
  var subtle = _wc.subtle;

  var ITER = 600000;
  var enc = new TextEncoder();
  var dec = new TextDecoder();

  function _rand(n) { return _wc.getRandomValues(new Uint8Array(n)); }
  function _utf8(s) { return enc.encode(s); }
  function _str(u8) { return dec.decode(u8); }

  function _b64e(u8) {
    if (typeof Buffer !== "undefined") return Buffer.from(u8).toString("base64");
    var s = ""; for (var i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s);
  }
  function _b64d(b64) {
    if (typeof Buffer !== "undefined") return new Uint8Array(Buffer.from(b64, "base64"));
    var s = atob(b64), u8 = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
    return u8;
  }

  async function _deriveKey(secret, saltU8, iter) {
    var base = await subtle.importKey("raw", _utf8(secret), { name: "PBKDF2" }, false, ["deriveKey"]);
    return subtle.deriveKey(
      { name: "PBKDF2", salt: saltU8, iterations: iter, hash: "SHA-256" },
      base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }
  function _importVMK(vmkBytes) {
    return subtle.importKey("raw", vmkBytes, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
  }
  async function _enc(key, plainU8) {
    var iv = _rand(12);
    var ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv: iv }, key, plainU8));
    return { iv: _b64e(iv), ct: _b64e(ct) };
  }
  async function _dec(key, obj) {
    var pt = await subtle.decrypt({ name: "AES-GCM", iv: _b64d(obj.iv) }, key, _b64d(obj.ct));
    return new Uint8Array(pt);
  }

  async function _wrapVMK(vmkBytes, secret, iter) {
    var salt = _rand(16);
    var key = await _deriveKey(secret, salt, iter);
    var w = await _enc(key, vmkBytes);
    return { kdf: "PBKDF2-SHA256", salt: _b64e(salt), iter: iter, iv: w.iv, ct: w.ct };
  }

  async function createVault(passphrase, recoveryCode, opts) {
    var iter = (opts && opts.iter) || ITER;
    var passWhich = (opts && opts.passWhich) || "pass";
    if (!passphrase) throw new Error("passphrase required");
    if (!recoveryCode) throw new Error("recovery code required");
    var vmk = _rand(32);
    var wrap = {};
    wrap[passWhich] = await _wrapVMK(vmk, passphrase, iter);
    wrap.recov = await _wrapVMK(vmk, recoveryCode, iter);
    var vaultKey = await _importVMK(vmk);
    var vault = await _enc(vaultKey, _utf8(JSON.stringify({})));
    return { v: 1, wrap: wrap, vault: vault };
  }

  async function unlock(blob, secret, which) {
    which = which || "pass";
    var w = blob && blob.wrap && blob.wrap[which];
    if (!w) throw new Error("no such unlock method: " + which);
    var key = await _deriveKey(secret, _b64d(w.salt), w.iter);
    var vmkBytes;
    try { vmkBytes = await _dec(key, w); }
    catch (e) { throw new Error("unlock failed (wrong " + which + "?)"); }
    var entries;
    try { entries = JSON.parse(_str(await _dec(await _importVMK(vmkBytes), blob.vault))); }
    catch (e) { throw new Error("vault decrypt failed (corrupt blob?)"); }
    return { vmkBytes: vmkBytes, entries: entries };
  }

  async function save(blob, vmkBytes, entries) {
    var vault = await _enc(await _importVMK(vmkBytes), _utf8(JSON.stringify(entries)));
    return { v: blob.v || 1, wrap: blob.wrap, vault: vault };
  }

  async function addWrap(blob, vmkBytes, secret, which, opts) {
    var iter = (opts && opts.iter) || ITER;
    var wrap = Object.assign({}, blob.wrap);
    wrap[which] = await _wrapVMK(vmkBytes, secret, iter);
    return { v: blob.v || 1, wrap: wrap, vault: blob.vault };
  }

  function generateRecoveryCode() {
    var b = _rand(20), A = "ABCDEFGHIJKLMNPQRSTUVWXYZ23456789", s = "";
    for (var i = 0; i < b.length; i++) s += A[b[i] & 31];
    return s.match(/.{1,5}/g).join("-");
  }

  var _CH_INFO = _utf8("brainarbeit-zkchannel-v1");
  function _concat(a, b) { var u = new Uint8Array(a.length + b.length); u.set(a, 0); u.set(b, a.length); return u; }

  async function _chKey(myPriv, peerRaw, salt) {
    var peer = await subtle.importKey("raw", peerRaw, { name: "ECDH", namedCurve: "P-256" }, false, []);
    var bits = new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: peer }, myPriv, 256));
    var hk = await subtle.importKey("raw", bits, { name: "HKDF" }, false, ["deriveKey"]);
    return subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: salt, info: _CH_INFO },
      hk, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }

  async function genEphemeral() {
    var kp = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var raw = new Uint8Array(await subtle.exportKey("raw", kp.publicKey));
    return { keyPair: kp, pubRawB64: _b64e(raw) };
  }

  async function sealTo(peerPubRawB64, plaintextU8) {
    var peerRaw = _b64d(peerPubRawB64);
    var eph = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    var epk = new Uint8Array(await subtle.exportKey("raw", eph.publicKey));
    var key = await _chKey(eph.privateKey, peerRaw, _concat(peerRaw, epk));
    var iv = _rand(12);
    var ct = new Uint8Array(await subtle.encrypt({ name: "AES-GCM", iv: iv }, key, plaintextU8));
    return { epk: _b64e(epk), iv: _b64e(iv), ct: _b64e(ct) };
  }

  async function openFrom(myEph, sealed) {
    var epk = _b64d(sealed.epk), myPubRaw = _b64d(myEph.pubRawB64);
    var key = await _chKey(myEph.keyPair.privateKey, epk, _concat(myPubRaw, epk));
    var pt = await subtle.decrypt({ name: "AES-GCM", iv: _b64d(sealed.iv) }, key, _b64d(sealed.ct));
    return new Uint8Array(pt);
  }

  async function linkSAS(newPubRawB64, epkRawB64) {
    var pre = _concat(_utf8("brainarbeit-link-sas-v1"), _concat(_b64d(newPubRawB64), _b64d(epkRawB64)));
    var h = new Uint8Array(await subtle.digest("SHA-256", pre));
    var n = ((h[0] << 16) | (h[1] << 8) | h[2]) % 1000000;
    var s = ("000000" + n).slice(-6);
    return s.slice(0, 3) + " " + s.slice(3);
  }

  function serialize(blob) { return _utf8(JSON.stringify(blob)); }
  function deserialize(bytesOrStr) {
    return JSON.parse(typeof bytesOrStr === "string" ? bytesOrStr : _str(bytesOrStr));
  }

  var API = {
    createVault: createVault, unlock: unlock, save: save, addWrap: addWrap,
    generateRecoveryCode: generateRecoveryCode, serialize: serialize, deserialize: deserialize,
    genEphemeral: genEphemeral, sealTo: sealTo, openFrom: openFrom, linkSAS: linkSAS,
    b64e: _b64e, b64d: _b64d, utf8: _utf8, str: _str, ITER: ITER,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  root.VaultCrypto = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
