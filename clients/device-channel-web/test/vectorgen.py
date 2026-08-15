#!/usr/bin/env python3

import json, os, sys, hashlib
sys.path.insert(0, os.path.expanduser("~/brainarbeit/engine"))
from relaylib import crypto
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as ser

def xpub(p):
    return X25519PrivateKey.from_private_bytes(p).public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw)
def edpub(s):
    return Ed25519PrivateKey.from_private_bytes(s).public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw)

dv_id = bytes(range(0, 32)); dv_sx = bytes(range(32, 64)); dv_ex = bytes(range(64, 96))
bx_id = bytes(range(96, 128)); bx_sx = bytes(range(128, 160)); bx_ex = bytes(range(160, 192))
dv_id_pub = edpub(dv_id); dv_sx_pub = xpub(dv_sx)
bx_id_pub = edpub(bx_id); bx_sx_pub = xpub(bx_sx)

vec = {}
vec["primitives"] = {
    "blake2s_empty": hashlib.blake2s(b"").hexdigest(),
    "blake2s_hello": hashlib.blake2s(b"hello").hexdigest(),
    "did_for_dv_id_pub": crypto.did_for(dv_id_pub),
    "rendezvous_topic_bx_sx_pub": crypto.rendezvous_topic(bx_sx_pub),
    "hkdf_chain01_ikm02_n2": [x.hex() for x in crypto._hkdf(b"\x01" * 32, b"\x02" * 16, 2)],
}
dev = crypto.Handshake(initiator=True, static_x_priv=dv_sx, static_x_pub=dv_sx_pub,
                       id_ed_priv=dv_id, id_ed_pub=dv_id_pub)
dev.ex_priv, dev.ex_pub = dv_ex, xpub(dv_ex)
box = crypto.Handshake(initiator=False, static_x_priv=bx_sx, static_x_pub=bx_sx_pub,
                       id_ed_priv=bx_id, id_ed_pub=bx_id_pub)
box.ex_priv, box.ex_pub = bx_ex, xpub(bx_ex)

m1 = dev.write_msg1(); box.read_msg1(m1)
m2 = box.write_msg2(); dev.read_msg2(m2)
m3 = dev.write_msg3(); box.read_msg3(m3)
ds = dev.session(); bs = box.session()

vec["inputs"] = {
    "dv_id_seed": dv_id.hex(), "dv_id_pub": dv_id_pub.hex(),
    "dv_sx_priv": dv_sx.hex(), "dv_sx_pub": dv_sx_pub.hex(),
    "dv_ex_priv": dv_ex.hex(), "dv_ex_pub": xpub(dv_ex).hex(),
    "bx_id_pub": bx_id_pub.hex(), "bx_sx_pub": bx_sx_pub.hex(),
}
vec["handshake"] = {"msg1": m1.hex(), "msg2": m2.hex(), "msg3": m3.hex()}
vec["dev_session"] = {"k_send": ds.k_send.hex(), "k_recv": ds.k_recv.hex(), "h": ds.h.hex()}
pt = b'{"t":"hello","token":"abc"}'
ct = ds.encrypt(pt)
vec["app"] = {"pt": pt.decode(), "ct_dev_send0": ct.hex(),
              "roundtrip_box_recv": bs.decrypt(ct).decode()}
print(json.dumps(vec, indent=2))
