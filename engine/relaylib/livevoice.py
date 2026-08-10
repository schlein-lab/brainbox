
import os, json, time, hmac, hashlib, struct, base64
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from relaylib import crypto

INFO = b"brainarbeit/livevoice/v1"

MIC = "mic"
STT = "stt"
TTS_REQ = "tts_req"
TTS = "tts"
PING = "ping"; PONG = "pong"; BYE = "bye"

def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()

def _hkdf(ikm: bytes, salt: bytes, info: bytes, n: int = 32) -> bytes:
    prk = hmac.new(salt or b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm = b""; t = b""; i = 1
    while len(okm) < n:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest(); okm += t; i += 1
    return okm[:n]

def lane_keys(my_x_priv_hex: str, peer_x_pub_hex: str, lane_id: str):

    ss = crypto._ecdh(bytes.fromhex(my_x_priv_hex), bytes.fromhex(peer_x_pub_hex))
    base = _hkdf(ss, lane_id.encode(), INFO, 32)
    return (_hkdf(base, b"c2b", INFO, 32), _hkdf(base, b"b2c", INFO, 32))

def seal(key: bytes, seq: int, frame: dict) -> str:
    nonce = b"\x00\x00\x00\x00" + struct.pack(">Q", seq)
    ct = ChaCha20Poly1305(key).encrypt(nonce, _canon(frame), None)
    return (nonce + ct).hex()

def open_blob(key: bytes, blob_hex: str, last_seq: int):
    b = bytes.fromhex(blob_hex)
    nonce, ct = b[:12], b[12:]
    seq = struct.unpack(">Q", nonce[4:12])[0]
    if seq <= last_seq:
        raise ValueError("replay/reorder: seq %d <= %d" % (seq, last_seq))
    pt = ChaCha20Poly1305(key).decrypt(nonce, ct, None)
    return json.loads(pt), seq

class Lane:

    def __init__(self, chan, my_x_priv_hex, peer_x_pub_hex, lane_id, role):
        c2b, b2c = lane_keys(my_x_priv_hex, peer_x_pub_hex, lane_id)
        if role == "client":
            self.send_key, self.recv_key = c2b, b2c
        elif role == "box":
            self.send_key, self.recv_key = b2c, c2b
        else:
            raise ValueError("role must be 'box' or 'client'")
        self.chan = chan; self._sseq = 0; self._rseq = -1

    def send(self, frame: dict):
        self._sseq += 1
        self.chan.send_blob(seal(self.send_key, self._sseq, frame))

    def recv(self, timeout=None):
        blob = self.chan.recv_blob(timeout=timeout)
        if not blob:
            return None
        frame, seq = open_blob(self.recv_key, blob, self._rseq)
        self._rseq = seq
        return frame

    def bye(self):
        try: self.send({"t": BYE})
        except Exception: pass

def mint_ticket(box_hmac_secret: bytes, *, principal: str, device_did: str, relay_url: str,
                ttl_s: int = 120) -> dict:
    body = {"rz": os.urandom(16).hex(), "lane_id": os.urandom(16).hex(),
            "principal": principal, "did": device_did, "relay_url": relay_url,
            "exp": int(time.time()) + ttl_s}
    body["mac"] = hmac.new(box_hmac_secret, _canon({k: body[k] for k in
                  ("rz", "lane_id", "principal", "did", "relay_url", "exp")}),
                  hashlib.sha256).hexdigest()
    return body

def verify_ticket(box_hmac_secret: bytes, ticket: dict):
    try:
        body = {k: ticket[k] for k in ("rz", "lane_id", "principal", "did", "relay_url", "exp")}
    except KeyError as e:
        return (False, "malformed ticket (%s)" % e)
    mac = hmac.new(box_hmac_secret, _canon(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, ticket.get("mac", "")):
        return (False, "bad ticket signature")
    if time.time() > body["exp"]:
        return (False, "ticket expired")
    return (True, body)

def serve_voice(lane: Lane, stt_fn, tts_fn, *, should_stop=None, idle_timeout=120.0):

    stats = {"mic": 0, "stt": 0, "tts_req": 0, "tts_chunks": 0}
    last = time.time()
    while True:
        if should_stop and should_stop():
            break
        fr = lane.recv(timeout=1.0)
        if fr is None:
            if time.time() - last > idle_timeout:
                break
            continue
        last = time.time()
        t = fr.get("t")
        if t == MIC:
            stats["mic"] += 1
            text, final = stt_fn(fr)
            if text is not None:
                lane.send({"t": STT, "ref": fr.get("seq"), "text": text, "final": bool(final)})
                stats["stt"] += 1
        elif t == TTS_REQ:
            stats["tts_req"] += 1
            for chunk in tts_fn(fr.get("text", "")):
                lane.send({"t": TTS, "wav": base64.b64encode(chunk).decode(), "last": False})
                stats["tts_chunks"] += 1
            lane.send({"t": TTS, "wav": "", "last": True})
        elif t == PING:
            lane.send({"t": PONG, "ts": fr.get("ts")})
        elif t == BYE:
            break
    return stats
