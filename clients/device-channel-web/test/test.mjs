import { readFileSync } from 'fs';
import { Handshake, Session, hex, unhex, cat, didFor, rendezvousTopic, b2s, hkdf } from './crypto.mjs';
const v = JSON.parse(readFileSync(new URL('./vectors.json', import.meta.url), 'utf8'));
const te = new TextEncoder();
let pass = 0, fail = 0;
const check = (name, got, want) => {
  if (got === want) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('  FAIL ' + name + '\n    got  ' + got + '\n    want ' + want); }
};

check('blake2s_empty', hex(b2s(new Uint8Array(0))), v.primitives.blake2s_empty);
check('blake2s_hello', hex(b2s(te.encode('hello'))), v.primitives.blake2s_hello);
check('did_for', didFor(unhex(v.inputs.dv_id_pub)), v.primitives.did_for_dv_id_pub);
check('rendezvous_topic', rendezvousTopic(unhex(v.inputs.bx_sx_pub)), v.primitives.rendezvous_topic_bx_sx_pub);
const hk = hkdf(new Uint8Array(32).fill(1), new Uint8Array(16).fill(2), 2);
check('hkdf[0]', hex(hk[0]), v.primitives.hkdf_chain01_ikm02_n2[0]);
check('hkdf[1]', hex(hk[1]), v.primitives.hkdf_chain01_ikm02_n2[1]);

const dev = new Handshake({
  initiator: true, sxPriv: unhex(v.inputs.dv_sx_priv), sxPub: unhex(v.inputs.dv_sx_pub),
  idPriv: unhex(v.inputs.dv_id_seed), idPub: unhex(v.inputs.dv_id_pub),
});
dev.setEphemeral(unhex(v.inputs.dv_ex_priv));
check('msg1', hex(dev.writeMsg1()), v.handshake.msg1);
dev.readMsg2(unhex(v.handshake.msg2));
check('peer_id_pub', hex(dev.peerIdPub), v.inputs.bx_id_pub);
check('peer_sx_pub', hex(dev.peerSxPub), v.inputs.bx_sx_pub);
check('msg3', hex(dev.writeMsg3()), v.handshake.msg3);
const ses = dev.session();
check('session.k_send', hex(ses.kSend), v.dev_session.k_send);
check('session.k_recv', hex(ses.kRecv), v.dev_session.k_recv);
check('session.h', hex(ses.h), v.dev_session.h);
check('app_ct (send#0)', hex(ses.encrypt(te.encode(v.app.pt))), v.app.ct_dev_send0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
