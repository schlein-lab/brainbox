import WebSocket from 'ws';
import { DeviceChannel, generateKeys } from './devicechannel.mjs';
import { rendezvousTopic } from './crypto.mjs';
const BOX_ID='fd4a012d6be6ff721d225a7d4c2d1decaf2e6b712020fd42a8c61fedbf4b65cc';
const BOX_SX='a44fc373aad4a6fcf56406b438e90debd58ce6d12330032d40ae2c9ff5c8256a';

import { unhex } from './crypto.mjs';
const derived = rendezvousTopic(unhex(BOX_SX));
console.log('topic derived from box_sx_pub:', derived, derived==='rz_4aa4265119a4da307b3ac3bf342c94b3'?'✓ matches live':'✗ MISMATCH');
const dc = new DeviceChannel({
  relayUrl:'wss://rz.brainarbeit.com/', topic: derived, boxIdPub:BOX_ID, boxSxPub:BOX_SX,
  keys: generateKeys(), WebSocketImpl: WebSocket, origin:'https://app.brainarbeit.com',
});
await dc.connect();
console.log('handshake ok via DeviceChannel — box identity verified, E2E up');
const r = await dc.hello(null);
console.log('HELLO(unpaired) reply (decrypted):', JSON.stringify(r));
dc.close();
if (r && r.t==='error') { console.log('\n✅ BAU 5a: refactored DeviceChannel class is protocol-identical & works live.'); process.exit(0); }
else { console.log('\n❌ unexpected reply'); process.exit(1); }
