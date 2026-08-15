
from __future__ import annotations
import json, logging

_log = logging.getLogger(__name__)

from relaylib import crypto, protocol as P
from relaylib.box import BoxSession

_PARSE_ERRORS = (crypto.CryptoError, ValueError, UnicodeDecodeError,
                 json.JSONDecodeError, IndexError, TypeError)

def serve_session(ch, appliance_keys, reg_cx, submit_fn, *, bind_identity_fn=None,
                  seen_nonces=None, inflight=None, inflight_count_fn=None, control_fn=None,
                  portal_fn=None, max_messages=1000):

    hs = appliance_keys.handshake()

    try:
        m1 = ch.recv_blob(timeout=10)
        if m1 is None:
            return None
        hs.read_msg1(crypto.from_hex(m1, "handshake msg1"))
        ch.send_blob(hs.write_msg2().hex())
        m3 = ch.recv_blob(timeout=10)
        if m3 is None:
            return None
        hs.read_msg3(crypto.from_hex(m3, "handshake msg3"))
        sess = hs.session()
    except _PARSE_ERRORS:

        return None
    bs = BoxSession(sess, reg_cx, submit_fn, bind_identity_fn=bind_identity_fn,
                    seen_nonces=seen_nonces, inflight=inflight,
                    inflight_count_fn=inflight_count_fn, control_fn=control_fn,
                    portal_fn=portal_fn)
    for _ in range(max_messages):
        blob = ch.recv_blob(timeout=300)
        if blob is None:
            break
        try:
            msg = crypto.loads_json(sess.decrypt(crypto.from_hex(blob, "app frame")), "app message")
        except _PARSE_ERRORS:

            break
        try:
            reply = bs.handle(msg)
        except Exception:

            _log.exception("relaylib.serve: dropping session after unexpected handler error")
            break
        ch.send_blob(sess.encrypt(json.dumps(reply, separators=(",", ":")).encode()).hex())
    return bs
