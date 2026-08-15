
from __future__ import annotations
import os, json, base64, tempfile

def _crypto():
    from relaylib import crypto
    return crypto

def default_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "brainarbeit", "connect")

class Keystore:

    def __init__(self, path: str | None = None, backend: str = "file"):
        self.dir = path or default_dir()
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.path = os.path.join(self.dir, "alliances.json")
        self.backend = backend
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path) as f:
                return json.load(f)
        return {"alliances": {}, "lan": {}}

    def _save(self):
        fd, tmp = tempfile.mkstemp(dir=self.dir)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def device_identity(self, box_label: str) -> dict:

        al = self._data["alliances"].get(box_label)
        if al and al.get("id_priv"):
            return al
        crypto = _crypto()
        id_priv, id_pub = crypto.gen_ed25519()
        sx_priv, sx_pub = crypto.gen_x25519()
        rec = self._data["alliances"].setdefault(box_label, {})
        rec.update({
            "box_label": box_label,
            "id_priv": id_priv.hex(), "id_pub": id_pub.hex(),
            "sx_priv": sx_priv.hex(), "sx_pub": sx_pub.hex(),
        })
        self._save()
        return rec

    def save_box_keys(self, box_label, *, relay_url, appliance_id_pubkey, appliance_x_pubkey,
                      rendezvous_topic=None, principal=None):

        rec = self._data["alliances"].setdefault(box_label, {"box_label": box_label})
        rec.update({"relay_url": relay_url,
                    "appliance_id_pubkey": appliance_id_pubkey,
                    "appliance_x_pubkey": appliance_x_pubkey})
        if rendezvous_topic:
            rec["rendezvous_topic"] = rendezvous_topic
        if principal:
            rec["principal"] = principal
        self._save()
        return rec

    def save_alliance(self, box_label, *, principal, token, did=None, caps=None):

        rec = self._data["alliances"].setdefault(box_label, {"box_label": box_label})
        rec.update({"principal": principal, "token": token, "paired_at": _now()})
        if did:
            rec["did"] = did
        if caps is not None:
            rec["caps"] = caps
        self._save()
        return rec

    def alliance(self, box_label) -> dict | None:
        return self._data["alliances"].get(box_label)

    def is_paired(self, box_label) -> bool:
        al = self._data["alliances"].get(box_label)
        return bool(al and al.get("token"))

    def forget(self, box_label) -> bool:

        return self._data["alliances"].pop(box_label, None) is not None

    def list_alliances(self) -> list[dict]:
        out = []
        for al in self._data["alliances"].values():
            out.append({"box_label": al.get("box_label"), "principal": al.get("principal"),
                        "did": al.get("did"), "paired": bool(al.get("token")),
                        "relay_url": al.get("relay_url"), "paired_at": al.get("paired_at")})
        return out

    def save_lan(self, box_label, *, principal, sock):
        self._data["lan"][box_label] = {"box_label": box_label, "principal": principal, "sock": sock}
        self._save()

    def lan(self, box_label):
        return self._data["lan"].get(box_label)

def _now():
    import time
    return time.time()

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()
