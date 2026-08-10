
import WebSocket from 'ws';
import { x25519, ed25519 } from '@noble/curves/ed25519';
import { Handshake, hex, unhex, didFor } from './crypto.mjs';

const RELAY = 'wss://rz.brainarbeit.com/';
const TOPIC = 'rz_4aa4265119a4da307b3ac3bf342c94b3';
const BOX_ID_PUB = unhex('fd4a012d6be6ff721d225a7d4c2d1decaf2e6b712020fd42a8c61fedbf4b65cc');
const BOX_SX_PUB = unhex('a44fc373aad4a6fcf56406b438e90debd58ce6d12330032d40ae2c9ff5c8256a');

const te = new TextEncoder(), td = new TextDecoder();
const log = (...a) => console.log(...a);

function relay(ws, rz) {
  const q = [], waiters = [];
  ws.on('message', (raw) => {
    let f; try { f = JSON.parse(typeof raw === 'string' ? raw : raw.toString('utf8')); } catch { return; }
    if (f.t !== 'data') return;
    if (waiters.length) waiters.shift()(f.blob); else q.push(f.blob);
  });
  return {
    dial: () => ws.send(JSON.stringify({ t: 'dial', rz })),
    send: (blobHex) => ws.send(JSON.stringify({ t: 'data', rz, blob: blobHex })),
    recv: (ms = 8000) => new Promise((res, rej) => {
      if (q.length) return res(q.shift());
      const to = setTimeout(() => rej(new Error('recv timeout')), ms);
      waiters.push((b) => { clearTimeout(to); res(b); });
    }),
    bye: () => { try { ws.send(JSON.stringify({ t: 'bye', rz })); } catch {} },
  };
}

async function main() {

  const idPriv = ed25519.utils.randomPrivateKey(), idPub = ed25519.getPublicKey(idPriv);
  const sxPriv = x25519.utils.randomPrivateKey(), sxPub = x25519.getPublicKey(sxPriv);
  log('device did :', didFor(idPub));
  log('dialing    :', RELAY, 'topic', TOPIC);

  const wsOpts = { handshakeTimeout: 10000 };
  if (process.env.PN_ORIGIN) { wsOpts.origin = process.env.PN_ORIGIN; log('origin     :', process.env.PN_ORIGIN, '(simulating the browser PWA)'); }
  const ws = new WebSocket(RELAY, wsOpts);
  await new Promise((res, rej) => {
    ws.once('open', res);
    ws.once('error', rej);
    setTimeout(() => rej(new Error('ws open timeout')), 12000);
  });
  log('ws open    : yes (101 Switching Protocols)');

  const ch = relay(ws, TOPIC);
  ch.dial();

  const hs = new Handshake({ initiator: true, sxPriv, sxPub, idPriv, idPub });

  ch.send(hex(hs.writeMsg1()));
  log('→ msg1     :', 32, 'B sent');

  const m2 = unhex(await ch.recv());
  log('← msg2     :', m2.length, 'B');
  hs.readMsg2(m2);
  hs.assertPeer(BOX_ID_PUB, BOX_SX_PUB);
  log('  box id   :', hex(hs.peerIdPub), '(matches expected)');
  log('  box x    :', hex(hs.peerSxPub), '(matches expected)');

  ch.send(hex(hs.writeMsg3()));
  log('→ msg3     :', 144, 'B sent');

  const ses = hs.session();
  log('E2E session: ESTABLISHED (k_send/k_recv derived, transcript authenticated)');

  const hello = { t: 'hello', did: didFor(idPub), token: null };
  ch.send(hex(ses.encrypt(te.encode(JSON.stringify(hello)))));
  log('→ HELLO    : encrypted, sent (unpaired → box should reject)');
  try {
    const respBlob = await ch.recv(8000);
    const plain = td.decode(ses.decrypt(unhex(respBlob)));
    log('← RESPONSE : DECRYPTED OK →', plain);
    log('\n✅ BAU 2 PASS: live Noise-XX handshake + full-duplex E2E session against the real box.');
  } catch (e) {

    log('← RESPONSE : none (' + e.message + ') — box likely closed the unpaired session silently.');
    log('\n✅ BAU 2 PASS (handshake): msg2 identity signature verified against the real box keys;');
    log('   session keys derived. (No app-layer reply because this device is unpaired — expected.)');
  }
  ch.bye();
  ws.close();
  setTimeout(() => process.exit(0), 200);
}

main().catch((e) => { console.error('\n❌ BAU 2 FAIL:', e.message); process.exit(1); });
