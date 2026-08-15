
from __future__ import annotations
import json, os, tempfile, threading

import relaylib
from relaylib import registry, protocol as P, crypto, ID_METHOD
from relaylib.keys import ApplianceKeys
from relaylib.box import BoxSession
from relaylib.transport import MockRelay, Channel

class MockBox:
    def __init__(self, pnd, *, state_dir=None):
        self.pnd = pnd
        self.state_dir = state_dir or tempfile.mkdtemp(prefix="mockbox-")

        os.environ["XDG_DATA_HOME"] = self.state_dir
        self._reset_relaylib_paths()
        self.keys = ApplianceKeys()
        self.reg = registry.connect()
        self.relay = MockRelay()
        self._serving = False
        self._seen_nonces = set()

        self._did_principal = {}

    def _reset_relaylib_paths(self):

        import importlib
        importlib.reload(relaylib)
        from relaylib import keys as _k
        importlib.reload(_k)

    @property
    def relay_url(self):
        return self.relay.url

    @property
    def appliance_id_pub_hex(self):
        return self.keys.id_pub.hex()

    @property
    def appliance_x_pub_hex(self):
        return self.keys.sx_pub.hex()

    def arm_2fa(self, principal) -> str:

        return registry.arm_2fa(self.reg, principal)

    def mint_pairing(self, principal, caps, *, label=None, ttl_s=900) -> str:
        return registry.mint_pairing(self.reg, principal, set(caps), parent_principal=principal,
                                     label=label, ttl_s=ttl_s)

    def revoke(self, did) -> bool:
        return registry.revoke(self.reg, did)

    def _submit_fn(self, req: dict) -> dict:

        did = req.get("_selector")
        principal = self._did_principal.get(did) or self._principal_of_did(did)
        resp = self.pnd.handle({k: v for k, v in req.items()
                                if k in ("verb", "task_type", "params", "cmd", "class",
                                         "via_device", "source")} | {"verb": "submit"},
                               principal=principal)
        return resp

    def _control_fn(self, req: dict) -> dict:

        did = req.get("_selector")
        principal = self._did_principal.get(did) or self._principal_of_did(did)

        return self.pnd.handle(req, principal=principal, via_device=did,
                               ceiling_caps=req.get("_ceiling_caps"), is_broker=True)

    def _inflight_count_fn(self, did) -> int:

        principal = self._did_principal.get(did) or self._principal_of_did(did)
        return self.pnd.count_inflight_via_device(principal, did)

    def _principal_of_did(self, did) -> str:
        al = registry.get_alliance(self.reg, did)
        return al["principal"] if al else "lan-guest"

    def _bind_identity(self, method, selector, principal, verified):

        self._did_principal[selector] = principal

    def serve_forever(self):
        self._serving = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def _accept_loop(self):
        rz = crypto.rendezvous_topic(self.keys.sx_pub)
        while self._serving:
            try:
                ch = Channel.register(self.relay_url, rz, timeout=5)
            except OSError:
                break
            threading.Thread(target=self._serve_one, args=(ch,), daemon=True).start()

    def _serve_one(self, ch):

        try:
            hs = self.keys.handshake()
            m1 = ch.recv_blob(timeout=10)
            if m1 is None:
                return
            hs.read_msg1(bytes.fromhex(m1))
            ch.send_blob(hs.write_msg2().hex())
            m3 = ch.recv_blob(timeout=10)
            if m3 is None:
                return
            hs.read_msg3(bytes.fromhex(m3))
            sess = hs.session()
            bs = BoxSession(sess, self.reg, self._submit_fn,
                            bind_identity_fn=self._bind_identity, seen_nonces=self._seen_nonces,
                            control_fn=self._control_fn,
                            inflight_count_fn=self._inflight_count_fn)
            while True:
                blob = ch.recv_blob(timeout=120)
                if blob is None:
                    break
                try:
                    msg = json.loads(sess.decrypt(bytes.fromhex(blob)).decode())
                except crypto.CryptoError:
                    break
                reply = bs.handle(msg)

                if reply.get("t") == P.PAIR_OK:
                    self._did_principal[reply["did"]] = reply["principal"]

                ch.send_blob(sess.encrypt(json.dumps(reply, separators=(",", ":")).encode()).hex())
        except Exception:
            pass
        finally:
            try:
                ch.close()
            except Exception:
                pass

    def stop(self):
        self._serving = False
        self.relay.stop()
