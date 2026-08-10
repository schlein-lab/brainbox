
from __future__ import annotations
import os, json
from relaylib import crypto, KEYS_DIR

def _load_or_make(cx_dir: str):
    os.makedirs(cx_dir, mode=0o700, exist_ok=True)
    path = os.path.join(cx_dir, "appliance.json")
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        return {k: bytes.fromhex(v) for k, v in d.items()}
    id_priv, id_pub = crypto.gen_ed25519()
    sx_priv, sx_pub = crypto.gen_x25519()
    d = {"id_priv": id_priv.hex(), "id_pub": id_pub.hex(),
         "sx_priv": sx_priv.hex(), "sx_pub": sx_pub.hex()}
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(d, f)
    os.replace(tmp, path)
    return {k: bytes.fromhex(v) for k, v in d.items()}

class ApplianceKeys:
    def __init__(self, keys_dir: str | None = None):
        self.dir = keys_dir or KEYS_DIR
        k = _load_or_make(self.dir)
        self.id_priv, self.id_pub = k["id_priv"], k["id_pub"]
        self.sx_priv, self.sx_pub = k["sx_priv"], k["sx_pub"]

    def handshake(self):

        return crypto.Handshake(initiator=False, static_x_priv=self.sx_priv,
                                static_x_pub=self.sx_pub, id_ed_priv=self.id_priv,
                                id_ed_pub=self.id_pub)
