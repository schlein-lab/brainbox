
from __future__ import annotations
import secrets, time

from relaylib import crypto, protocol as P, totp
from relaylib.transport import Channel

class Device:
    def __init__(self, box_id_pub: bytes, box_x_pub: bytes,
                 id_priv=None, id_pub=None, sx_priv=None, sx_pub=None,
                 totp_secret: str | None = None):
        self.box_id_pub = box_id_pub
        self.box_x_pub = box_x_pub
        if id_priv is None:
            id_priv, id_pub = crypto.gen_ed25519()
        if sx_priv is None:
            sx_priv, sx_pub = crypto.gen_x25519()
        self.id_priv, self.id_pub = id_priv, id_pub
        self.sx_priv, self.sx_pub = sx_priv, sx_pub
        self.did = crypto.did_for(self.id_pub)
        self.sess = None
        self.token = None
        self.session_token = None
        self.counter = 0

        self.totp_secret = totp_secret

    def rendezvous_topic(self) -> str:
        return crypto.rendezvous_topic(self.box_x_pub)

    def connect(self, url: str):

        rz = self.rendezvous_topic()
        ch = Channel.dial(url, rz)
        hs = crypto.Handshake(initiator=True, static_x_priv=self.sx_priv,
                              static_x_pub=self.sx_pub, id_ed_priv=self.id_priv,
                              id_ed_pub=self.id_pub)
        ch.send_blob(hs.write_msg1().hex())
        m2 = ch.recv_blob(timeout=5)
        hs.read_msg2(bytes.fromhex(m2))

        if hs.peer_id_pub != self.box_id_pub or hs.peer_sx_pub != self.box_x_pub:
            ch.close()
            raise crypto.CryptoError("box identity does not match the expected appliance keys")
        ch.send_blob(hs.write_msg3().hex())
        self.sess = hs.session()
        self._ch = ch
        return ch

    def _rpc(self, obj: dict) -> dict:
        self._ch.send_blob(self.sess.encrypt(_jdump(obj)).hex())
        blob = self._ch.recv_blob(timeout=10)
        if blob is None:
            raise crypto.CryptoError("relay closed the channel")
        resp = _jload(self.sess.decrypt(bytes.fromhex(blob)))

        if isinstance(resp, dict) and resp.get("session_token"):
            self.session_token = resp["session_token"]
        return resp

    def _totp_now(self, override: str | None = None, *, step_offset: int = 0) -> str | None:
        if override is not None:
            return override
        if self.totp_secret:

            return totp.code_at(self.totp_secret, time.time() + step_offset * totp.STEP_S)
        return None

    def pair(self, code: str, label: str | None = None, *, totp_code: str | None = None) -> dict:
        proof = crypto.ed_sign(self.id_priv, self.sess.h)
        resp = self._rpc({"t": P.PAIR_REQUEST, "code": code,
                          "device_id_pubkey": self.id_pub.hex(), "proof": proof.hex(),
                          "totp": self._totp_now(totp_code), "label": label})
        if resp.get("t") == P.PAIR_OK:
            self.token = resp["token"]
        return resp

    def hello(self) -> dict:
        return self._rpc({"t": P.HELLO, "did": self.did, "token": self.token})

    def submit(self, *, task_type=None, params=None, cmd=None, cls="worker",
               sign=True, tamper=False, session_token=None, counter=None, nonce=None,
               step_up_2fa=None, step_up_auto=False, step_offset=1) -> dict:

        if counter is None:
            self.counter += 1
            ctr = self.counter
        else:
            ctr = counter
        st = self.session_token if session_token is None else session_token
        payload = P.submit_payload(task_type=task_type, params=params, cmd=cmd, cls=cls,
                                   nonce=nonce or secrets.token_hex(8), ts=time.time(),
                                   counter=ctr, session_token=st)
        su = (self._totp_now(step_up_2fa, step_offset=step_offset)
              if (step_up_auto or step_up_2fa is not None) else None)
        if su is not None:
            payload["step_up_2fa"] = su
        if sign:
            sig = crypto.ed_sign(self.id_priv, P.signing_bytes(payload))
            if tamper:

                payload["params"] = {**(params or {}), "msg": "TAMPERED"}
            payload["sig"] = sig.hex()
        return self._rpc(payload)

    def close(self):
        try:
            self._ch.close()
        except Exception:
            pass

def _jdump(obj) -> bytes:
    import json
    return json.dumps(obj, separators=(",", ":")).encode()

def _jload(b: bytes) -> dict:
    import json
    return json.loads(b.decode())
