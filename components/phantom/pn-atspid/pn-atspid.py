#!/usr/bin/env python3
"""pn-atspid — the AT-SPI control daemon (Brainarbeit OS, display.md §5, the control
ladder). This is the LLM's "operate apps by NAME, not pixels" plane; it pairs with the
live MJPEG stream (streamd.rs, §7 — pixels for the human's eyes) so the model acts
through the semantic object model while the human watches the same seat.

LANGUAGE CHOICE — Python (not Rust). The box ships rustc 1.75.0 and phantom is a
hand-rolled, zero-dependency Rust crate; the only portable, mature AT-SPI client is
`gi.repository.Atspi` (the AT-SPI2 D-Bus client lib), already proven in phantom's
`phantom-atspi-helper.py` / the `~/uiapi` engine. A Rust path would need
atspi+zbus+tokio, none of which build cleanly on 1.75 and all of which break the
zero-dep promise. So pn-atspid REUSES the helper's Atspi engine and exposes it as a
long-lived daemon with a JSON IPC. No toolchain risk; the SAME act/sense bridge that
`sense_text` (main.rs, BUG-5) already shells to.

ARCHITECTURE
  • Tier 0/1 (SENSE + the default ACT path) run IN-PROCESS via Atspi: read_tree,
    invoke(DoAction), set_value, insert_text. These never need the compositor.
  • Tier 2/3 (input injection) are NOT the default. They go through phantom's existing
    actuator: the control socket ($XDG_RUNTIME_DIR/phantom.ctl), the very same
    newline-text protocol phantomctl speaks. press_keys -> `act <target> key|type ...`
    (uinput/forged keys); click_at -> resolve Component.GetExtents box -> `act <target>
    click <x> <y>` which is compositor::act_pointer (FEATURE-1, the forged wl_pointer).

THE STRICT LADDER (enforced SERVER-SIDE, display.md §5):
  read_tree (Tier 0)  ── sense; cheap, pixel-free, always allowed.
  invoke    (Tier 1)  ── DEFAULT acting path. AT-SPI DoAction by role+name.
  set_value / insert_text (Tier 1) ── value/text interfaces by role+name.
  press_keys (Tier 2) ── virtual keyboard. Allowed when an object can't be invoked.
  click_at   (Tier 3) ── LAST RESORT. Refused if the target object exposes ANY usable
                         Tier 0–2 affordance (an action, or an editable text/value
                         interface). The daemon makes the model EARN the click.

NO-DONE-WITHOUT-RECORD: every ACTING verb (invoke/set_value/insert_text/press_keys/
click_at) emits one minimal Record stub line to the record sink (stderr + an optional
$PN_RECORD file) before returning. Sensing (read_tree) does not record.

WHERE MCP/rmcp ATTACHES: the JSON-over-unix-socket request schema below is verb-for-verb
the MCP tool surface display.md §5 specifies. An rmcp/stdio MCP server is a thin shim:
each MCP tool call -> one JSON request to this socket -> the JSON reply becomes the tool
result. The ladder, the record hook, and the Atspi engine all live here, server-side, so
the MCP layer stays a dumb transport. See `mcp_tool_schema()` for the generated surface.

IPC: a unix socket ($PN_ATSPID_SOCK, default $XDG_RUNTIME_DIR/pn-atspid.sock). One JSON
object per connection (newline-terminated), one JSON object reply. Fails CLOSED: a bad
request or an engine error returns {"ok": false, "error": ...}; the daemon never crashes
on a request.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

Atspi.init()

_ST = {
    Atspi.StateType.EDITABLE: "editable",
    Atspi.StateType.FOCUSABLE: "focusable",
    Atspi.StateType.FOCUSED: "focused",
    Atspi.StateType.SHOWING: "showing",
    Atspi.StateType.VISIBLE: "visible",
    Atspi.StateType.ENABLED: "enabled",
    Atspi.StateType.SENSITIVE: "sensitive",
    Atspi.StateType.CHECKED: "checked",
    Atspi.StateType.SELECTED: "selected",
    Atspi.StateType.EXPANDED: "expanded",
}

def desktop():
    return Atspi.get_desktop(0)

def role(o):
    try:
        return o.get_role_name()
    except Exception:
        return "?"

def name(o):
    try:
        return o.get_name() or ""
    except Exception:
        return ""

def states(o):
    try:
        ss = o.get_state_set()
        return [v for k, v in _ST.items() if ss.contains(k)]
    except Exception:
        return []

def actions(o):
    try:
        return [o.get_action_name(i) or "" for i in range(o.get_n_actions())]
    except Exception:
        return []

def n_actions(o):
    try:
        return o.get_n_actions()
    except Exception:
        return 0

def char_count(o):

    try:
        return Atspi.Text.get_character_count(o)
    except Exception:
        try:
            return o.get_character_count()
        except Exception:
            return None

def read_text(o):

    n = char_count(o)
    if n is None:
        return None
    try:
        return Atspi.Text.get_text(o, 0, n)
    except Exception:
        pass
    for end in (n, -1):
        try:
            return o.get_text(0, end)
        except Exception:
            continue
    return None

def has_text_iface(o):

    try:
        return "editable" in states(o) and char_count(o) is not None
    except Exception:
        return False

def has_value_iface(o):

    try:
        o.get_current_value()
        return True
    except Exception:
        return False

def extents(o):

    try:
        e = o.get_extents(Atspi.CoordType.SCREEN)
        return {"x": e.x, "y": e.y, "w": e.width, "h": e.height}
    except Exception:
        return None

def app_by(token):

    d = desktop()
    if token is None or token == "":
        return d
    if str(token).isdigit():
        i = int(token)
        return d.get_child_at_index(i) if 0 <= i < d.get_child_count() else None
    tl = str(token).lower()
    best = None
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        nm = name(a).lower()
        if nm == tl:
            return a
        if tl in nm and best is None:
            best = a
    return best

def allowed_actions(o):

    out = []
    acts = actions(o)
    if any(acts):
        out.append("invoke")
    if has_value_iface(o):
        out.append("set_value")
    if has_text_iface(o):
        out.append("insert_text")
    if "focusable" in states(o):
        out.append("press_keys")
    if extents(o) is not None:
        out.append("click_at")
    return out

def node_dict(o, path, want_text=False, want_extents=True):
    d = {
        "path": path,
        "role": role(o),
        "name": name(o),
        "states": states(o),
        "actions": [a for a in actions(o) if a],
        "allowed_actions": allowed_actions(o),
    }
    if want_extents:
        ext = extents(o)
        if ext:
            d["extents"] = ext
    if want_text:

        if _secure_node(o):
            d["text_redacted"] = True
        else:
            t = read_text(o)
            if t:
                d["text"] = t[:400]
    return d

def resolve_by_role_name(app_token, want_role=None, want_name=None, want_editable=False,
                         budget=20000):

    root = app_by(app_token)
    if root is None:
        return None
    wr = (want_role or "").lower()
    wn = (want_name or "").lower()
    stack = [root]
    seen = 0
    while stack and seen < budget:
        o = stack.pop(0)
        seen += 1
        ok = bool(wr or wn or want_editable)
        if wr and wr not in role(o).lower():
            ok = False
        if ok and wn and wn not in name(o).lower():
            ok = False
        if ok and want_editable and "editable" not in states(o):
            ok = False
        if ok:
            return o
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 250)):
            try:
                stack.append(o.get_child_at_index(j))
            except Exception:
                pass
    return None

def _ctl_path():
    return os.environ.get("PN_PHANTOM_CTL") or os.environ.get("PHANTOM_CTL") or \
        os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "phantom.ctl")

def phantom_ctl(cmd, timeout=5.0):

    path = _ctl_path()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((cmd + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
        s.close()
        return b"".join(chunks).decode("utf-8", "replace")
    except Exception as e:
        return f"__PN_CTL_ERROR__ {e}"

_CLIENT_RE = re.compile(r'cid=(\d+).*?title="([^"]*)".*?app_id="([^"]*)"')

def phantom_clients():

    rep = phantom_ctl("clients")
    out = []
    for line in rep.splitlines():
        m = _CLIENT_RE.search(line)
        if m:
            out.append({"cid": int(m.group(1)), "title": m.group(2),
                        "app_id": m.group(3)})
    return out

def phantom_target_for_app(app_name):

    if app_name is None:
        return None
    al = app_name.lower()
    best = None
    for c in phantom_clients():
        hay = (c["title"] + " " + c["app_id"]).lower()
        if al and (al in hay or any(al in w for w in hay.split())):
            best = c
            break

        if c["app_id"] and c["app_id"].lower() in al:
            best = c
    if best is None:
        return None

    if best["title"]:
        return "@" + best["title"]
    return str(best["cid"])

def record(verb, tier, target, detail, ok):

    rec = {
        "ts": round(time.time(), 3),
        "kind": "act",
        "verb": verb,
        "tier": tier,
        "target": target,
        "detail": detail,
        "ok": bool(ok),
    }
    line = "RECORD " + json.dumps(rec, ensure_ascii=False)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    path = os.environ.get("PN_RECORD")
    if path:
        try:
            with open(path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

def v_read_tree(req):

    app = req.get("app")
    max_nodes = int(req.get("max", 600))
    depth_max = int(req.get("depth", 30))
    fanout = int(req.get("fanout", 120))
    want_text = bool(req.get("text", False))
    root = app_by(app)
    if root is None:
        return {"ok": False, "error": "app not found", "app": app}
    budget = [max_nodes]
    nodes = []

    def walk(o, path, depth):
        if budget[0] <= 0 or depth > depth_max:
            return
        budget[0] -= 1
        nodes.append(node_dict(o, path, want_text))
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, fanout)):
            try:
                walk(o.get_child_at_index(j), path + "/" + str(j), depth + 1)
            except Exception:
                pass

    base = str(app) if app else "desktop"
    walk(root, base, 0)
    return {"ok": True, "tier": 0, "app": app, "count": len(nodes), "nodes": nodes}

def _resolve_target_node(req):

    return resolve_by_role_name(
        req.get("app"),
        want_role=req.get("role"),
        want_name=req.get("name"),
        want_editable=bool(req.get("editable", False)),
    )

_GEHEIM_WOERTER = (
    "password", "passwort", "passphrase", "kennwort", "pin", "token", "secret", "geheim",
    "credential", "zugangsdaten", "api key", "api-key", "apikey", "schluessel", "schlüssel",
    "private key", "seed", "recovery", "wiederherstellung", "otp", "2fa", "mfa", "cvv", "cvc",
)

def _attribut(o, schluessel):

    try:
        at = o.get_attributes()
    except Exception:
        return ""
    try:
        if isinstance(at, dict):
            return str(at.get(schluessel) or "")

        for eintrag in (at or []):
            s = str(eintrag)
            if s.lower().startswith(schluessel.lower() + ":"):
                return s.split(":", 1)[1]
    except Exception:
        pass
    return ""

def _secure_node(o):

    try:

        if "password" in (o.get_role_name() or "").lower():
            return True
        beschriftung = (o.get_name() or "").lower()
        if any(w in beschriftung for w in _GEHEIM_WOERTER):
            return True
        if "password" in _attribut(o, "text-input-type").lower():
            return True
        return False
    except Exception:
        return True

_SECURE_ERR = "secure field: acting on password nodes is blocked"

def v_invoke(req):

    o = _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        record("invoke", 1, tgt, {"action": req.get("action")}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("invoke", 1, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    want = req.get("action")
    acts = actions(o)
    if not any(acts):
        record("invoke", 1, tgt, {"action": want, "reason": "no actions"}, False)
        return {"ok": False, "error": "object exposes no AT-SPI actions; "
                "consider press_keys (Tier 2)", "target": tgt, "actions": acts}
    idx = None
    if want is None:
        idx = 0
    else:
        for i, an in enumerate(acts):
            if (str(want).isdigit() and int(want) == i) or str(want).lower() in an.lower():
                idx = i
                break
    if idx is None:
        record("invoke", 1, tgt, {"action": want, "reason": "action not found"}, False)
        return {"ok": False, "error": "action not found", "want": want, "actions": acts}
    try:
        res = bool(o.do_action(idx))
    except Exception as e:
        record("invoke", 1, tgt, {"action": acts[idx], "exc": str(e)}, False)
        return {"ok": False, "error": f"do_action: {e}", "target": tgt}
    record("invoke", 1, tgt, {"action": acts[idx], "index": idx}, res)
    return {"ok": res, "tier": 1, "did": acts[idx], "index": idx, "target": tgt}

def v_set_value(req):

    o = _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    val = req.get("value")
    if o is None:
        record("set_value", 1, tgt, {"value": val}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("set_value", 1, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    try:
        o.set_current_value(float(val))
        cur = o.get_current_value()
        record("set_value", 1, tgt, {"value": val}, True)
        return {"ok": True, "tier": 1, "value": cur, "target": tgt}
    except Exception as e:
        record("set_value", 1, tgt, {"value": val, "exc": str(e)}, False)
        return {"ok": False, "error": f"set_value: {e}", "target": tgt}

def v_insert_text(req):

    o = _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    text = req.get("text", "")
    if o is None:
        record("insert_text", 1, tgt, {"len": len(text)}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("insert_text", 1, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    try:
        if o.set_text_contents(text):
            record("insert_text", 1, tgt, {"len": len(text), "method": "set"}, True)
            return {"ok": True, "tier": 1, "method": "set_text_contents", "target": tgt}
    except Exception:
        pass
    try:
        o.insert_text(0, text, len(text))
        record("insert_text", 1, tgt, {"len": len(text), "method": "insert"}, True)
        return {"ok": True, "tier": 1, "method": "insert_text", "target": tgt}
    except Exception as e:
        record("insert_text", 1, tgt, {"len": len(text), "exc": str(e)}, False)
        return {"ok": False, "error": f"insert_text: {e}", "target": tgt}

def v_press_keys(req):

    app = req.get("app")
    target = req.get("target") or phantom_target_for_app(app)
    if target is None:

        sysmode = True
    else:
        sysmode = False
    text = req.get("text")
    key = req.get("key")
    enter = bool(req.get("enter", False))
    tgt = {"app": app, "target": target}
    if text is not None:
        cmd = (f"kbd type {text}" if sysmode else f"act {target} type {text}")
    elif key is not None:
        cmd = (f"kbd key {key}" if sysmode else f"act {target} key {key}")
    elif enter:
        cmd = ("kbd enter" if sysmode else f"act {target} enter")
    else:
        record("press_keys", 2, tgt, {}, False)
        return {"ok": False, "error": "press_keys needs text|key|enter"}
    rep = phantom_ctl(cmd)
    ok = not rep.startswith("__PN_CTL_ERROR__") and "error" not in rep.lower()
    record("press_keys", 2, tgt, {"cmd": cmd.split(" ", 2)[:2]}, ok)
    return {"ok": ok, "tier": 2, "via": "compositor", "cmd": cmd, "reply": rep.strip()}

def v_click_at(req):

    o = _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        record("click_at", 3, tgt, {}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("click_at", 3, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    allowed = allowed_actions(o)
    higher = [a for a in allowed if a != "click_at"]
    if higher and not req.get("force"):

        record("click_at", 3, tgt, {"refused": True, "higher": higher}, False)
        return {
            "ok": False,
            "refused": "ladder",
            "error": "click_at is Tier 3 (LAST RESORT); this object has higher-tier "
                     f"affordances {higher} — use those (e.g. invoke). Pass "
                     "force=true only for a true pixel-only target.",
            "allowed_actions": allowed,
            "target": tgt,
        }
    box = extents(o)
    if not box:
        record("click_at", 3, tgt, {"reason": "no extents"}, False)
        return {"ok": False, "error": "object has no extents box to click", "target": tgt}
    cx = box["x"] + box["w"] // 2
    cy = box["y"] + box["h"] // 2
    target = req.get("target") or phantom_target_for_app(req.get("app"))
    if target is None:
        record("click_at", 3, tgt, {"box": box, "reason": "no phantom window"}, False)
        return {"ok": False, "error": "no phantom window maps to this app; cannot "
                "forge a pointer click", "box": box, "target": tgt}

    cmd = f"act {target} click {cx} {cy}"
    rep = phantom_ctl(cmd)
    ok = not rep.startswith("__PN_CTL_ERROR__") and "error" not in rep.lower()
    record("click_at", 3, tgt, {"box": box, "at": [cx, cy], "forced": bool(req.get("force"))}, ok)
    return {"ok": ok, "tier": 3, "via": "act_pointer", "at": [cx, cy], "box": box,
            "cmd": cmd, "reply": rep.strip()}

def v_clients(req):

    return {"ok": True, "clients": phantom_clients()}

def v_apps(req):

    d = desktop()
    out = []
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        out.append({"idx": i, "name": name(a), "role": role(a),
                    "children": a.get_child_count()})
    return {"ok": True, "apps": out}

def mcp_tool_schema(req):

    tools = [
        {"name": "read_tree", "tier": 0, "args": ["app?"],
         "desc": "Semantic object model of an app (sense)."},
        {"name": "invoke", "tier": 1, "args": ["app", "role", "name", "action?"],
         "desc": "DEFAULT act path: AT-SPI DoAction by role+name."},
        {"name": "set_value", "tier": 1, "args": ["app", "role", "name", "value"],
         "desc": "AT-SPI Value interface."},
        {"name": "insert_text", "tier": 1, "args": ["app", "role", "name", "text"],
         "desc": "AT-SPI editable Text interface."},
        {"name": "press_keys", "tier": 2, "args": ["app?", "target?", "text|key|enter"],
         "desc": "Virtual keyboard via the compositor."},
        {"name": "click_at", "tier": 3, "args": ["app", "role", "name", "force?"],
         "desc": "LAST RESORT: GetExtents box -> forged pointer click. Refused if a "
                 "higher tier exists."},
    ]
    return {"ok": True, "mcp_tools": tools,
            "note": "rmcp/stdio shim: 1 MCP call -> 1 JSON request to this socket -> "
                    "reply is the tool result. Ladder+record enforced server-side."}

VERBS = {
    "read_tree": v_read_tree,
    "invoke": v_invoke,
    "set_value": v_set_value,
    "insert_text": v_insert_text,
    "press_keys": v_press_keys,
    "click_at": v_click_at,
    "clients": v_clients,
    "apps": v_apps,
    "mcp_schema": mcp_tool_schema,
    "ping": lambda req: {"ok": True, "pong": True},
}

def handle(req):
    verb = req.get("verb")
    fn = VERBS.get(verb)
    if fn is None:
        return {"ok": False, "error": f"unknown verb {verb!r}",
                "verbs": sorted(VERBS.keys())}
    try:
        return fn(req)
    except Exception as e:
        return {"ok": False, "error": f"engine: {e}",
                "trace": traceback.format_exc().splitlines()[-3:]}

def sock_path():
    return os.environ.get("PN_ATSPID_SOCK") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "pn-atspid.sock")

def serve():
    path = sock_path()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(16)
    sys.stderr.write(f"pn-atspid: listening on {path} (ctl={_ctl_path()})\n")
    sys.stderr.flush()
    while True:
        try:
            conn, _ = srv.accept()
        except KeyboardInterrupt:
            break
        try:
            conn.settimeout(15.0)
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 8 * 1024 * 1024:
                    break
            line = buf.split(b"\n", 1)[0]
            if not line.strip():
                conn.close()
                continue
            try:
                req = json.loads(line.decode("utf-8"))
            except Exception as e:
                conn.sendall((json.dumps({"ok": False, "error": f"bad json: {e}"}) + "\n").encode())
                conn.close()
                continue
            reply = handle(req)
            conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode())
        except Exception as e:
            try:
                conn.sendall((json.dumps({"ok": False, "error": f"server: {e}"}) + "\n").encode())
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

def main():

    if len(sys.argv) >= 2 and sys.argv[1] == "call":
        req = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.loads(sys.stdin.read())
        sys.stdout.write(json.dumps(handle(req), ensure_ascii=False) + "\n")
        return
    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return
    serve()

if __name__ == "__main__":
    main()
