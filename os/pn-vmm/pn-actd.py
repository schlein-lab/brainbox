#!/usr/bin/env python3

import sys, os, json, socket, struct, threading, time, hashlib, hmac

LLMD_SOCK = os.environ.get("PN_LLMD_SOCK", os.path.expanduser("~/.local/run/pn-llmd.sock"))
POLICY_PATH = os.environ.get("PN_ACTD_POLICY", "")

AUDIT_PATH = os.environ.get("PN_ACTD_AUDIT", "/tmp/pn-actd-audit.jsonl")
ATSPID_SOCK = os.environ.get("PN_ATSPID_SOCK", os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()), "pn-atspid.sock"))
DEFAULT_SESSION = os.environ.get("PN_ACTD_SESSION", "test-session")

def elog(*a):
    sys.stderr.write("[pn-actd] " + " ".join(str(x) for x in a) + "\n"); sys.stderr.flush()

TOOLS = {
    "read_screen": {
        "description": "Semantic accessibility tree of a visible app (roles/names/values; secure fields redacted). Read-only.",
        "inputSchema": {"type": "object", "properties": {"app": {"type": "string"}}, "required": []},
        "annotations": {"readOnlyHint": True}, "mutating": False},
    "drive_program": {
        "description": "Perform the primary/named action on a UI element addressed by role+name (e.g. click 'Send'). Governed: may queue or be denied.",
        "inputSchema": {"type": "object", "properties": {"app": {"type": "string"}, "role": {"type": "string"},
                        "name": {"type": "string"}, "action": {"type": "string"}}, "required": ["app", "role", "name"]},
        "annotations": {"destructiveHint": True}, "mutating": True},
    "inject_input": {
        "description": "Type text or a key into an app (Tier-2 fallback when no semantic action exists). Governed.",
        "inputSchema": {"type": "object", "properties": {"app": {"type": "string"}, "text": {"type": "string"},
                        "key": {"type": "string"}, "enter": {"type": "boolean"}}, "required": []},
        "annotations": {"destructiveHint": True}, "mutating": True},
    "list_capabilities": {
        "description": "What phantom powers this session is currently allowed (tri-state allowlist) and current queue depth.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": True}, "mutating": False},
}
MAX_TYPE_CHARS = 4000

def load_policy():

    pol = {"tools": {"read_screen": "allow", "drive_program": "ask", "inject_input": "deny",
                     "list_capabilities": "allow"},
           "caveats": {"apps": [], "roles_denied": ["password"], "max_type_chars": MAX_TYPE_CHARS}}
    if POLICY_PATH and os.path.exists(POLICY_PATH):
        try:
            j = json.load(open(POLICY_PATH))
            if isinstance(j.get("tools"), dict): pol["tools"].update(j["tools"])
            if isinstance(j.get("caveats"), dict): pol["caveats"].update(j["caveats"])
        except Exception as e:
            elog("policy load error (fail-closed to default): %r" % e)
    return pol

def _llmd_rpc(req, timeout=3.0):
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(timeout); c.connect(LLMD_SOCK)
        c.sendall((json.dumps(req, separators=(",", ":")) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            d = c.recv(65536)
            if not d: break
            buf += d
        c.close()
        return json.loads(buf.split(b"\n", 1)[0] or b"{}")
    except Exception:
        return None

def act_acquire(turn, session, tool, on_pos=None, deadline_s=120):
    r = _llmd_rpc({"verb": "act-admit", "id": turn, "cell_principal": session, "tool": tool, "klass": "interactive"})
    if r is None:
        return "down"
    t0 = time.time()
    while True:
        if r.get("granted"): return "ok"
        if on_pos: on_pos(int(r.get("position", -1)))
        if time.time() - t0 > deadline_s: return "timeout"
        time.sleep(0.2)
        r = _llmd_rpc({"verb": "act-admit-poll", "id": turn})
        if r is None: return "down"

def act_release(turn):
    _llmd_rpc({"verb": "act-admit-release", "id": turn})

def _atspid_rpc(req, timeout=8.0):
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(timeout); c.connect(ATSPID_SOCK)
        c.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            d = c.recv(1 << 20)
            if not d: break
            buf += d
        c.close()
        return json.loads(buf.split(b"\n", 1)[0] or b"{}")
    except Exception as e:
        return {"_unavailable": True, "error": str(e)}

def execute(tool, args, pol):

    if tool == "list_capabilities":
        return {"allowlist": pol["tools"], "caveats": pol["caveats"], "queue": act_depth(),
                "status": aufbaustand()}, None
    if tool == "read_screen":
        r = _atspid_rpc({"verb": "read_tree", "app": args.get("app", "")})
        if r.get("_unavailable"):
            return {"nodes": [], "note": "no live seat/pn-atspid on this host (headless standby); read returns empty"}, None
        return r, None
    if tool == "drive_program":
        r = _atspid_rpc({"verb": "invoke", "app": args.get("app"), "role": args.get("role"),
                         "name": args.get("name"), "action": args.get("action", "")})
        if r.get("_unavailable"):
            return None, "phantom/pn-atspid unavailable (no live seat)"
        return r, None
    if tool == "inject_input":
        r = _atspid_rpc({"verb": "press_keys", "app": args.get("app"), "text": args.get("text", ""),
                         "key": args.get("key", ""), "enter": bool(args.get("enter"))})
        if r.get("_unavailable"):
            return None, "phantom/pn-atspid unavailable (no live seat)"
        return r, None
    return None, "unknown tool"

def act_depth():
    r = _llmd_rpc({"verb": "act-admit-snapshot"})
    if not r: return {"slots": None, "waiting": None}
    return {"slots": r.get("slots"), "in_use": r.get("in_use"), "waiting": r.get("waiting")}

def aufbaustand():

    ask_mode = os.environ.get("PN_ACTD_ASK_MODE", "deny")
    seat = os.path.exists(ATSPID_SOCK)
    mutierend_moeglich = bool(seat and ask_mode == "allow_audit")
    return {
        "im_aufbau": not mutierend_moeglich,
        "seat": ("pn-atspid erreichbar" if seat else
                 "KEIN pn-atspid unter %s — read_screen liefert leer, jeder Akt endet 'unavailable' "
                 "(Bauplan Stufen 1-3)" % ATSPID_SOCK),
        "policy_quelle": (POLICY_PATH if (POLICY_PATH and os.path.exists(POLICY_PATH)) else
                          "eingebaute Vorgabe — PN_ACTD_POLICY ist ungesetzt und hat keinen Setzer "
                          "(Bauplan Stufe 4)"),
        "ask_mode": ask_mode,
        "wirkung": ("IM AUFBAU, HEUTE KEINE KONTROLLE: diese Bahn traegt bisher nur list_capabilities "
                    "und ein leeres read_screen. Mutierende Akte sterben im Policy-Gate — nicht an "
                    "Admission, Caveats oder Audit. Siehe Kopf von pn-actd.py + os/display/"
                    "p55-ausbauplan.md."
                    if not mutierend_moeglich else
                    "Mutierende Akte koennen die Ausfuehrung erreichen."),
    }

def policy_gate(tool, args, pol):
    tri = pol["tools"].get(tool)
    if tool not in TOOLS:
        return False, "unknown_tool"
    if tri == "deny" or tri is None:
        return False, "role_not_allowed"

    if tri == "ask":

        _mode = os.environ.get("PN_ACTD_ASK_MODE", "deny")
        if _mode == "allow_audit":
            pass
        elif _mode == "elicit":
            return False, "ask_elicit_pending"
        else:
            return False, "ask_denied_no_elicit"
    cav = pol.get("caveats", {})

    req_role = str(args.get("role") or "").lower()
    for _denied in (cav.get("roles_denied") or []):
        if _denied and str(_denied).lower() in req_role:
            return False, "secure_field_blocked:%s" % args["role"]

    apps = cav.get("apps") or []

    if apps and args.get("app") not in apps:
        return False, "target_app_not_granted:%s" % (args.get("app") or "<none>")

    if tool == "inject_input":
        txt = str(args.get("text") or "")
        if len(txt) > int(cav.get("max_type_chars", MAX_TYPE_CHARS)):
            return False, "type_length_exceeded"
    return True, "ok"

_audit_lock = threading.Lock()
_prev_hash = "0" * 64
_seq = 0

def _digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def audit(session, tool, args, decision, reason, queue_pos, latency_ms, result):
    global _prev_hash, _seq
    with _audit_lock:
        _seq += 1
        row = {"seq": _seq, "prev_hash": _prev_hash, "ts": round(time.time(), 3), "session": session,
               "tool": tool, "args_digest": _digest(args), "decision": decision, "reason": reason,
               "queue_pos": queue_pos, "latency_ms": latency_ms,
               "result_hash": _digest(result) if result is not None else None}
        row_hash = hashlib.sha256((_prev_hash + str(_seq) + _digest(row)).encode()).hexdigest()
        row["row_hash"] = row_hash
        _prev_hash = row_hash
        try:
            with open(AUDIT_PATH, "a") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
                f.flush(); os.fsync(f.fileno())
        except Exception as e:
            elog("AUDIT WRITE FAILED (fail-closed): %r" % e)
            return False
        return True

_turnseq = 0
def _next_turn():
    global _turnseq; _turnseq += 1; return "act-%d-%d" % (int(time.time()), _turnseq)

def handle_call(session, tool, args, send_frame):
    t0 = time.time()
    pol = load_policy()

    allowed, reason = policy_gate(tool, args, pol)
    if not allowed:
        ok = audit(session, tool, args, "deny", reason, None, int((time.time()-t0)*1000), None)
        send_frame({"status": "denied", "reason": reason if ok else "audit_failed"})
        return
    meta = TOOLS[tool]
    turn = None; qpos = None
    try:

        if meta["mutating"]:
            turn = _next_turn()
            v = act_acquire(turn, session, tool, on_pos=lambda p: send_frame({"status": "queued", "pos": p}))
            if v != "ok":
                audit(session, tool, args, "deny", "admission_%s" % v, None, int((time.time()-t0)*1000), None)
                send_frame({"status": "denied", "reason": "admission_%s" % v})
                return

        res, exerr = execute(tool, args, pol)
        if exerr:
            audit(session, tool, args, "deny", exerr, qpos, int((time.time()-t0)*1000), None)
            send_frame({"status": "error", "reason": exerr})
            return

        lat = int((time.time()-t0)*1000)
        if not audit(session, tool, args, "allow", "ok", qpos, lat, res):
            send_frame({"status": "error", "reason": "audit_failed"}); return
        send_frame({"status": "ok", "result": res})
    finally:
        if turn: act_release(turn)

def handle_list_tools(session, send_frame):
    pol = load_policy()
    stand = aufbaustand()
    granted = []
    for name, meta in TOOLS.items():
        tri = pol["tools"].get(name)
        if tri in ("allow", "ask"):
            desc = meta["description"]

            if meta["mutating"] and stand["im_aufbau"]:
                desc += (" ⚠️ IM AUFBAU: auf dieser Anlage DERZEIT IMMER ABGELEHNT (%s) — nicht "
                         "versuchen; `list_capabilities` nennt den Grund."
                         % ("'ask' ohne Owner-Elicit-Zeremonie" if tri == "ask" else "kein Seat"))
            granted.append({"name": name, "description": desc,
                            "inputSchema": meta["inputSchema"], "annotations": meta["annotations"]})
    send_frame({"status": "ok", "result": granted})

def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        d = sock.recv(n - len(buf))
        if not d: raise ConnectionError("closed")
        buf += d
    return buf

def serve_conn(sock, session):
    def send_frame(obj):
        b = json.dumps(obj, separators=(",", ":")).encode()
        sock.sendall(struct.pack("!I", len(b)) + b)
    elog("lane accept session=%s" % session)
    try:
        while True:
            (ln,) = struct.unpack("!I", _recvn(sock, 4))
            req = json.loads(_recvn(sock, ln))
            op = req.get("op")
            if op == "list_tools":
                handle_list_tools(session, send_frame)
            elif op == "call":
                handle_call(session, req.get("tool"), req.get("args") or {}, send_frame)
            else:
                send_frame({"status": "error", "reason": "unknown op"})
    except (ConnectionError, OSError):
        pass
    finally:
        try: sock.close()
        except Exception: pass

def _session_for_peer(addr):

    return DEFAULT_SESSION

def main():
    t = os.environ.get("PN_ACTD_LISTEN", "vsock:9300")
    kind, rest = t.split(":", 1)
    if kind == "vsock":
        srv = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((socket.VMADDR_CID_ANY, int(rest))); srv.listen(16)
    elif kind == "tcp":
        host, port = rest.rsplit(":", 1)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, int(port))); srv.listen(16)
    elif kind == "unix":
        if os.path.exists(rest): os.unlink(rest)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(rest); srv.listen(16)
    else:
        raise ValueError("bad PN_ACTD_LISTEN " + t)
    elog("up listen=%s llmd=%s atspid=%s audit=%s" % (t, LLMD_SOCK, ATSPID_SOCK, AUDIT_PATH))
    _d = aufbaustand()
    if _d["im_aufbau"]:
        elog("IM AUFBAU (P5.5) — heute noch KEINE Kontrolle: %s | seat=%s | policy=%s | ask_mode=%s"
             % (_d["wirkung"], _d["seat"], _d["policy_quelle"], _d["ask_mode"]))
    while True:
        c, addr = srv.accept()
        threading.Thread(target=serve_conn, args=(c, _session_for_peer(addr)), daemon=True).start()

if __name__ == "__main__":
    main()
