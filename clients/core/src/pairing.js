
export class PairingError extends Error {
  constructor(msg, { need2fa = false } = {}) { super(msg); this.need2fa = need2fa; }
}

export async function pairViaPortal({ baseUrl, code, totpCode, devicePubkey, label, httpPost }) {
  if (!code) throw new PairingError('pairing requires the one-time code');
  if (!totpCode) throw new PairingError('a second-factor (2FA) code is required', { need2fa: true });
  const body = { code, totp: totpCode, device_pubkey: devicePubkey || null, label: label || null };
  const resp = await httpPost(`${baseUrl}/api/pair`, body);
  if (!resp || resp.ok === false) {
    throw new PairingError(resp?.error || 'pairing rejected',
      { need2fa: !!(resp && (resp.need_2fa || resp.need_step_up_2fa)) });
  }

  return { principal: resp.principal, token: resp.token, did: resp.did, caps: resp.caps };
}

export async function ensureDeviceKey() {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) return null;
  try {
    const kp = await subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
    const pub = await subtle.exportKey('raw', kp.publicKey);
    return { keypair: kp, pubkeyHex: [...new Uint8Array(pub)].map(b => b.toString(16).padStart(2, '0')).join('') };
  } catch {
    try {
      const kp = await subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);
      const pub = await subtle.exportKey('raw', kp.publicKey);
      return { keypair: kp, pubkeyHex: [...new Uint8Array(pub)].map(b => b.toString(16).padStart(2, '0')).join(''), alg: 'ECDSA-P256' };
    } catch { return null; }
  }
}
