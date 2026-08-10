
import WebSocket from 'ws';
import { x25519, ed25519 } from '@noble/curves/ed25519';
import { Handshake, hex, unhex, didFor } from './crypto.mjs';

const RELAY = 'wss://rz.brainarbeit.com/';
const TOPIC = 'rz_4aa4265119a4da307b3ac3bf342c94b3';
const BOX_ID_PUB = unhex('fd4a012d6be6ff721d225a7d4c2d1decaf2e6b712020fd42a8c61fedbf4b65cc');
const BOX_SX_PUB = unhex('a44fc373aad4a6fcf56406b438e90debd58ce6d12330032d40ae2c9ff5c8256a');
const PAIR_CODE = process.env.PAIR_CODE, PAIR_TOTP = process.env.PAIR_TOTP;
const ORIGIN = process.env.PN_ORIGIN || 'https://app.brainarbeit.com';

const te = new TextEncoder(), td = new TextDecoder();
const log = (...a) => console.log(...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const idPriv = ed25519.utils.randomPrivateKey(), idPub = ed25519.getPublicKey(idPriv);
const sxPriv = x25519.utils.randomPrivateKey(), sxPub = x25519.getPublicKey(sxPriv);
const DID = didFor(idPub);

function mailbox(ws) {
  const q = [], waiters = [];
  ws.on('message', (raw) => {
    let f; try { f = JSON.parse(typeof raw === 'string' ? raw : raw.toString('utf8')); } catch { return; }
    if (f.t !== 'data') return;
    if (waiters.length) waiters.shift()(f.blob); else q.push(f.blob);
  });
  return (ms = 8000) => new Promise((res, rej) => {
    if (q.length) return res(q.shift());
    const to = setTimeout(() => rej(new Error('recv timeout')), ms);
    waiters.push((b) => { clearTimeout(to); res(b); });
  });
}

async function connect(tag) {
  const ws = new WebSocket(RELAY, { handshakeTimeout: 10000, origin: ORIGIN });
  await new Promise((res, rej) => { ws.once('open', res); ws.once('error', rej); setTimeout(() => rej(new Error('ws open timeout')), 12000); });
  const recv = mailbox(ws);
  const send = (blobHex) => ws.send(JSON.stringify({ t: 'data', rz: TOPIC, blob: blobHex }));
  ws.send(JSON.stringify({ t: 'dial', rz: TOPIC }));
  const hs = new Handshake({ initiator: true, sxPriv, sxPub, idPriv, idPub });
  send(hex(hs.writeMsg1()));
  hs.readMsg2(unhex(await recv()));
  hs.assertPeer(BOX_ID_PUB, BOX_SX_PUB);
  send(hex(hs.writeMsg3()));
  const ses = hs.session();
  log(`[${tag}] handshake ok — box identity verified, E2E session up`);
  const rpc = async (obj) => {
    send(hex(ses.encrypt(te.encode(JSON.stringify(obj)))));
    return JSON.parse(td.decode(ses.decrypt(unhex(await recv()))));
  };
  return { ws, ses, rpc, close: () => { try { ws.send(JSON.stringify({ t: 'bye', rz: TOPIC })); } catch {} ws.close(); } };
}

async function main() {
  if (!PAIR_CODE || !PAIR_TOTP) throw new Error('need PAIR_CODE + PAIR_TOTP env');
  log('device did :', DID);
  log('origin     :', ORIGIN, '\n');

  const a = await connect('pair');
  const redact = (o) => JSON.stringify(o, (k, v) => (k === 'token' || k === 'session_token') && typeof v === 'string' ? v.slice(0, 6) + '…' : v);
  const proof = ed25519.sign(a.ses.h, idPriv);
  const pairResp = await a.rpc({
    t: 'pair_request', code: PAIR_CODE, device_id_pubkey: hex(idPub),
    proof: hex(proof), totp: PAIR_TOTP, label: 'BAU4-selftest',
  });
  log('← PAIR reply:', redact(pairResp));
  if (pairResp.t !== 'pair_ok') { a.close(); throw new Error('pairing failed: ' + JSON.stringify(pairResp)); }
  const token = pairResp.token, principal = pairResp.principal;
  log(`  paired → principal=${principal} caps=${JSON.stringify(pairResp.caps)} token=${token.slice(0, 8)}… session_token=${pairResp.session_token.slice(0, 8)}…`);
  a.close();

  await sleep(1500);

  const b = await connect('reconnect');
  const helloResp = await b.rpc({ t: 'hello', did: DID, token });
  log('← HELLO ok :', JSON.stringify(helloResp));
  b.close();
  if (helloResp.t !== 'hello_ok') throw new Error('HELLO failed: ' + JSON.stringify(helloResp));

  log('\n✅ BAU 4 PASS: pair-over-relay minted a durable token; a fresh-handshake reconnect with the');
  log('   SAME device key was accepted (HELLO_OK, new rotating session token). Off-LAN reconnect works.');
  setTimeout(() => process.exit(0), 200);
}
main().catch((e) => { console.error('\n❌ BAU 4 FAIL:', e.message); process.exit(1); });
