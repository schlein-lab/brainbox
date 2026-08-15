
import os
import sys
import json
import time
import threading

_chan_ctx = None
def _prov_log(*_a, **_k):
    return None

require_2fa = None
box_hmac_secret = None
box_x_priv = None
box_keys = None
alliance_lookup = None
alliance_for_principal = None
local_channel_factory = None
transport_mod = None
is_production_ready = None
serve_fn = None
adapters_factory = None
relay_url = None
per_principal_max = 4
lane_ttl_s = 1800
idle_timeout = 120.0
ticket_ttl_s = 120
reap_interval_s = 5.0

def configure(**kw):

    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _ensure_relaylib_path():
    for _p in (os.environ.get("PNLIB_HOME"), os.path.expanduser("~/portioneer")):
        if _p and os.path.isdir(os.path.join(_p, "relaylib")):
            if _p in sys.path:
                sys.path.remove(_p)
            sys.path.insert(0, _p)
            break

def _livechat():
    _ensure_relaylib_path()
    from relaylib import livechat
    return livechat

def _livechat_portal():
    _ensure_relaylib_path()
    from relaylib import livechat_portal
    return livechat_portal

def _registry():
    _ensure_relaylib_path()
    from relaylib import registry
    return registry

def _transport():
    if transport_mod is not None:
        return transport_mod
    _ensure_relaylib_path()
    from relaylib import transport
    return transport

def _appliance_keys():
    global box_keys
    if box_keys is None:
        _ensure_relaylib_path()
        from relaylib.keys import ApplianceKeys
        box_keys = ApplianceKeys()
    return box_keys

def _box_hmac_secret():
    if box_hmac_secret is not None:
        return box_hmac_secret if isinstance(box_hmac_secret, (bytes, bytearray)) else \
            bytes(box_hmac_secret, "utf-8")
    import hmac as _h
    import hashlib as _hl
    idp = _appliance_keys().id_priv
    return _h.new(idp, b"brainarbeit/live/ticket-mac/v1", _hl.sha256).digest()

def _box_x_priv_hex():
    if box_x_priv is not None:
        return box_x_priv
    sx = _appliance_keys().sx_priv
    return sx.hex() if isinstance(sx, (bytes, bytearray)) else str(sx)

def _resolve_is_production_ready():
    if is_production_ready is not None:
        return is_production_ready
    _ensure_relaylib_path()
    import relaylib
    return relaylib.is_production_ready

def _require_2fa_fn():
    if require_2fa is not None:
        return require_2fa
    import portal_delete_guard as _dg
    return _dg.require_2fa

def _relay_url_default():
    return relay_url or os.environ.get("RELAY_URL", "") or ""

def _ticket_ttl():
    try:
        return int(ticket_ttl_s)
    except Exception:
        return 120

def _idle_timeout():
    try:
        return float(idle_timeout)
    except Exception:
        return 120.0

def _safe_prov(verb, principal, text, meta):
    try:
        _prov_log(verb, principal, text, meta)
    except Exception:
        pass

def _resolve_device_x_pub(did):

    if alliance_lookup is not None:
        al = alliance_lookup(did)
    else:
        reg = _registry()
        cx = reg.connect()
        try:
            al = reg.get_alliance(cx, did)
        finally:
            try:
                cx.close()
            except Exception:
                pass
    if not al or not al.get("device_x_pubkey"):
        raise KeyError("no device_x_pubkey for did %r" % did)
    return al["device_x_pubkey"]

def _default_did_for(principal):

    if alliance_for_principal is not None:
        return alliance_for_principal(principal)
    try:
        reg = _registry()
        cx = reg.connect()
        try:
            for al in reg.list_alliances(cx, principal):
                if al.get("revoked_at") is None and al.get("device_did"):
                    return al["device_did"]
        finally:
            try:
                cx.close()
            except Exception:
                pass
    except Exception:
        return None
    return None

class LaneRefused(Exception):
    def __init__(self, msg, code=503):
        super().__init__(msg)
        self.code = code

def _outbound_enabled():

    if os.environ.get("LIVE_LANE_ENABLED", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        return bool(_resolve_is_production_ready()())
    except Exception:
        return False

def _open_channel(rz, url):

    if local_channel_factory is not None:
        return local_channel_factory(rz)
    if not url:
        raise LaneRefused("no relay_url configured for the live lane", 503)
    if not _outbound_enabled():
        raise LaneRefused("outbound relay dial refused: live lane not enabled (LIVE_LANE_ENABLED "
                          "+ is_production_ready required; fail-closed default OFF)", 503)
    return _transport().Channel.register(url, rz)

def _default_adapters(ctx, principal, kind):
    lp = _livechat_portal()
    return dict(
        say_fn=lp.make_say_fn(ctx, principal),
        transcript_fn=lp.make_transcript_fn(ctx, principal),
        sessions_fn=lp.make_sessions_fn(ctx, principal),
        decisions_fn=lp.make_decisions_fn(principal),
        decide_fn=lp.make_decide_fn(principal),
    )

def _serve_for_kind(kind):

    if kind == "chat":
        return serve_fn if serve_fn is not None else _livechat().serve_chat
    return None

class LaneManager:

    def __init__(self, *, per_principal_max=4, lane_ttl_s=1800, idle_timeout=120.0,
                 reap_interval_s=5.0):
        self._lock = threading.RLock()
        self._lanes = {}
        self.per_principal_max = int(per_principal_max)
        self.lane_ttl_s = int(lane_ttl_s)
        self.idle_timeout = float(idle_timeout)
        self.reap_interval_s = float(reap_interval_s)
        self._shutdown = threading.Event()
        self._reaper = None

    def _count_for(self, principal):
        return sum(1 for r in self._lanes.values() if r["principal"] == principal)

    def count(self):
        with self._lock:
            return len(self._lanes)

    def _start_reaper(self):
        if self._reaper is not None:
            return
        t = threading.Thread(target=self._reap_loop, name="live-lane-reaper", daemon=True)
        self._reaper = t
        t.start()

    def open_lane(self, ticket, principal, did, kind, device_x_pub):
        serve = _serve_for_kind(kind)
        if serve is None:

            return {"ok": True, "worker": False,
                    "note": "kind=%s: ticket minted; the portal chat worker is chat-only in "
                            "Phase 1b (voice serve is box-local, a follow-up)" % kind}
        rz = ticket["rz"]
        with self._lock:
            if self._count_for(principal) >= self.per_principal_max:
                return {"ok": False, "code": 429,
                        "error": "lane limit reached for this principal (%d)" % self.per_principal_max}
            if rz in self._lanes:
                return {"ok": False, "code": 500, "error": "rz collision"}
            rec = {"principal": principal, "did": did, "kind": kind, "rz": rz,
                   "started_at": time.time(), "deadline": time.time() + self.lane_ttl_s,
                   "stop": threading.Event(), "thread": None, "ch": None, "state": "opening",
                   "ticket": ticket, "device_x_pub": device_x_pub}
            self._lanes[rz] = rec

        try:
            ch = _open_channel(rz, ticket.get("relay_url") or "")
        except LaneRefused as e:
            with self._lock:
                self._lanes.pop(rz, None)
            return {"ok": False, "code": getattr(e, "code", 503), "error": str(e)}
        except Exception as e:
            with self._lock:
                self._lanes.pop(rz, None)
            return {"ok": False, "code": 502, "error": "channel open failed: %s" % e}
        rec["ch"] = ch
        rec["state"] = "serving"
        th = threading.Thread(target=self._run, args=(rz, serve), name="live-lane-" + rz[:8],
                              daemon=True)
        rec["thread"] = th
        self._start_reaper()
        th.start()
        return {"ok": True, "worker": True, "rz": rz}

    def _run(self, rz, serve):
        rec = self._lanes.get(rz)
        if rec is None:
            return
        try:
            lc = _livechat()
            ok, body = lc.verify_ticket(_box_hmac_secret(), rec["ticket"])
            if not ok:
                _safe_prov("live.reject", rec["principal"], "bad-ticket", {"rz": rz[:8], "why": body})
                return
            principal = body["principal"]
            lane = lc.Lane(rec["ch"], _box_x_priv_hex(), rec["device_x_pub"],
                           rec["ticket"]["lane_id"], role="box")
            ctx = _chan_ctx() if callable(_chan_ctx) else None
            af = adapters_factory if adapters_factory is not None else _default_adapters
            adapters = af(ctx, principal, rec["kind"])
            serve(lane, should_stop=rec["stop"].is_set, idle_timeout=self.idle_timeout, **adapters)
        except Exception as e:
            _safe_prov("live.error", rec.get("principal"), "worker", {"rz": rz[:8], "err": str(e)})
        finally:
            self.retire(rz)

    def retire(self, rz):
        with self._lock:
            rec = self._lanes.pop(rz, None)
        if rec is not None:
            try:
                rec["stop"].set()
            except Exception:
                pass
            ch = rec.get("ch")
            if ch is not None:
                try:
                    ch.close()
                except Exception:
                    pass

    def stop_lane(self, rz, join_timeout=2.0):
        with self._lock:
            rec = self._lanes.get(rz)
        if rec is None:
            return False
        try:
            rec["stop"].set()
        except Exception:
            pass
        th = rec.get("thread")
        if th is not None:
            th.join(timeout=join_timeout)
        self.retire(rz)
        return True

    def _reap_once(self):
        now = time.time()
        with self._lock:
            dead = [rz for rz, r in self._lanes.items()
                    if now > r.get("deadline", 0)
                    or (r.get("thread") is not None and not r["thread"].is_alive()
                        and r.get("state") == "serving")]
        for rz in dead:
            self.stop_lane(rz, join_timeout=0.5)
        return dead

    def _reap_loop(self):
        while not self._shutdown.wait(self.reap_interval_s):
            try:
                self._reap_once()
            except Exception:
                pass

    def close_all(self, join_timeout=2.0):
        self._shutdown.set()
        with self._lock:
            rzs = list(self._lanes.keys())
        for rz in rzs:
            self.stop_lane(rz, join_timeout=join_timeout)

_MGR = None
_MGR_LOCK = threading.Lock()

def _get_mgr():
    global _MGR
    with _MGR_LOCK:
        if _MGR is None:
            _MGR = LaneManager(per_principal_max=per_principal_max, lane_ttl_s=lane_ttl_s,
                               idle_timeout=_idle_timeout(), reap_interval_s=reap_interval_s)
        return _MGR

class LiveRoutes:

    _LIVE_GET = {}
    _LIVE_POST = {"/live/open": "_api_live_open"}

    def _live_json(self, obj, code=200):
        return self.send_html(json.dumps(obj, ensure_ascii=False), code,
                              [("Content-Type", "application/json")])

    def _live_dispatch(self, method, path, query="", raw=None):

        m = (method or "").upper()
        name = (self._LIVE_GET if m == "GET" else
                self._LIVE_POST if m == "POST" else {}).get(path)
        if not name:
            return False
        if not self.authed():
            self.send_html("unauthorized", 403)
            return True
        if m == "GET":
            getattr(self, name)(query or "")
        else:
            getattr(self, name)(raw if raw is not None else self._body())
        return True

    def _api_live_open(self, raw):

        principal = self._principal()
        try:
            if isinstance(raw, (bytes, bytearray)):
                body = json.loads(raw.decode("utf-8", "replace") or "{}")
            else:
                body = json.loads(raw or "{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        code = str(body.get("totp") or body.get("code") or "")
        try:
            ok2, resp = _require_2fa_fn()(principal, code, None, prov_log=_prov_log,
                                          action="live.open")
        except Exception as e:
            return self._live_json({"ok": False, "need_2fa": True,
                                    "error": "2FA-Prüfung nicht möglich — Lane gesperrt (%s)." % e}, 403)
        if not ok2:
            return self._live_json(resp if isinstance(resp, dict) else
                                   {"ok": False, "error": "2FA required"}, 403)

        kind = str(body.get("kind") or "chat").strip().lower()
        if kind not in ("voice", "chat"):
            return self._live_json({"ok": False, "error": "bad kind (voice|chat)"}, 400)

        did = str(body.get("did") or body.get("device_did") or "").strip()
        if not did:
            did = _default_did_for(principal) or ""
        if not did:
            return self._live_json({"ok": False, "error": "no paired device for this principal"}, 400)

        try:
            device_x_pub = _resolve_device_x_pub(did)
        except Exception as e:
            return self._live_json({"ok": False, "error": "unknown/unpaired device: %s" % e}, 404)

        url = str(body.get("relay_url") or _relay_url_default() or "")
        lc = _livechat()
        ticket = lc.mint_ticket(_box_hmac_secret(), principal=principal, device_did=did,
                                relay_url=url, ttl_s=_ticket_ttl())

        res = _get_mgr().open_lane(ticket, principal, did, kind, device_x_pub)
        if not res.get("ok"):
            return self._live_json({"ok": False, "error": res.get("error", "cannot open lane")},
                                   res.get("code", 503))

        _safe_prov("live.open", principal, kind,
                   {"did": did, "rz": ticket["rz"][:8], "lane": ticket["lane_id"][:8],
                    "worker": res.get("worker", True)})
        out = {k: ticket[k] for k in ("rz", "lane_id", "principal", "did", "relay_url", "exp", "mac")}
        return self._live_json({"ok": True, "kind": kind, "worker": res.get("worker", True),
                                "ticket": out, "note": res.get("note")}, 200)
