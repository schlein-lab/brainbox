
from __future__ import annotations
import json, time
from collections import OrderedDict

from relaylib import crypto, registry, protocol as P, ID_METHOD
from relaylib import audit as _audit

class BoxError(Exception):
    pass

MAX_NONCE_LEN = 64

NONCE_WINDOW_S = 300.0

class NonceCache:

    def __init__(self, *, window_s: float = NONCE_WINDOW_S, per_device_maxlen: int = 4096):
        self._window = window_s
        self._maxlen = max(1, int(per_device_maxlen))

        self._buckets: dict[str, "OrderedDict[str, float]"] = {}

    @staticmethod
    def valid_nonce(nonce) -> bool:

        return isinstance(nonce, str) and 0 < len(nonce) <= MAX_NONCE_LEN

    def _evict_stale(self, bucket: "OrderedDict[str, float]", now: float):

        while bucket:
            k, t = next(iter(bucket.items()))
            if now - t > self._window:
                bucket.popitem(last=False)
            else:
                break

    def seen(self, did: str, nonce: str, *, now: float | None = None) -> bool:

        now = time.time() if now is None else now
        bucket = self._buckets.get(did)
        if not bucket:
            return False
        self._evict_stale(bucket, now)
        return nonce in bucket

    def add(self, did: str, nonce: str, *, now: float | None = None):

        now = time.time() if now is None else now
        bucket = self._buckets.get(did)
        if bucket is None:
            bucket = self._buckets[did] = OrderedDict()
        self._evict_stale(bucket, now)
        bucket[nonce] = now
        bucket.move_to_end(nonce)
        while len(bucket) > self._maxlen:
            bucket.popitem(last=False)

    def drop_device(self, did: str):

        self._buckets.pop(did, None)

    def total(self) -> int:
        return sum(len(b) for b in self._buckets.values())

INFLIGHT_TTL_S = 900.0

class InFlightCounter:

    def __init__(self, *, ttl_s: float = INFLIGHT_TTL_S):
        self._ttl = ttl_s

        self._jobs: dict[str, dict] = {}

    def _evict(self, did: str, now: float):
        d = self._jobs.get(did)
        if not d:
            return
        for k in [k for k, t in d.items() if now - t > self._ttl]:
            d.pop(k, None)
        if not d:
            self._jobs.pop(did, None)

    def count(self, did: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        self._evict(did, now)
        return len(self._jobs.get(did, {}))

    def add(self, did: str, job_key, *, now: float | None = None):
        now = time.time() if now is None else now
        self._evict(did, now)
        self._jobs.setdefault(did, {})[job_key if job_key is not None else now] = now

    def remove(self, did: str, job_key):
        d = self._jobs.get(did)
        if d is not None:
            d.pop(job_key, None)
            if not d:
                self._jobs.pop(did, None)

    def drop_device(self, did: str):
        self._jobs.pop(did, None)

def _as_inflight(inflight) -> InFlightCounter:
    if isinstance(inflight, InFlightCounter):
        return inflight
    return InFlightCounter()

def _as_nonce_cache(seen_nonces) -> NonceCache:

    if isinstance(seen_nonces, NonceCache):
        return seen_nonces
    return NonceCache()

def _stepup_task_types() -> set:
    import os
    raw = os.environ.get("RELAY_STEPUP_TASK_TYPES", "")
    return {t.strip() for t in raw.replace(",", " ").split() if t.strip()}

CONTROL_TASK_TYPE = "relay.control"

CONTROL_READ_VERBS = frozenset({"cvm", "events", "replay", "job", "pending", "my-jobs"})
CONTROL_DECISION_VERBS = frozenset({"approve", "deny", "reject", "revise", "steer"})

CONTROL_PORTAL_READ = frozenset({"portal.sessions", "portal.transcript", "portal.stt", "portal.voicetail", "portal.tts", "portal.voicewarm", "portal.sessmeta", "portal.pushkey", "portal.pushsub", "portal.pushunsub", "portal.sessinfo", "portal.decisions", "portal.files", "portal.file", "portal.sesswarm"})
CONTROL_PORTAL_WRITE = frozenset({"portal.say", "portal.voiceturn", "portal.newsession", "portal.archive", "portal.rename", "portal.provision", "portal.decide", "portal.dismiss"})
_PORTAL_OP = {"portal.sessions": "sessions.list", "portal.transcript": "session.transcript",
              "portal.say": "session.say", "portal.stt": "stt",
              "portal.voiceturn": "voice.turn", "portal.voicetail": "voice.tail", "portal.tts": "tts", "portal.voicewarm": "voice.warm",
              "portal.newsession": "session.new", "portal.archive": "session.archive", "portal.rename": "session.rename",
              "portal.sessmeta": "session.meta", "portal.provision": "session.provision",
              "portal.pushkey": "push.pubkey", "portal.pushsub": "push.subscribe", "portal.pushunsub": "push.unsubscribe", "portal.sessinfo": "session.info",
              "portal.decisions": "decisions.list", "portal.decide": "decisions.decide", "portal.dismiss": "decisions.dismiss",
              "portal.files": "files.list", "portal.file": "files.get", "portal.sesswarm": "session.warm"}

def _stepup_control_verbs() -> set:
    import os
    raw = os.environ.get("RELAY_STEPUP_CONTROL_VERBS", "")
    extra = {v.strip() for v in raw.replace(",", " ").split() if v.strip()}
    return {"approve", "deny", "reject"} | extra

class BoxSession:

    def __init__(self, sess: "crypto.Session", reg_cx, submit_fn, *, bind_identity_fn=None,
                 seen_nonces=None, inflight=None, inflight_count_fn=None, control_fn=None,
                 portal_fn=None):
        self.sess = sess
        self.reg = reg_cx
        self.submit_fn = submit_fn

        self.bind_identity_fn = bind_identity_fn

        self.control_fn = control_fn or submit_fn

        self.portal_fn = portal_fn
        self.device_did = None
        self.alliance = None

        self._seen_nonces = _as_nonce_cache(seen_nonces)

        self._inflight = _as_inflight(inflight)

        self._inflight_count_fn = inflight_count_fn

    @property
    def device_x_pub(self) -> bytes:
        return self.sess.peer_static_pub

    @property
    def transcript_hex(self) -> str:
        return self.sess.h.hex()

    def _audit(self, event, **kw):
        try:
            _audit.record(self.reg, event, **kw)
        except Exception:
            pass

    def handle(self, msg: dict) -> dict:

        t = msg.get("t")
        if t == P.PAIR_REQUEST:
            return self._pair(msg)
        if t == P.HELLO:
            return self._hello(msg)
        if t == P.SUBMIT:
            return self._submit(msg)
        return {"t": P.ERROR, "error": f"unknown message type {t!r}"}

    def _pair(self, msg) -> dict:
        code = msg.get("code")
        id_pub_hex = msg.get("device_id_pubkey")
        sig_hex = msg.get("proof")
        totp_code = msg.get("totp")
        label = msg.get("label")
        if not (code and id_pub_hex and sig_hex):
            return {"t": P.ERROR, "error": "pair_request requires code, device_id_pubkey, proof"}
        id_pub = bytes.fromhex(id_pub_hex)

        if not crypto.ed_verify(id_pub, bytes.fromhex(sig_hex), self.sess.h):
            self._audit("pair.pop_fail")
            return {"t": P.ERROR, "error": "device identity proof-of-possession invalid"}

        pr = registry.redeem_pairing(self.reg, code, totp_code=totp_code)
        if not pr:

            if not totp_code:
                return {"t": P.ERROR, "error": "pairing requires a second-factor (2FA) code",
                        "need_2fa": True}
            return {"t": P.ERROR,
                    "error": "pairing code unknown/expired/used or second factor rejected"}
        did = crypto.did_for(id_pub)
        caps = json.loads(pr["caps"])
        token = registry.create_alliance(
            self.reg, device_did=did, device_pubkey_hex=id_pub_hex,
            device_x_pubkey_hex=self.device_x_pub.hex(), principal=pr["principal"],
            parent_principal=pr["parent_principal"], caps=caps,
            label=label or pr["label"], max_rate=pr["max_rate"],
            max_concurrency=pr["max_concurrency"])
        if self.bind_identity_fn:
            self.bind_identity_fn(ID_METHOD, did, pr["principal"], 1)
        self.device_did = did
        self.alliance = registry.get_alliance(self.reg, did)

        st = registry.issue_session_token(self.reg, did, self.transcript_hex)
        self._audit("pair.ok", device_did=did, principal=pr["principal"])
        return {"t": P.PAIR_OK, "did": did, "token": token, "principal": pr["principal"],
                "caps": sorted(caps), "session_token": st}

    def _hello(self, msg) -> dict:
        did = msg.get("did")
        token = msg.get("token")
        if not (did and token):
            return {"t": P.ERROR, "error": "hello requires did + token"}
        al = registry.alliance_for_token(self.reg, did, token)
        if not al:
            return {"t": P.ERROR, "error": "alliance unknown, token invalid, or revoked"}

        if al.get("device_x_pubkey") and al["device_x_pubkey"] != self.device_x_pub.hex():
            self._audit("hello.keymismatch", device_did=did, principal=al["principal"])
            return {"t": P.ERROR, "error": "device key does not match the alliance"}
        self.device_did = did
        self.alliance = al
        st = registry.issue_session_token(self.reg, did, self.transcript_hex)
        self._audit("hello.ok", device_did=did, principal=al["principal"])
        return {"t": P.HELLO_OK, "principal": al["principal"], "caps": json.loads(al["caps"]),
                "session_token": st}

    def _submit(self, msg) -> dict:
        if not self.alliance:
            return {"t": P.ERROR, "error": "no active session (pair or hello first)"}
        did = self.device_did

        if not registry.is_active(self.reg, did):
            self._audit("submit.revoked", device_did=did)
            return {"t": P.ERROR, "error": "alliance revoked"}
        al = registry.get_alliance(self.reg, did)

        sig_hex = msg.get("sig")
        if not sig_hex:
            self._audit("submit.unsigned", device_did=did, principal=al["principal"])
            return {"t": P.ERROR, "error": "submission is unsigned (rejected)"}
        payload = {k: v for k, v in msg.items() if k != "sig"}
        dev_pub = bytes.fromhex(al["device_pubkey"])
        if not crypto.ed_verify(dev_pub, bytes.fromhex(sig_hex), P.signing_bytes(payload)):
            self._audit("submit.badsig", device_did=did, principal=al["principal"])
            return {"t": P.ERROR, "error": "device signature invalid (spoofed submission)"}

        st = payload.get("session_token")
        if not st or not registry.consume_session_token(self.reg, did, self.transcript_hex, st):
            self._audit("submit.badsession", device_did=did, principal=al["principal"])
            return {"t": P.ERROR, "error": "missing/expired/replayed session token"}

        nonce = payload.get("nonce")
        if not nonce:
            return self._reissue_err(did, "submission missing nonce")

        if not NonceCache.valid_nonce(nonce):
            self._audit("submit.badnonce", device_did=did, principal=al["principal"])
            return self._reissue_err(did, "submission nonce malformed or too long")
        if self._seen_nonces.seen(did, nonce):
            self._audit("submit.replay_nonce", device_did=did, principal=al["principal"])
            return self._reissue_err(did, "replayed submission (nonce already used)")
        counter = payload.get("counter")
        if not registry.next_submit_counter_ok(self.reg, did, counter):
            self._audit("submit.replay_counter", device_did=did, principal=al["principal"])
            return self._reissue_err(did, "submission counter not strictly monotonic (replay)")

        ts = payload.get("ts")
        if not isinstance(ts, (int, float)) or abs(time.time() - ts) > 300:
            return self._reissue_err(did, "submission timestamp outside the freshness window")

        ok, why = registry.check_and_record_rate(self.reg, did)
        if not ok:
            return self._reissue_err(did, why)
        tt = payload.get("task_type")
        is_control = (tt == CONTROL_TASK_TYPE)

        if not is_control:
            max_conc = al["max_concurrency"]
            if isinstance(max_conc, int) and max_conc > 0:
                in_flight = self._in_flight_count(did)
                if in_flight >= max_conc:
                    self._audit("concurrency.ceiling", device_did=did, principal=al["principal"],
                                detail={"max_concurrency": max_conc, "in_flight": in_flight})
                    return self._reissue_err(
                        did, f"per-device concurrency ceiling exceeded ({max_conc} in-flight)",
                        extra={"concurrency_ceiling": max_conc})

        if is_control:
            self._seen_nonces.add(did, nonce)
            return self._control(did, al, payload)
        needs_stepup = (tt in _stepup_task_types()) or (payload.get("cmd") is not None)
        if needs_stepup:
            su = payload.get("step_up_2fa")

            ok2fa, why2fa = (
                (False, "no step-up code") if not su
                else registry.verify_stepup_2fa(self.reg, al["principal"], did, su))
            if not ok2fa:
                self._audit("submit.stepup_fail", device_did=did, principal=al["principal"],
                            detail={"task_type": tt, "why": why2fa})

                return self._reissue_err(did, "this operation requires a step-up 2FA code",
                                         extra={"need_step_up_2fa": True})
        self._seen_nonces.add(did, nonce)

        try:
            ceiling = sorted(json.loads(al["caps"]))
        except Exception:
            ceiling = []
        req = {"verb": "submit", "_method": ID_METHOD, "_selector": did,
               "via_device": did, "class": payload.get("class") or "worker",
               "source": "relay", "_ceiling_caps": ceiling}
        if payload.get("task_type") is not None:
            tt2 = payload["task_type"]

            if f"task_type:{tt2}" not in ceiling:
                self._audit("submit.ceiling_reject", device_did=did, principal=al["principal"],
                            detail={"task_type": tt2, "ceiling": ceiling})
                return self._reissue_err(
                    did, f"task_type {tt2!r} is not in this device's cap ceiling")
            req["task_type"] = tt2
            req["params"] = payload.get("params") or {}
        elif payload.get("cmd") is not None:

            req["cmd"] = payload["cmd"]
        else:
            return self._reissue_err(did, "submit requires task_type or cmd")
        resp = self.submit_fn(req)

        new_st = registry.issue_session_token(self.reg, did, self.transcript_hex)
        if not resp.get("ok"):
            self._audit("submit.engine_reject", device_did=did, principal=al["principal"],
                        detail={"error": resp.get("error"), "task_type": tt})
            return {"t": P.ERROR, "error": resp.get("error", "submit rejected by engine"),
                    "via": did, "session_token": new_st}

        self._inflight.add(did, resp.get("id") if resp.get("id") is not None else nonce)
        self._audit("submit.broker", device_did=did, principal=al["principal"],
                    detail={"job_id": resp.get("id"), "task_type": tt, "via_device": did,
                            "in_flight": self._inflight.count(did)})
        return {"t": P.RESULT, "id": resp.get("id"), "pos": resp.get("pos"),
                "principal": al["principal"], "via": did, "session_token": new_st}

    def _in_flight_count(self, did) -> int:

        if self._inflight_count_fn is None:
            return self._inflight.count(did)
        try:
            return int(self._inflight_count_fn(did))
        except Exception:
            return self._inflight.count(did)

    _TERMINAL_STATES = frozenset({"done", "failed", "cancelled", "timeout", "rejected"})

    def _release_if_terminal(self, did, control_resp) -> None:

        try:
            view = control_resp.get("cvm") or control_resp.get("job")
            if not isinstance(view, dict):
                return
            if view.get("state") in self._TERMINAL_STATES and view.get("id") is not None:
                self._inflight.remove(did, view["id"])
        except Exception:
            pass

    def _portal(self, did, al, payload, inner, verb, new_st) -> dict:

        if self.portal_fn is None:
            return {"t": P.ERROR, "error": "messenger door not available", "via": did,
                    "session_token": new_st}
        if verb in CONTROL_PORTAL_WRITE:

            try:
                _ceiling = set(json.loads(al["caps"]))
            except Exception:
                _ceiling = set()
            if "msg:write" not in _ceiling:
                self._audit("portal.write_denied", device_did=did, principal=al["principal"],
                            detail={"verb": verb})
                return self._reissue_err(did, "this device is read-only (messenger write not granted)")
        op = _PORTAL_OP.get(verb)
        args = inner.get("args") if isinstance(inner.get("args"), dict) else {}
        try:
            resp = self.portal_fn({"did": did, "op": op, "args": args})
        except Exception as e:
            resp = {"ok": False, "error": "portal bridge error: %s" % type(e).__name__}
        if not isinstance(resp, dict):
            resp = {"ok": False, "error": "portal bridge returned no response"}
        self._audit("portal.broker", device_did=did, principal=al["principal"],
                    detail={"op": op, "ok": bool(resp.get("ok"))})
        return {"t": P.RESULT, "id": None, "via": did, "principal": al["principal"],
                "session_token": new_st, "control_result": resp}

    def _control(self, did, al, payload) -> dict:

        inner = (payload.get("params") or {}).get("control")
        new_st = registry.issue_session_token(self.reg, did, self.transcript_hex)
        if not isinstance(inner, dict) or not inner.get("verb"):
            return self._reissue_err(did, "relay.control requires params.control.{verb,...}")
        verb = inner.get("verb")

        if verb in CONTROL_PORTAL_READ or verb in CONTROL_PORTAL_WRITE:
            return self._portal(did, al, payload, inner, verb, new_st)
        if verb not in CONTROL_READ_VERBS and verb not in CONTROL_DECISION_VERBS:
            self._audit("control.unknown_verb", device_did=did, principal=al["principal"],
                        detail={"verb": verb})
            return {"t": P.ERROR, "error": f"unsupported relay.control verb {verb!r}",
                    "via": did, "session_token": new_st}

        if verb in _stepup_control_verbs():
            su = payload.get("step_up_2fa")
            ok2fa, why2fa = (
                (False, "no step-up code") if not su
                else registry.verify_stepup_2fa(self.reg, al["principal"], did, su))
            if not ok2fa:
                self._audit("control.stepup_fail", device_did=did, principal=al["principal"],
                            detail={"verb": verb, "why": why2fa})
                return self._reissue_err(
                    did, f"relay.control {verb} requires a step-up 2FA code",
                    extra={"need_step_up_2fa": True})

        try:
            ceiling = sorted(json.loads(al["caps"]))
        except Exception:
            ceiling = []

        SAFE = ("id", "nonce", "topics", "after_id", "limit", "feedback", "input", "state", "kind")
        req = {k: inner[k] for k in SAFE if k in inner}
        req.update({"verb": verb, "_method": ID_METHOD, "_selector": did,
                    "via_device": did, "source": "relay", "_ceiling_caps": ceiling})
        resp = self.control_fn(req)
        if not isinstance(resp, dict):
            resp = {"ok": False, "error": "control broker returned no response"}

        self._release_if_terminal(did, resp)
        self._audit("control.broker", device_did=did, principal=al["principal"],
                    detail={"verb": verb, "ok": bool(resp.get("ok")), "via_device": did,
                            "id": resp.get("id")})

        return {"t": P.RESULT, "id": None, "via": did, "principal": al["principal"],
                "session_token": new_st, "control_result": resp}

    def _reissue_err(self, did, error, extra=None) -> dict:

        out = {"t": P.ERROR, "error": error, "via": did}
        try:
            out["session_token"] = registry.issue_session_token(self.reg, did, self.transcript_hex)
        except Exception:
            pass
        if extra:
            out.update(extra)
        return out
