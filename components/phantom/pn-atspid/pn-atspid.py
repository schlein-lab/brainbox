#!/usr/bin/env python3
import collections
import json
import os
import re
import socket
import subprocess
import sys
import threading
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

_START = time.time()


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
    d["id"] = _ziel_merken(None if path.startswith("desktop") else path.split("/", 1)[0],
                           path, d["role"], d["name"])
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
    notnagel = None
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
            box = extents(o)
            if "showing" in states(o) and box and box.get("w", 0) > 0:
                return o
            if notnagel is None:
                notnagel = o
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 250)):
            try:
                stack.append(o.get_child_at_index(j))
            except Exception:
                pass
    return notnagel

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
        zusatz = (" (KANAL-urteil, kein gate: die app kann noch arbeiten/"
                  "fertig sein — ENDZUSTAND pruefen, z.B. per uno_lesen)"
                  if "timed out" in str(e) else "")
        return f"__PN_CTL_ERROR__ {e}{zusatz}"

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
    try:
        _verlauf_schreiben("verb", {"verb": verb, "tier": tier,
                                    "ziel": str(target)[:80], "ok": bool(ok)})
    except Exception:
        pass


def _hat_ctl():
    try:
        return os.path.exists(_ctl_path())
    except Exception:
        return False


def _xdotool(*args, **kw):
    import subprocess
    exe = os.environ.get("PN_XDOTOOL") or "xdotool"
    for kand in (exe, "/opt/kits/phantom/opt/bin/xdotool", "/usr/bin/xdotool"):
        if kand == exe or os.path.exists(kand):
            try:
                r = subprocess.run([kand] + [str(a) for a in args],
                                   capture_output=True, text=True, timeout=kw.get("timeout", 20))
                return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
            except FileNotFoundError:
                continue
            except Exception as exc:
                return 1, "", repr(exc)
    return 1, "", "xdotool nicht gefunden"


def x11_type(text):
    rc, _o, e = _xdotool("type", "--clearmodifiers", "--delay", "20", text)
    return rc == 0, e


def x11_key(key):
    rc, _o, e = _xdotool("key", "--clearmodifiers", str(key))
    return rc == 0, e


def x11_click(x, y):
    _xdotool("mousemove", int(x), int(y))
    rc, _o, e = _xdotool("click", "1")
    return rc == 0, e


def knoten_text(o):
    try:
        return read_text(o) or ""
    except Exception:
        return ""


def _ziel_auswahlkasten(req):
    if req.get("_knoten") is not None:
        return req["_knoten"]
    for rolle in ("combo box", "list box"):
        o = resolve_by_role_name(req.get("app"), rolle, req.get("name"))
        if o is not None:
            return o
    b = _resolve_target_node(req)
    if b is None:
        return None
    ziel = _feld_der_beschriftung(b, verlangt_editable=False)
    return ziel if ziel is not None else b


def _feld_der_beschriftung(o, verlangt_editable=True):
    try:
        for rel in o.get_relation_set() or []:
            if rel.get_relation_type() != Atspi.RelationType.LABEL_FOR:
                continue
            for i in range(rel.get_n_targets()):
                ziel = rel.get_target(i)
                if ziel is None:
                    continue
                if verlangt_editable and "editable" not in states(ziel):
                    continue
                return ziel
    except Exception:
        pass
    return None


def _ziel_beschreibbar(req):
    if req.get("_knoten") is not None:
        return req["_knoten"]
    o = resolve_by_role_name(req.get("app"), req.get("role"), req.get("name"),
                             want_editable=True)
    if o is not None:
        return o
    beschriftung = _resolve_target_node(req)
    if beschriftung is not None:
        feld = _feld_der_beschriftung(beschriftung)
        if feld is not None:
            return feld
    return beschriftung


def v_grab_focus(req):
    o = _ziel_beschreibbar(req) if req.get("editable") else _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        record("grab_focus", 1, tgt, {}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    try:
        ok = bool(o.grab_focus())
    except Exception as e:
        record("grab_focus", 1, tgt, {"exc": str(e)}, False)
        return {"ok": False, "error": "grab_focus: %s" % e, "target": tgt}
    record("grab_focus", 1, tgt, {}, ok)
    return {"ok": ok, "tier": 1, "target": tgt}


def v_read_text(req):
    o = _ziel_beschreibbar(req) if req.get("editable") else _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        return {"ok": False, "error": "target not found", "target": tgt}
    return {"ok": True, "tier": 0, "text": knoten_text(o), "target": tgt}

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

    if req.get("_knoten") is not None:
        return req["_knoten"]
    return resolve_by_role_name(
        req.get("app"),
        want_role=req.get("role"),
        want_name=req.get("name"),
        want_editable=bool(req.get("editable", False)),
    )

_GEHEIM_INLINE = (
    "password", "passwort", "passphrase", "kennwort", "pin", "token", "secret", "geheim",
    "credential", "zugangsdaten", "api key", "api-key", "apikey", "schluessel", "schlüssel",
    "private key", "seed", "recovery", "wiederherstellung", "otp", "2fa", "mfa", "cvv", "cvc",
)


def _lade_geheim_woerter():
    pfad = os.environ.get("PHANTOM_GEHEIM_DATEI") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "geheim_woerter.txt")
    try:
        with open(pfad, encoding="utf-8") as f:
            woerter = tuple(
                z.strip().lower() for z in f
                if z.strip() and not z.strip().startswith("#"))
        if len(woerter) >= 10:
            return woerter
    except OSError:
        pass
    return _GEHEIM_INLINE


_GEHEIM_WOERTER = _lade_geheim_woerter()

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


_GATE_ZUSTAENDE = ("showing", "visible", "enabled", "sensitive")
_APP_ERFOLG = {}


def _app_schluessel(token):
    return str(token or "").lower()


def _probe_app(token, frist_s=2.0):
    ergebnis = {}

    def _lauf():
        try:
            a = app_by(token)
            ergebnis["da"] = a is not None
            ergebnis["name"] = name(a) if a is not None else None
        except Exception as e:
            ergebnis["fehler"] = str(e)

    t = threading.Thread(target=_lauf, daemon=True)
    t0 = time.time()
    t.start()
    t.join(min(max(float(frist_s or 2.0), 0.2), 15.0))
    dauer_ms = int((time.time() - t0) * 1000)
    antwortet = (not t.is_alive()) and "fehler" not in ergebnis
    return {"antwortet": antwortet, "dauer_ms": dauer_ms,
            "gefunden": ergebnis.get("da"), "fehler": ergebnis.get("fehler")}


def _eingefroren_zeile(token, probe):
    seit = _APP_ERFOLG.get(_app_schluessel(token))
    if seit:
        haengt = round(time.time() - seit, 1)
    else:
        haengt = round(probe["dauer_ms"] / 1000.0, 1)
    return {"ereignis": "eingefroren", "app": str(token or "?"),
            "haengt_seit_s": haengt,
            "naechster_schritt": "prozess neu starten; phantom up stellt die "
                                 "ebene wieder her"}


def v_probe(req):
    token = req.get("app")
    p = _probe_app(token, req.get("frist", 2.0))
    if p["antwortet"]:
        _APP_ERFOLG[_app_schluessel(token)] = time.time()
        return {"ok": True, "tier": 0, "app": token, "antwortet": True,
                "gefunden": p["gefunden"], "dauer_ms": p["dauer_ms"]}
    z = _eingefroren_zeile(token, p)
    _melde(z)
    return {"ok": True, "tier": 0, "app": token, "antwortet": False,
            "dauer_ms": p["dauer_ms"], "fehler": p.get("fehler"),
            "eingefroren": z}


def _gate_bedienbar(o, req, tgt, verb, tier):
    try:
        frist = float(req.get("frist", 3.0))
    except Exception:
        frist = 3.0
    frist = min(max(frist, 0.2), 30.0)
    ende = time.time() + frist
    vorher, vorher_ts = None, 0.0
    fehlt, box = [], None
    while True:
        st = states(o)
        fehlt = [z for z in _GATE_ZUSTAENDE if z not in st]
        box = extents(o)
        jetzt = time.time()
        if not fehlt and box and box["w"] > 0 and box["h"] > 0:
            if vorher == box and jetzt - vorher_ts >= 0.08:
                _APP_ERFOLG[_app_schluessel(req.get("app"))] = jetzt
                return None
            if vorher != box:
                vorher, vorher_ts = box, jetzt
        else:
            vorher = None
        if jetzt >= ende:
            break
        time.sleep(0.1)
    if fehlt:
        grund = "not " + fehlt[0].upper()
    elif not box or box["w"] <= 0 or box["h"] <= 0:
        grund = "empty extents"
    else:
        grund = "extents unstable"
    antwort = {"ok": False, "gate": grund, "states": states(o), "extents": box,
               "frist_s": frist, "target": tgt}
    _steck_strike(req.get("app"), tgt.get("name") or tgt.get("role"), grund)
    probe = _probe_app(req.get("app"), 2.0)
    if not probe["antwortet"]:
        z = _eingefroren_zeile(req.get("app"), probe)
        antwort["eingefroren"] = z
        _melde(z)
    else:
        _APP_ERFOLG[_app_schluessel(req.get("app"))] = time.time()
    record(verb, tier, tgt, {"gate": grund}, False)
    return antwort


def _eintraege(o, tiefe=0):
    try:
        n = o.get_child_count()
    except Exception:
        return
    for i in range(min(n, 200)):
        try:
            c = o.get_child_at_index(i)
        except Exception:
            continue
        if c is None:
            continue
        yield o, i, c
        if tiefe < 2:
            for t in _eintraege(c, tiefe + 1):
                yield t


def v_select_option(req):
    o = _ziel_auswahlkasten(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    gesucht = str(req.get("option") or "").strip().lower()
    if not gesucht:
        return {"ok": False, "error": "option fehlt", "target": tgt}
    tor = _gate_bedienbar(o, req, tgt, "select_option", 1)
    if tor is not None:
        if not any(True for _ in _popup_optionen(req.get("app"))):
            return tor

    gefunden = []
    for elter, idx, kind in _eintraege(o):
        nm = name(kind)
        gefunden.append(nm)
        if gesucht not in nm.lower():
            continue
        versucht = []
        try:
            if any(actions(kind)):
                kind.do_action(0)
                versucht.append("aktion")
        except Exception as exc:
            versucht.append("aktion-fehler:%r" % exc)
        if not versucht:
            try:
                sel = elter.get_selection_iface()
                if sel is not None and sel.select_child(idx):
                    versucht.append("selection")
            except Exception:
                pass

        gewaehlt_ist = None
        try:
            for _e, _i, k2 in _eintraege(o):
                if "selected" in states(k2):
                    gewaehlt_ist = name(k2)
                    break
        except Exception:
            pass
        bestaetigt = bool(gewaehlt_ist and gesucht in gewaehlt_ist.lower())
        record("select_option", 1, tgt, {"option": nm, "weg": versucht}, bestaetigt)
        if bestaetigt:
            return {"ok": True, "tier": 1, "weg": versucht, "gewaehlt": nm}
        return {"ok": False, "weg": versucht, "gewuenscht": nm,
                "jetzt_gewaehlt": gewaehlt_ist,
                "error": "die Auswahl ist NICHT angekommen — die Antwort des schreibenden "
                         "Kanals wurde nicht bestaetigt",
                "naechster_schritt": "phantom invoke %s '%s' und danach `phantom type` "
                                     "(Typeahead) — oder `phantom sicht` zum Nachsehen"
                                     % (str(req.get("app")), nm)}
    popup_namen = []
    for kind in _popup_optionen(req.get("app")):
        nm = name(kind)
        popup_namen.append(nm)
        if gesucht not in nm.lower():
            continue
        versucht = []
        try:
            if any(actions(kind)):
                kind.do_action(0)
                versucht.append("popup-aktion")
        except Exception as exc:
            versucht.append("popup-aktion-fehler:%r" % exc)
        if not versucht and _eltern_selektion(kind):
            versucht.append("popup-selection")
        bestaetigt = False
        try:
            bestaetigt = "selected" in states(kind)
        except Exception:
            pass
        record("select_option", 1, tgt,
               {"option": nm, "weg": versucht, "quelle": "popup"}, bool(versucht))
        if versucht:
            return {"ok": True, "tier": 1, "weg": versucht, "gewaehlt": nm,
                    "quelle": "offenes popup-fenster",
                    "hinweis": ("selected bestaetigt" if bestaetigt else
                                "zustand nicht gegenlesbar — ENDZUSTAND pruefen "
                                "(anzeige/uno), popup ggf. mit popup-zu raeumen")}
    return {"ok": False, "error": "Option nicht gefunden",
            "gesucht": req.get("option"),
            "vorhanden": ([g for g in gefunden if g][:20] +
                          [p for p in popup_namen if p][:10]),
            "popup_durchsucht": bool(popup_namen),
            "target": tgt}


def _popup_optionen(app_token):
    root = app_by(app_token)
    if root is None:
        return
    try:
        n = root.get_child_count()
    except Exception:
        return
    for i in range(min(n, 30)):
        try:
            f = root.get_child_at_index(i)
        except Exception:
            continue
        if f is None or role(f) not in ("window", "frame"):
            continue
        try:
            if "showing" not in states(f) or name(f):
                continue
        except Exception:
            continue
        stapel = [f]
        budget = 0
        while stapel and budget < 3000:
            o = stapel.pop(0)
            budget += 1
            if role(o) in ("table cell", "list item", "menu item") and name(o):
                yield o
            try:
                for j in range(min(o.get_child_count(), 200)):
                    k = o.get_child_at_index(j)
                    if k is not None:
                        stapel.append(k)
            except Exception:
                pass


def _eltern_selektion(o):
    try:
        eltern = o.get_parent()
        idx = o.get_index_in_parent()
    except Exception:
        return False
    if eltern is None or idx is None or idx < 0:
        return False
    for weg in (lambda: Atspi.Selection.select_child(eltern, idx),
                lambda: eltern.select_child(idx)):
        try:
            if weg():
                return True
        except Exception:
            continue
    return False


def v_invoke(req):

    o = _resolve_target_node(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    if o is None:
        record("invoke", 1, tgt, {"action": req.get("action")}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("invoke", 1, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    tor = _gate_bedienbar(o, req, tgt, "invoke", 1)
    if tor is not None:
        return tor
    want = req.get("action")
    acts = actions(o)
    if not any(acts):
        if "selectable" in states(o) or role(o) in (
                "page tab", "list item", "table row", "tree item"):
            if _eltern_selektion(o):
                bestaetigt = "selected" in states(o)
                record("invoke", 1, tgt,
                       {"action": "selection", "weg": "eltern"}, True)
                return {"ok": True, "tier": 1, "did": "selection:eltern",
                        "bestaetigt": bestaetigt, "target": tgt}
        box = extents(o)
        record("invoke", 1, tgt, {"action": want, "reason": "no actions"}, False)
        antwort = {"ok": False, "error": "object exposes no AT-SPI actions; "
                   "consider press_keys (Tier 2)", "target": tgt, "actions": acts}
        if box and box.get("w", 0) > 0:
            antwort["ausweich"] = {
                "klick": [box["x"] + box["w"] // 2, box["y"] + box["h"] // 2],
                "hinweis": "keine AT-SPI-Action; Ausweichweg: act-Klick auf "
                           "diese Bildschirm-Mitte"}
        return antwort
    idx = None
    if want is None:
        nur_klapp = bool(acts) and all(
            ("expand" in an.lower() or "contract" in an.lower())
            for an in acts if an)
        if nur_klapp and ("selectable" in states(o) or role(o) in (
                "list item", "tree item", "table row", "page tab")):
            if _eltern_selektion(o):
                bestaetigt = "selected" in states(o)
                record("invoke", 1, tgt,
                       {"action": "selection", "weg": "eltern-statt-klapp"}, True)
                return {"ok": True, "tier": 1, "did": "selection:eltern",
                        "bestaetigt": bestaetigt, "target": tgt,
                        "hinweis": "eintrag trug nur expand/contract — "
                                   "selektion via eltern; klappen: action='expand'"}
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

    o = _ziel_beschreibbar(req)
    tgt = {"app": req.get("app"), "role": req.get("role"), "name": req.get("name")}
    text = req.get("text", "")
    if o is None:
        record("insert_text", 1, tgt, {"len": len(text)}, False)
        return {"ok": False, "error": "target not found", "target": tgt}
    if _secure_node(o):
        record("insert_text", 1, tgt, {"reason": "secure field"}, False)
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    tor = _gate_bedienbar(o, req, tgt, "insert_text", 1)
    if tor is not None:
        return tor
    versuche = []
    for wie in ("set", "insert"):
        try:
            if wie == "set":
                if not o.set_text_contents(text):
                    versuche.append("set:false")
                    continue
            else:
                o.insert_text(0, text, len(text))
        except Exception as e:
            versuche.append("%s:%s" % (wie, e))
            continue
        ist = knoten_text(o)
        if text in (ist or ""):
            record("insert_text", 1, tgt, {"len": len(text), "method": wie}, True)
            return {"ok": True, "tier": 1, "method": wie, "applied": True,
                    "text": ist, "target": tgt}
        versuche.append("%s:nicht angekommen" % wie)
    record("insert_text", 1, tgt, {"len": len(text), "versuche": versuche}, False)
    return {"ok": False, "applied": False, "tier": 1, "versuche": versuche,
            "text": knoten_text(o), "target": tgt,
            "error": "die Anwendung nahm den Text ueber AT-SPI nicht an — "
                     "grab_focus + press_keys ist hier der Weg"}

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
    if not _hat_ctl():
        if text is not None:
            ok, err = x11_type(text)
        elif key is not None:
            ok, err = x11_key(key)
        else:
            ok, err = x11_key("Return")
        record("press_keys", 2, tgt, {"via": "xtest"}, ok)
        return {"ok": ok, "tier": 2, "via": "xtest", "error": (err or None) if not ok else None}
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
    tor = _gate_bedienbar(o, req, tgt, "click_at", 3)
    if tor is not None:
        return tor
    box = extents(o)
    if not box:
        record("click_at", 3, tgt, {"reason": "no extents"}, False)
        return {"ok": False, "error": "object has no extents box to click", "target": tgt}
    cx = box["x"] + box["w"] // 2
    cy = box["y"] + box["h"] // 2
    if not _hat_ctl():
        ok, err = x11_click(cx, cy)
        record("click_at", 3, tgt, {"box": box, "at": [cx, cy], "via": "xtest"}, ok)
        return {"ok": ok, "tier": 3, "via": "xtest", "at": [cx, cy], "box": box,
                "target": tgt, "error": (err or None) if not ok else None}
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
    tote = 0
    for i in range(d.get_child_count()):
        try:
            a = d.get_child_at_index(i)
            if a is None:
                tote += 1
                continue
            out.append({"idx": i, "name": name(a), "role": role(a),
                        "children": a.get_child_count()})
        except Exception:
            tote += 1
    antwort = {"ok": True, "apps": out}
    if tote:
        antwort["tote_eintraege"] = tote
    return antwort


_ABON_MAX = 512
_ABON_LOCK = threading.Lock()
_ABONNENTEN = []


class _Abonnent:

    def __init__(self, conn, filt, roh):
        self.conn = conn
        self.filt = set(filt) if filt else None
        self.roh = bool(roh)
        self.zeilen = collections.deque()
        self.cv = threading.Condition()
        self.lebt = True
        self.verloren = 0

    def lege_hin(self, zeile):
        with self.cv:
            if not self.lebt:
                return
            if len(self.zeilen) >= _ABON_MAX:
                self.zeilen.popleft()
                self.verloren += 1
            self.zeilen.append(zeile)
            self.cv.notify()

    def sende_schleife(self):
        import select
        letzter = time.time()
        try:
            self.conn.settimeout(20.0)
            while True:
                zeile = None
                with self.cv:
                    while self.lebt and not self.zeilen:
                        self.cv.wait(timeout=5.0)
                        if self.zeilen or not self.lebt:
                            break
                        r, _w, _x = select.select([self.conn], [], [], 0)
                        if r and not self.conn.recv(4096):
                            raise ConnectionResetError("leser weg")
                        if time.time() - letzter >= 60.0:
                            break
                    if not self.lebt and not self.zeilen:
                        break
                    if self.zeilen:
                        zeile = self.zeilen.popleft()
                    drops, self.verloren = self.verloren, 0
                if zeile is None:
                    zeile = {"meta": "puls", "ts": round(time.time(), 1)}
                if drops:
                    self.conn.sendall((json.dumps(
                        {"meta": "drops", "verloren": drops}) + "\n").encode())
                self.conn.sendall(
                    (json.dumps(zeile, ensure_ascii=False) + "\n").encode())
                letzter = time.time()
        except Exception:
            pass
        finally:
            self.schliessen()
            _abo_austragen(self)

    def schliessen(self):
        with self.cv:
            self.lebt = False
            self.cv.notify()
        try:
            self.conn.close()
        except Exception:
            pass


_WELTPULS = {"n": 0, "halter": 0}


def _puls_anfordern():
    with _ABON_LOCK:
        _WELTPULS["halter"] += 1
    err = _listener_sicherstellen()
    if err is not None:
        with _ABON_LOCK:
            _WELTPULS["halter"] = max(0, _WELTPULS["halter"] - 1)
        return False
    return True


def _puls_freigeben():
    with _ABON_LOCK:
        _WELTPULS["halter"] = max(0, _WELTPULS["halter"] - 1)
        leer = not _ABONNENTEN and _WELTPULS["halter"] == 0
    if leer:
        _listener_stoppen()


def _abonnenten_da():
    with _ABON_LOCK:
        return bool(_ABONNENTEN) or _WELTPULS["halter"] > 0


def _abo_austragen(ab):
    with _ABON_LOCK:
        if ab in _ABONNENTEN:
            _ABONNENTEN.remove(ab)
        leer = not _ABONNENTEN and _WELTPULS["halter"] == 0
    if leer:
        _listener_stoppen()


def _melde(zeile, roh_nur=False):
    if not roh_nur and zeile.get("ereignis") in _NOTIZ_KLASSEN:
        _hub_notiz(zeile)
    with _ABON_LOCK:
        abos = list(_ABONNENTEN)
    for ab in abos:
        if roh_nur and not ab.roh:
            continue
        if ab.filt is not None and "meta" not in zeile \
                and zeile.get("ereignis") not in ab.filt:
            continue
        ab.lege_hin(dict(zeile))


_LISTENER_LOCK = threading.Lock()
_LISTENER = {"laeuft": False, "hoerer": None, "arten": []}
_EREIGNIS_ARTEN = (
    "window:create", "window:destroy", "window:activate", "window:deactivate",
    "object:state-changed", "object:children-changed", "object:text-changed",
    "object:property-change", "document:load-complete", "focus:",
)


def _listener_sicherstellen():
    with _LISTENER_LOCK:
        if _LISTENER["laeuft"]:
            return None
        try:
            hoerer = Atspi.EventListener.new(_ereignis_rein)
            arten = []
            for art in _EREIGNIS_ARTEN:
                try:
                    if hoerer.register(art):
                        arten.append(art)
                except Exception:
                    pass
            if not arten:
                return "keine Ereignisklasse registrierbar (Registry tot?)"
            _LISTENER.update(laeuft=True, hoerer=hoerer, arten=arten)
        except Exception as e:
            return "listener: %s" % e
    threading.Thread(target=_listener_lauf, daemon=True).start()
    return None


def _listener_lauf():
    from gi.repository import GLib
    GLib.timeout_add_seconds(30, _tick_unsichtbar)
    try:
        Atspi.event_main()
    except Exception as e:
        sys.stderr.write("pn-atspid: listener endete unerwartet: %s\n" % e)
        sys.stderr.flush()
    finally:
        with _LISTENER_LOCK:
            _LISTENER["laeuft"] = False
        _melde({"meta": "listener-ende", "ts": round(time.time(), 1)})


def _listener_stoppen():
    with _LISTENER_LOCK:
        if not _LISTENER["laeuft"]:
            return
        _LISTENER["laeuft"] = False
        hoerer, arten = _LISTENER["hoerer"], _LISTENER["arten"]
        _LISTENER.update(hoerer=None, arten=[])
    for art in arten:
        try:
            hoerer.deregister(art)
        except Exception:
            pass
    try:
        Atspi.event_quit()
    except Exception:
        pass


def _abo_annehmen(conn, req):
    err = _listener_sicherstellen()
    if err is not None:
        try:
            conn.sendall((json.dumps({"ok": False, "error": err}) + "\n").encode())
            conn.close()
        except Exception:
            pass
        return
    ab = _Abonnent(conn, req.get("filter"), req.get("roh"))
    with _ABON_LOCK:
        _ABONNENTEN.append(ab)
    ab.lege_hin({"ok": True, "meta": "abo",
                 "ereignisse": list(_LISTENER["arten"]), "roh": ab.roh,
                 "schlange": _ABON_MAX,
                 "hinweis": "JSON-Zeilen; bei Ueberlauf drop-oldest + Meta-Zeile"})
    threading.Thread(target=ab.sende_schleife, daemon=True).start()


def v_subscribe_hinweis(req):
    return {"ok": False, "error": "subscribe streamt JSON-Zeilen und braucht die "
            "Daemon-Verbindung -- im call-Modus nicht verfuegbar",
            "hinweis": "an den Sockel verbinden und {\"verb\":\"subscribe\"} senden"}


def _app_von(o):
    try:
        a = o.get_application()
        return name(a) or "?"
    except Exception:
        return "?"


def _ereignis_rein(ev):
    try:
        _WELTPULS["n"] += 1
        if not _abonnenten_da():
            return
        typ = str(ev.type or "")
        quelle = ev.source
        app = _app_von(quelle)
        if typ.startswith("window:create") or typ.startswith("object:children-changed"):
            for kand in (quelle, getattr(ev, "any_data", None)):
                z = _dialog_zeile(kand, app)
                if z:
                    _melde(z)
                    break
        if typ.startswith(("object:children-changed", "document:load-complete")):
            _karte_anstossen(app)
        if typ.startswith("object:text-changed"):
            _SCHRIFT_DIRTY.add((app or "").lower())
        if typ.startswith("window:"):
            _unsichtbar_pruefen(5.0)
        zeile = {"ereignis": "roh", "typ": typ, "app": app,
                 "rolle": role(quelle), "name": name(quelle),
                 "ts": round(time.time(), 3)}
        if typ.startswith(("window:", "document:load-complete", "focus")):
            _melde(zeile)
        else:
            if typ.startswith(("object:text-changed", "object:property-change")):
                if _secure_node(quelle):
                    zeile["inhalt"] = "<geschwaerzt>"
                else:
                    inhalt = getattr(ev, "any_data", None)
                    if isinstance(inhalt, str) and inhalt:
                        zeile["inhalt"] = inhalt[:200]
            _melde(zeile, roh_nur=True)
    except Exception:
        pass


_DIALOG_ROLLEN = ("dialog", "alert", "file chooser", "notification")
_FEHLER_WOERTER = ("error", "fehler", "failed", "fehlgeschlagen", "schlug fehl",
                   "konnte nicht", "denied", "verweigert")
_DIALOGE_GEMELDET = {}


def _fehler_schwere(rolle, wortlaut):
    w = (wortlaut or "").lower()
    if any(x in w for x in _FEHLER_WOERTER):
        return "fehler"
    if "warnung" in w or "warning" in w:
        return "warnung"
    if rolle == "alert":
        return "warnung"
    if rolle == "notification":
        return "hinweis"
    return None


def _dialog_zeile(o, app):
    if o is None:
        return None
    rolle = role(o)
    if rolle not in _DIALOG_ROLLEN:
        return None
    titel = name(o)
    jetzt = time.time()
    schl = (app, titel or rolle)
    if jetzt - _DIALOGE_GEMELDET.get(schl, 0) < 10.0:
        return None
    _DIALOGE_GEMELDET[schl] = jetzt
    if len(_DIALOGE_GEMELDET) > 64:
        for k in [k for k, t in _DIALOGE_GEMELDET.items() if jetzt - t > 300]:
            _DIALOGE_GEMELDET.pop(k, None)
    texte, knoepfe = [], []
    stapel = [(o, 0)]
    budget = 200
    while stapel and budget > 0:
        k, tiefe = stapel.pop()
        budget -= 1
        r = role(k)
        nm = name(k)
        if r in ("label", "paragraph", "static", "text") \
                and "editable" not in states(k):
            if _secure_node(k):
                texte.append("<geschwaerzt>")
            else:
                t = read_text(k) or nm
                if t:
                    texte.append(t[:200])
        elif nm and any(actions(k)):
            knoepfe.append({"name": nm,
                            "befehl": "phantom invoke %s '%s'" % (app, nm)})
        if tiefe < 4:
            try:
                cc = k.get_child_count()
            except Exception:
                cc = 0
            for j in range(min(cc, 60)):
                try:
                    stapel.append((k.get_child_at_index(j), tiefe + 1))
                except Exception:
                    pass
    text = " ".join(dict.fromkeys(texte))[:600]
    zeile = {"ereignis": "dialog", "app": app, "titel": titel, "text": text,
             "knoepfe": knoepfe[:12]}
    schwere = _fehler_schwere(rolle, "%s %s" % (titel, text))
    if schwere:
        zeile["ereignis"] = "fehler"
        zeile["schwere"] = schwere
    return zeile


_UNSICHTBAR = {"zuletzt": 0.0, "vermisst": {}, "gemeldet": {}}


def _unsichtbar_finden():
    fenster = phantom_clients()
    if not fenster:
        return []
    d = desktop()
    app_namen = []
    for i in range(d.get_child_count()):
        n = name(d.get_child_at_index(i)).lower()
        if n:
            app_namen.append(n)
    fehlend = []
    for f in fenster:
        hay = ((f.get("title") or "") + " " + (f.get("app_id") or "")).lower()
        woerter = [w for w in re.split(r"[^a-z0-9]+", hay) if len(w) >= 3]
        if any(a in hay or any(w in a for w in woerter) for a in app_namen):
            continue
        fehlend.append(f)
    return fehlend


def _unsichtbar_zeile(f):
    return {"ereignis": "unsichtbar",
            "fenster": f.get("title") or f.get("app_id") or "cid=%s" % f.get("cid"),
            "diagnose": "am anzeigeserver, nicht am a11y-bus",
            "naechster_schritt": "programm neu starten (phantom launch), "
                                 "nicht nur die ebene"}


def v_unsichtbar(req):
    try:
        fehlend = _unsichtbar_finden()
    except Exception as e:
        return {"ok": False, "error": "abgleich: %s" % e}
    return {"ok": True, "tier": 0, "anzahl": len(fehlend),
            "unsichtbar": [_unsichtbar_zeile(f) for f in fehlend]}


def _unsichtbar_pruefen(mindest_abstand):
    jetzt = time.time()
    if jetzt - _UNSICHTBAR["zuletzt"] < mindest_abstand:
        return
    _UNSICHTBAR["zuletzt"] = jetzt
    try:
        fehlend = _unsichtbar_finden()
    except Exception:
        return
    aktuell = set()
    for f in fehlend:
        schl = f.get("cid") or f.get("title")
        aktuell.add(schl)
        seit = _UNSICHTBAR["vermisst"].setdefault(schl, jetzt)
        if jetzt - seit < 5.0 or schl in _UNSICHTBAR["gemeldet"]:
            continue
        _UNSICHTBAR["gemeldet"][schl] = jetzt
        _melde(_unsichtbar_zeile(f))
    for tab in (_UNSICHTBAR["vermisst"], _UNSICHTBAR["gemeldet"]):
        for schl in [s for s in tab if s not in aktuell]:
            tab.pop(schl, None)


def _tick_unsichtbar():
    if not _LISTENER["laeuft"] or not _abonnenten_da():
        return False
    _unsichtbar_pruefen(25.0)
    return True


_AUSLOESBARE_ROLLEN = ("push button", "button", "toggle button", "menu item",
                       "check box", "radio button", "link", "tab", "page tab")
_KARTEN = {}
_KARTE_DIRTY = {}
_KARTE_TIMER = set()
_KARTE_MAX_B = 32 * 1024


def _karte_bauen(token, max_nodes=4000):
    root = app_by(token)
    if root is None:
        return None
    app = name(root) or str(token or "")
    eintraege, gesehen, erste = [], {}, {}
    basis = str(token or "desktop")
    fenster = []
    try:
        for j in range(min(root.get_child_count(), 120)):
            try:
                fenster.append((root.get_child_at_index(j), j))
            except Exception:
                pass
    except Exception:
        pass
    fenster.sort(key=lambda fj: 1 if "active" in states(fj[0]) else 0)
    stapel = [(f, False, 1, basis + "/" + str(j), name(f)) for f, j in fenster] \
        or [(root, False, 0, basis, name(root))]
    budget = max_nodes
    tab_leisten = []
    while stapel and budget > 0:
        o, in_doku, tiefe, pfad, fenster_nm = stapel.pop()
        budget -= 1
        rolle = role(o)
        doku = in_doku or "document" in rolle
        nm = name(o)
        st = states(o)
        if rolle == "page tab list":
            reiter, aktiv = [], None
            try:
                for jj in range(min(o.get_child_count(), 40)):
                    kind = o.get_child_at_index(jj)
                    knm = name(kind)
                    if knm:
                        reiter.append(knm)
                        if "selected" in states(kind):
                            aktiv = knm
            except Exception:
                pass
            if reiter:
                tab_leisten.append({"fenster": fenster_nm or "?",
                                    "tabs": reiter[:24], "aktiv": aktiv})
        eintrag = None
        if nm and "showing" in st:
            if "editable" in st:
                if _secure_node(o):
                    eintrag = {"art": "eingabefeld", "name": nm, "geheim": True,
                               "befehl": "gesperrt: Geheimnisfeld"}
                else:
                    eintrag = {"art": "eingabefeld", "name": nm,
                               "befehl": "phantom write %s '%s' \"<text>\""
                                         % (app, nm)}
            elif rolle in ("combo box", "list box"):
                eintrag = {"art": "auswahl", "name": nm,
                           "befehl": "phantom waehle %s '%s' '<option>'"
                                     % (app, nm)}
            elif any(actions(o)) or rolle in _AUSLOESBARE_ROLLEN:
                eintrag = {"art": "ausloesbar", "name": nm,
                           "befehl": "phantom invoke %s '%s'" % (app, nm)}
        if eintrag is not None:
            eintrag["id"] = _ziel_merken(token, pfad, rolle, nm)
            box = extents(o)
            if box:
                eintrag["extents"] = box
            schl = (eintrag["art"], nm)
            n_vettern = gesehen.get(schl, 0)
            if n_vettern < 8:
                gesehen[schl] = n_vettern + 1
                if n_vettern == 0:
                    erste[schl] = eintrag
                else:
                    eintrag["vetter"] = n_vettern + 1
                    if n_vettern == 1 and erste.get(schl) is not None:
                        erste[schl]["vetter"] = 1
                eintrag["rolle"] = rolle
                eintrag["bereich"] = "seite" if doku else "rahmen"
                eintraege.append(eintrag)
        if tiefe < 30:
            try:
                cc = o.get_child_count()
            except Exception:
                cc = 0
            for j in range(min(cc, 120)):
                try:
                    stapel.append((o.get_child_at_index(j), doku, tiefe + 1,
                                   pfad + "/" + str(j), fenster_nm))
                except Exception:
                    pass
    k = {"app": app, "eintraege": eintraege[:400]}
    if tab_leisten:
        k["tab_leisten"] = tab_leisten[:12]
    if len(eintraege) > 400 or (stapel and budget <= 0):
        k["voll"] = True
        k["hinweis"] = ("karte abgeschnitten (Knoten-Budget %d, %d Eintraege) — "
                        "aktives Fenster kam zuerst; 'max' erhoehen fuer mehr"
                        % (max_nodes, len(eintraege)))
    return k


def v_karte(req):
    token = req.get("app")
    try:
        k = _karte_bauen(token, int(req.get("max", 4000)))
    except Exception as e:
        return {"ok": False, "error": "karte: %s" % e, "app": token}
    if k is None:
        return {"ok": False, "error": "app not found", "app": token}
    schl = k["app"].lower()
    _KARTEN[schl] = {"token": token, "app": k["app"], "ts": time.time(),
                     "namen": set(e["name"] for e in k["eintraege"])}
    _steck_schreiben(k["app"], lambda d: d.update(
        {"karte_namen": sorted(e["name"] for e in k["eintraege"])[:400],
         "karte_ts": round(time.time(), 1)}))
    antwort = {"ok": True, "tier": 0, "app": k["app"],
               "anzahl": len(k["eintraege"]), "bedienbar": k["eintraege"]}
    if k.get("voll"):
        antwort["voll"] = True
        antwort["hinweis"] = k.get("hinweis", "")
    if k.get("tab_leisten"):
        antwort["tab_leisten"] = k["tab_leisten"]

        def _tabs_lernen(d):
            dt = d.get("dialog_tabs") or {}
            for leiste in k["tab_leisten"]:
                dt[leiste["fenster"]] = {"tabs": leiste["tabs"],
                                         "zuletzt": leiste.get("aktiv"),
                                         "ts": round(time.time(), 1)}
            d["dialog_tabs"] = dt

        _steck_schreiben(k["app"], _tabs_lernen)
    blob = json.dumps(antwort, ensure_ascii=False).encode()
    if len(blob) > _KARTE_MAX_B:
        verz = os.environ.get("PN_KARTEN_DIR") or "/work/phantom-karten"
        pfad = os.path.join(verz, "%s-%d.json" % (
            re.sub(r"[^a-z0-9._-]+", "_", schl) or "app", int(time.time())))
        try:
            os.makedirs(verz, exist_ok=True)
            with open(pfad, "w") as f:
                f.write(blob.decode())
            return {"ok": True, "tier": 0, "app": k["app"],
                    "anzahl": len(k["eintraege"]), "pfad": pfad}
        except Exception as e:
            antwort["hinweis"] = ("karte > 32 KB, aber %s nicht beschreibbar "
                                  "(%s) -- deshalb inline" % (verz, e))
    return antwort


def _karte_anstossen(app):
    schl = (app or "").lower()
    if schl not in _KARTEN or not _abonnenten_da():
        return
    _KARTE_DIRTY[schl] = time.time()
    if schl in _KARTE_TIMER:
        return
    from gi.repository import GLib
    _KARTE_TIMER.add(schl)
    GLib.timeout_add(400, _karte_delta_wenn_ruhig, schl)


def _karte_delta_wenn_ruhig(schl):
    if schl not in _KARTEN or not _abonnenten_da():
        _KARTE_TIMER.discard(schl)
        return False
    if time.time() - _KARTE_DIRTY.get(schl, 0) < 0.3:
        return True
    _KARTE_TIMER.discard(schl)
    stand = _KARTEN[schl]
    try:
        k = _karte_bauen(stand["token"])
    except Exception:
        return False
    if k is None:
        return False
    namen = set(e["name"] for e in k["eintraege"])
    neu = sorted(namen - stand["namen"])[:40]
    weg = sorted(stand["namen"] - namen)[:40]
    stand["namen"] = namen
    stand["ts"] = time.time()
    if neu or weg:
        _melde({"ereignis": "karte", "app": stand["app"],
                "delta": {"neu": neu, "weg": weg}})
    return False


_ZIELE = {}
_ZIELE_DECKEL = 4096


def _fnv32(s):
    h = 0x811C9DC5
    for b in str(s).encode("utf-8", "replace"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _ziel_merken(app, pfad, rolle, name_):
    tid = "t%06x" % (_fnv32("%s|%s|%s" % (str(app or "").lower(), pfad, rolle)) & 0xFFFFFF)
    e = _ZIELE.get(tid)
    if e is not None and (e["pfad"] != pfad or e["rolle"] != rolle):
        tid = "t%06x" % ((_fnv32("%s|%s" % (pfad, name_)) ^ 0x9E3779B9) & 0xFFFFFF)
    _ZIELE[tid] = {"app": app, "pfad": pfad, "rolle": rolle, "name": name_,
                   "ts": time.time()}
    if len(_ZIELE) > _ZIELE_DECKEL:
        for k, _ in sorted(_ZIELE.items(), key=lambda kv: kv[1]["ts"])[
                :len(_ZIELE) - _ZIELE_DECKEL]:
            _ZIELE.pop(k, None)
    return tid


def _pfad_knoten(app, pfad):
    root = app_by(app)
    if root is None:
        return None
    o = root
    for t in str(pfad).split("/")[1:]:
        try:
            o = o.get_child_at_index(int(t))
        except Exception:
            return None
        if o is None:
            return None
    return o


def _ziel_aufloesen(tid):
    e = _ZIELE.get(str(tid))
    if e is None:
        return None, {"error": "unbekanntes ziel %r — erst `karte`/`tree`/`such` holen "
                               "(ids veralten mit dem neustart der ebene)" % tid}
    o = _pfad_knoten(e["app"], e["pfad"])
    if o is None:
        return None, {"error": "ziel %s zeigt ins leere (fenster/knoten weg) — karte "
                               "neu holen" % tid,
                      "war": {"rolle": e["rolle"], "name": e["name"]}}
    if e["rolle"] and role(o) != e["rolle"]:
        return None, {"error": "ziel %s ist VERALTET (rolle war %r, ist %r) — karte "
                               "neu holen" % (tid, e["rolle"], role(o))}
    if e.get("name") and name(o) != e["name"]:
        return None, {"error": "ziel %s ist VERALTET (name war %r, ist %r) — der "
                               "pfad zeigt jetzt auf einen anderen knoten; karte "
                               "neu holen" % (tid, e["name"], name(o))}
    return o, e


_MENU_ROLLEN = ("menu", "menu item", "check menu item", "radio menu item")


def _menu_kinder(o, tiefe=0):
    try:
        n = o.get_child_count()
    except Exception:
        return
    for i in range(min(n, 120)):
        try:
            c = o.get_child_at_index(i)
        except Exception:
            continue
        if c is None:
            continue
        if role(c) in _MENU_ROLLEN and name(c):
            yield c
        elif tiefe < 2:
            for k in _menu_kinder(c, tiefe + 1):
                yield k


def _menu_finde(unter, wunsch):
    wl = wunsch.strip().lower()
    kandidaten = list(_menu_kinder(unter))
    for k in kandidaten:
        if name(k).strip().lower() == wl:
            return k, kandidaten
    for k in kandidaten:
        if wl in name(k).lower():
            return k, kandidaten
    return None, kandidaten


def _menu_fenster_stand(wurzel):
    aus = set()
    try:
        for i in range(wurzel.get_child_count()):
            k = wurzel.get_child_at_index(i)
            if role(k) in ("frame", "dialog", "window", "file chooser") \
                    and "showing" in states(k):
                aus.add("%s:%s" % (role(k), name(k)))
    except Exception:
        pass
    return aus


def v_menu(req):
    app = req.get("app")
    pfad = [t.strip() for t in str(req.get("pfad") or req.get("name") or "").split(">")
            if t.strip()]
    tgt = {"app": app, "pfad": ">".join(pfad)}
    if not pfad:
        return {"ok": False, "error": "menu braucht pfad 'Menue>Eintrag[>Untereintrag]'"}
    wurzel = app_by(app)
    if wurzel is None:
        return {"ok": False, "error": "app not found", "app": app}
    aktuell = resolve_by_role_name(app, "menu bar", None) or wurzel
    try:
        raeum_cid = phantom_target_for_app(app)
    except Exception:
        raeum_cid = None
    weg = []
    for stufe, segment in enumerate(pfad):
        ziel, kandidaten = _menu_finde(aktuell, segment)
        if ziel is None and stufe > 0:
            time.sleep(0.25)
            ziel, kandidaten = _menu_finde(aktuell, segment)
        if ziel is None:
            record("menu", 1, tgt, {"stufe": stufe + 1}, False)
            return {"ok": False, "error": "menuepunkt %r nicht gefunden" % segment,
                    "stufe": stufe + 1, "weg": weg,
                    "vorhanden": sorted({name(k) for k in kandidaten if name(k)})[:30],
                    "aufraeumen": "act <fenster> key esc  — falls ein menue offen blieb"}
        letzte = stufe == len(pfad) - 1
        vorher = puls_da = puls0 = None
        if letzte:
            vorher = _menu_fenster_stand(wurzel)
            puls_da = _puls_anfordern()
            puls0 = _WELTPULS["n"] if puls_da else 0
        try:
            if any(actions(ziel)):
                ziel.do_action(0)
            elif letzte:
                if puls_da:
                    _puls_freigeben()
                record("menu", 1, tgt, {"kein_action": name(ziel)}, False)
                return {"ok": False, "error": "eintrag %r traegt keine aktion" % name(ziel),
                        "weg": weg, "states": states(ziel)}
        except Exception as e:
            if letzte and puls_da:
                _puls_freigeben()
            record("menu", 1, tgt, {"exc": str(e)}, False)
            return {"ok": False, "error": "aktion auf %r: %s" % (name(ziel), e),
                    "weg": weg, "aufraeumen": "act <fenster> key esc"}
        weg.append(name(ziel))
        if not letzte:
            time.sleep(0.15)
            aktuell = ziel
    wirkung = None
    ende = time.time() + 1.5
    while time.time() < ende and wirkung is None:
        neu = _menu_fenster_stand(wurzel) - (vorher or set())
        if neu:
            wirkung = "fenster: " + ", ".join(sorted(neu))[:120]
            break
        if puls_da and _WELTPULS["n"] > puls0:
            wirkung = ("weltpuls (+%d ereignisse) — schwach: endzustand lesen"
                       % (_WELTPULS["n"] - puls0))
            break
        time.sleep(0.1)
    if puls_da:
        _puls_freigeben()
    try:
        if raeum_cid is None:
            raeum_cid = phantom_target_for_app(app)
        if raeum_cid is not None:
            phantom_ctl("popup-zu %s" % raeum_cid, timeout=3.0)
    except Exception:
        pass
    if wirkung is None and puls_da:
        record("menu", 1, tgt, {"weg": weg, "verpufft": True}, False)
        return {"ok": False,
                "error": "aktion ausgeloest, aber KEINE wirkung binnen 1,5 s "
                         "(kein fenster-delta, kein weltpuls) — der eintrag "
                         "verpufft (bekannt: baum traegt ihn, die app nicht)",
                "weg": weg,
                "naechster_schritt": "karte %s — ist ein dialog auf, oder "
                                     "fehlt der eintrag wirklich?" % app,
                "aufraeumen": "act <fenster> key esc"}
    record("menu", 1, tgt, {"weg": weg}, True)
    aus = {"ok": True, "tier": 1, "weg": weg,
           "popup_raeumung": (raeum_cid if raeum_cid is not None
                              else "kein phantom-ziel"),
           "hinweis": "ausgeloest ueber den a11y-baum — unabhaengig vom rendering; "
                      "was aufging, sagt das weltdelta der naechsten antwort"}
    if wirkung:
        aus["wirkung"] = wirkung
    elif not puls_da:
        aus["wirkung"] = "ungemessen (kein weltpuls-listener) — endzustand lesen"
    return aus


def _in_werkzeugleiste(o, deckel=30):
    p = o
    for _ in range(deckel):
        try:
            p = p.get_parent()
        except Exception:
            return False
        if p is None:
            return False
        if role(p) in ("tool bar", "menu bar"):
            return True
    return False


def _objekt_kandidaten(app_token, want_role, want_name, budget=20000, deckel=8):
    root = app_by(app_token)
    if root is None:
        return []
    wr = (want_role or "").lower()
    wn = (want_name or "").lower()
    stack = [root]
    seen = 0
    aus = []
    while stack and seen < budget and len(aus) < deckel:
        o = stack.pop(0)
        seen += 1
        if wn and wn in name(o).lower() and (not wr or wr in role(o).lower()):
            aus.append(o)
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 250)):
            try:
                stack.append(o.get_child_at_index(j))
            except Exception:
                pass
    return aus


def v_select_object(req):
    app = req.get("app")
    wunsch = str(req.get("name") or "").strip()
    tgt = {"app": app, "name": wunsch}
    o = req.get("_knoten")
    vettern = 0
    if o is None and wunsch:
        kand = _objekt_kandidaten(app, req.get("role"), wunsch)
        vettern = len(kand)
        echte = [k for k in kand if not _in_werkzeugleiste(k)]
        o = (echte or kand)[0] if kand else None
    if o is None:
        return {"ok": False, "error": "objekt nicht gefunden", "target": tgt,
                "naechster_schritt": "phantom find %s '%s' — wie heisst es wirklich?"
                                     % (app, wunsch)}
    if _secure_node(o):
        return {"ok": False, "error": _SECURE_ERR, "target": tgt}
    wege = []
    try:
        if any(actions(o)):
            o.do_action(0)
            wege.append("aktion")
    except Exception as e:
        wege.append("aktion-fehler:%r" % e)
    if not wege:
        try:
            if o.grab_focus():
                wege.append("fokus")
        except Exception:
            pass
    if not wege:
        box = extents(o)
        if box and box["w"] > 0 and box["h"] > 0:
            cx, cy = box["x"] + box["w"] // 2, box["y"] + box["h"] // 2
            ziel = req.get("target") or phantom_target_for_app(app)
            if ziel and _hat_ctl():
                phantom_ctl("act %s click %d %d" % (ziel, cx, cy))
                wege.append("klick")
            else:
                ok, _e = x11_click(cx, cy)
                wege.append("klick-x11" if ok else "klick-x11-fehler")
    st = states(o)
    if "selected" in st or "focused" in st:
        quittung = "bestaetigt"
    elif st:
        quittung = "widerlegt"
    else:
        quittung = "blind"
    record("select_object", 1, tgt, {"wege": wege}, quittung == "bestaetigt")
    aus = {"ok": bool(wege) and quittung != "widerlegt", "wege": wege,
           "quittung": quittung, "states": st, "target": tgt,
           "hinweis": None if quittung != "blind" else
                      "knoten meldet keine zustaende — nicht messbar ist NICHT nein"}
    if vettern > 1:
        aus["vettern"] = vettern
        aus["hinweis_vettern"] = ("%d gleichnamige knoten — werkzeugleisten-"
                                  "treffer nachrangig gewaehlt; eine ziel-id aus "
                                  "karte/find trifft GENAU einen" % vettern)
    return aus


def v_goto_cell(req):
    app = req.get("app") or "soffice"
    bereich = str(req.get("bereich") or req.get("text") or "").strip()
    if not bereich:
        return {"ok": False, "error": "goto_cell braucht einen bereich (A1 oder A1:E7)"}
    kasten = None
    for nb in ("Name Box", "Namenfeld", "Namensfeld"):
        kasten = (resolve_by_role_name(app, None, nb, want_editable=True)
                  or resolve_by_role_name(app, None, nb))
        if kasten is not None:
            break
    if kasten is None:
        return {"ok": False, "error": "keine Name-Box im baum", "app": app,
                "naechster_schritt": "phantom find %s 'Name' — wie heisst sie in "
                                     "dieser locale?" % app}
    try:
        if not kasten.grab_focus():
            return {"ok": False, "error": "Name-Box nicht fokussierbar"}
    except Exception as e:
        return {"ok": False, "error": "grab_focus: %s" % e}
    ziel = req.get("target") or phantom_target_for_app(app)
    if ziel and _hat_ctl():
        phantom_ctl("act %s key ctrl+a" % ziel)
        phantom_ctl("act %s type %s" % (ziel, bereich))
        phantom_ctl("act %s enter" % ziel)
        weg = "namebox+anzeigeserver"
    else:
        x11_key("ctrl+a")
        x11_type(bereich)
        x11_key("Return")
        weg = "namebox+x11"
    time.sleep(0.3)
    ist = (knoten_text(kasten) or "").strip()
    anker = bereich.split(":")[0].strip().upper()
    if ist and anker and anker in ist.upper():
        quittung = "bestaetigt"
    elif ist:
        quittung = "widerlegt"
    else:
        quittung = "blind"
    record("goto_cell", 2, {"app": app, "bereich": bereich}, {"weg": weg},
           quittung == "bestaetigt")
    return {"ok": quittung != "widerlegt", "quittung": quittung, "weg": weg,
            "bereich": bereich, "gelesen": ist[:40]}


def _fokus_knoten(token):
    root = app_by(token)
    if root is None:
        return None
    stapel = [root]
    seen = 0
    while stapel and seen < 4000:
        o = stapel.pop()
        seen += 1
        try:
            if o.get_state_set().contains(Atspi.StateType.FOCUSED):
                return o
        except Exception:
            continue
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 150)):
            try:
                stapel.append(o.get_child_at_index(j))
            except Exception:
                pass
    return None


def v_focus(req):
    comp = {}
    if _hat_ctl():
        rep = phantom_ctl("focus") or ""
        m = re.search(r"cid=(\d+)", rep)
        if m:
            comp["cid"] = int(m.group(1))
            for c in phantom_clients():
                if c["cid"] == comp["cid"]:
                    comp.update({"titel": c["title"], "app_id": c["app_id"]})
                    break
        else:
            comp["hinweis"] = rep.strip()[:120]
    token = req.get("app")
    knoten = None
    if token:
        knoten = _fokus_knoten(token)
    else:
        d = desktop()
        for i in range(d.get_child_count()):
            knoten = _fokus_knoten(str(i))
            if knoten is not None:
                break
    a11y = None
    if knoten is not None:
        a11y = {"rolle": role(knoten), "app": _app_von(knoten),
                "name": "(geheim)" if _secure_node(knoten) else name(knoten)}
    einig = None
    if comp.get("titel") is not None and a11y and a11y.get("app"):
        hay = ((comp.get("titel") or "") + " " + (comp.get("app_id") or "")).lower()
        al = a11y["app"].lower()
        einig = al in hay or any(w in hay for w in al.split() if len(w) >= 3)
    return {"ok": True, "tier": 0, "compositor": comp or None, "a11y": a11y,
            "einig": einig,
            "warnung": None if einig in (True, None) else
                       "QUELLEN WIDERSPRECHEN sich — eingaben koennten im falschen "
                       "fenster landen (fokus setzen: grab_focus oder phantom focus <cid>)"}


def v_type_geprueft(req):
    app = req.get("app")
    text = str(req.get("text") or "")
    if not text:
        return {"ok": False, "error": "text fehlt"}
    o = req.get("_knoten")
    if o is None and (req.get("role") or req.get("name")):
        o = _ziel_beschreibbar(req)
    if o is None:
        o = _fokus_knoten(app)
    geheim = o is not None and _secure_node(o)
    lesbar = o is not None and not geheim and char_count(o) is not None
    vorher = knoten_text(o) if lesbar else None
    ziel = req.get("target") or phantom_target_for_app(app)
    if ziel and _hat_ctl():
        rep = phantom_ctl("act %s type %s" % (ziel, text))
        gesendet = not rep.startswith("__PN_CTL_ERROR__")
        weg = "anzeigeserver"
    else:
        gesendet, _err = x11_type(text)
        weg = "x11"
    if not gesendet:
        record("type_geprueft", 2, {"app": app}, {"weg": weg}, False)
        return {"ok": False, "error": "eingabe nicht zustellbar", "weg": weg}
    time.sleep(0.35)
    if geheim:
        record("type_geprueft", 2, {"app": app}, {"geheim": True}, True)
        return {"ok": True, "quittung": "blind", "geheim": True, "laenge": len(text),
                "weg": weg, "hinweis": "geheimnisfeld: nur die laenge, nie der inhalt"}
    if not lesbar:
        record("type_geprueft", 2, {"app": app}, {"blind": True}, True)
        return {"ok": True, "quittung": "blind", "weg": weg,
                "hinweis": "ziel nicht lesbar — nicht messbar ist NICHT widerlegt; "
                           "echo/weltdelta der antwort ist die gegenprobe"}
    nachher = knoten_text(o) or ""
    quittung = "bestaetigt" if text in nachher else "widerlegt"
    record("type_geprueft", 2, {"app": app}, {"q": quittung}, quittung == "bestaetigt")
    return {"ok": quittung == "bestaetigt", "quittung": quittung, "weg": weg,
            "text": nachher[:200], "vorher": (vorher or "")[:120]}


_SCHRIFT = {}
_SCHRIFT_DIRTY = set()
_SCHRIFT_ROLLEN = ("text", "paragraph", "label", "entry", "heading", "static",
                   "document text", "document web", "terminal", "table cell",
                   "text box")
_SCHRIFT_KNOTEN_B = 4096
_SCHRIFT_APP_B = 256 * 1024
_SCHRIFT_GLOBAL_B = 2 * 1024 * 1024


def _schrift_eintrag(o, pfad):
    if _secure_node(o):
        return {"pfad": pfad, "rolle": role(o), "name": name(o), "geheim": True}
    t = read_text(o)
    if not t or not t.strip():
        nm = name(o)
        if not nm or not nm.strip():
            return None
        return {"pfad": pfad, "rolle": role(o), "name": nm, "text": "",
                "laenge": 0, "hash": _fnv32(nm)}
    voll = len(t)
    if voll > _SCHRIFT_KNOTEN_B:
        t = t[:3072] + "\n…[%d zeichen ausgelassen]…\n" % (voll - 4096) + t[-1024:]
    return {"pfad": pfad, "rolle": role(o), "name": name(o), "text": t,
            "laenge": voll, "hash": _fnv32(t)}


def _schrift_walzen(token, budget=2500):
    root = app_by(token)
    if root is None:
        return None
    knoten = {}
    bytes_gesamt = 0
    voll = False
    stapel = [(root, str(token or "desktop"))]
    seen = 0
    while stapel and seen < budget:
        o, pfad = stapel.pop()
        seen += 1
        st = states(o)
        r = role(o)
        if "showing" in st and (r in _SCHRIFT_ROLLEN or "editable" in st):
            e = _schrift_eintrag(o, pfad)
            if e is not None:
                laenge = len(e.get("text") or "")
                if bytes_gesamt + laenge > _SCHRIFT_APP_B:
                    voll = True
                else:
                    bytes_gesamt += laenge
                    knoten[pfad] = e
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 150)):
            try:
                stapel.append((o.get_child_at_index(j), pfad + "/" + str(j)))
            except Exception:
                pass
    return {"knoten": knoten, "bytes": bytes_gesamt, "voll": voll,
            "budget_erschoepft": bool(stapel), "ts": time.time()}


def _schrift_global_etat():
    while len(_SCHRIFT) > 1 and \
            sum(s["bytes"] for s in _SCHRIFT.values()) > _SCHRIFT_GLOBAL_B:
        aeltester = min(_SCHRIFT, key=lambda k: _SCHRIFT[k]["ts"])
        _SCHRIFT.pop(aeltester)


def v_schrift(req):
    token = req.get("app")
    neu = _schrift_walzen(token, int(req.get("max", 2500)))
    if neu is None:
        return {"ok": False, "error": "app not found", "app": token}
    schl = _app_schluessel(token)
    alt = _SCHRIFT.get(schl)
    delta = {"neu": [], "geaendert": [], "weg": []}
    if alt:
        for p, e in neu["knoten"].items():
            a = alt["knoten"].get(p)
            if a is None:
                delta["neu"].append(p)
            elif a.get("hash") != e.get("hash"):
                delta["geaendert"].append(p)
        delta["weg"] = [p for p in alt["knoten"] if p not in neu["knoten"]]
    _SCHRIFT[schl] = neu
    _schrift_global_etat()
    _SCHRIFT_DIRTY.discard(schl)
    antwort = {"ok": True, "tier": 0, "app": token, "anzahl": len(neu["knoten"]),
               "bytes": neu["bytes"], "voll": neu["voll"],
               "basis": ("diff gegen stand vor %.0fs" % (neu["ts"] - alt["ts"]))
                        if alt else "erststand (kein diff)",
               "delta": delta if alt else None}
    if not req.get("nur_delta"):
        antwort["texte"] = [
            {"pfad": p, "rolle": e["rolle"], "name": e["name"],
             "text": e.get("text"), "geheim": e.get("geheim")}
            for p, e in sorted(neu["knoten"].items())]
        blob = json.dumps(antwort, ensure_ascii=False)
        if len(blob) > 48 * 1024:
            verz = os.environ.get("PN_KARTEN_DIR") or "/work/phantom-karten"
            pfad = os.path.join(verz, "schrift-%s-%d.json" % (
                re.sub(r"[^a-z0-9._-]+", "_", schl) or "app", int(time.time())))
            try:
                os.makedirs(verz, exist_ok=True)
                with open(pfad, "w") as f:
                    f.write(blob)
                antwort.pop("texte")
                antwort["pfad"] = pfad
                antwort["hinweis"] = "texte > 48 KB — als datei abgelegt"
            except Exception:
                pass
    return antwort


def v_such(req):
    token = req.get("app")
    nadel = str(req.get("text") or "").strip()
    if not nadel:
        return {"ok": False, "error": "such braucht text"}
    schl = _app_schluessel(token)
    stand = _SCHRIFT.get(schl)
    frisch = False
    if stand is None or schl in _SCHRIFT_DIRTY or time.time() - stand["ts"] > 5.0:
        stand = _schrift_walzen(token)
        if stand is None:
            return {"ok": False, "error": "app not found", "app": token}
        _SCHRIFT[schl] = stand
        _schrift_global_etat()
        _SCHRIFT_DIRTY.discard(schl)
        frisch = True
    nl = nadel.lower()
    funde = []
    for p, e in stand["knoten"].items():
        if e.get("geheim"):
            continue
        t = e.get("text") or ""
        i = t.lower().find(nl)
        if i < 0 and nl not in (e.get("name") or "").lower():
            continue
        eintrag = {"pfad": p, "rolle": e["rolle"], "name": e["name"],
                   "auszug": t[max(0, i - 60):i + len(nadel) + 60] if i >= 0 else ""}
        o = _pfad_knoten(token, p)
        if o is not None:
            eintrag["id"] = _ziel_merken(token, p, e["rolle"], e["name"])
            b = extents(o)
            if b:
                eintrag["extents"] = b
        funde.append(eintrag)
        if len(funde) >= 40:
            break
    return {"ok": True, "tier": 0, "gesucht": nadel, "frisch": frisch,
            "alter_s": round(time.time() - stand["ts"], 1),
            "treffer": len(funde), "funde": funde}


_STECK_TROCKEN = {}


def _steck_dir():
    return os.environ.get("PN_STECKBRIEF_DIR") or "/work/phantom-steckbriefe"


def _steck_pfad(app):
    return os.path.join(_steck_dir(), (re.sub(
        r"[^a-z0-9._-]+", "_", str(app or "").lower()) or "app") + ".json")


def _steck_lesen(app):
    try:
        with open(_steck_pfad(app), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _steck_schreiben(app, aendern):
    import fcntl
    import tempfile
    verz = _steck_dir()
    try:
        os.makedirs(verz, exist_ok=True)
    except OSError:
        return False
    pfad = _steck_pfad(app)
    try:
        with open(pfad + ".lock", "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            d = _steck_lesen(app)
            d.setdefault("app", str(app))
            d.setdefault("angelegt", round(time.time(), 1))
            d["locale"] = os.environ.get("LANG") or ""
            d["geaendert"] = round(time.time(), 1)
            aendern(d)
            fd, tmp = tempfile.mkstemp(dir=verz, prefix=".steck-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, pfad)
        return True
    except Exception:
        return False


def _steck_strike(app, element, grund):
    jetzt = time.time()
    if jetzt - _STECK_TROCKEN.get(str(app), 0) < 3.0:
        return
    _STECK_TROCKEN[str(app)] = jetzt

    def _a(d):
        s = d.setdefault("strikes", {})
        e = s.setdefault(str(element or "?")[:80], {"anzahl": 0})
        e["anzahl"] = int(e.get("anzahl", 0)) + 1
        e["zuletzt"] = str(grund)[:120]

    _steck_schreiben(app, _a)


def v_steckbrief(req):
    d = _steck_lesen(req.get("app"))
    return {"ok": True, "tier": 0, "steckbrief": d or None,
            "pfad": _steck_pfad(req.get("app")),
            "hinweis": None if d else "noch keiner — waechst mit karte/gates/merke"}


def v_merke(req):
    app = req.get("app")
    text = str(req.get("text") or "").strip()[:500]
    if not app or not text:
        return {"ok": False, "error": "merke braucht app und text"}

    def _a(d):
        n = d.setdefault("notizen", [])
        n.append({"ts": round(time.time(), 1), "text": text})
        del n[:-100]

    ok = _steck_schreiben(app, _a)
    return {"ok": ok,
            "error": None if ok else "steckbrief-verzeichnis nicht beschreibbar (%s)"
                                     % _steck_dir()}


_KETTE = {"laeuft": False, "name": None, "seit": 0.0, "schritt": None}
_KETTE_LOCK = threading.Lock()

_KETTE_STAND = {}
_KETTE_STAND_DECKEL = 20


def _kette_stand_merken(name, req, schritt):
    if not name:
        return
    _KETTE_STAND[name] = {"req": {k: v for k, v in req.items()
                                  if k != "start_bei"},
                          "schritt": schritt, "ts": time.time()}
    _KETTE_STAND["__zuletzt__"] = name
    alt = [k for k in _KETTE_STAND if k != "__zuletzt__"]
    if len(alt) > _KETTE_STAND_DECKEL:
        for k in sorted(alt, key=lambda n: _KETTE_STAND[n]["ts"])[
                :len(alt) - _KETTE_STAND_DECKEL]:
            _KETTE_STAND.pop(k, None)


def _kette_weiter_req(req):
    nm = str(req.get("name") or "").strip() or _KETTE_STAND.get("__zuletzt__")
    st = _KETTE_STAND.get(nm) if nm and nm != "__zuletzt__" else None
    if not st:
        return None, {"ok": False,
                      "error": "nichts weiterzufuehren%s — der abbruchpunkt "
                               "lebt bis zum naechsten erfolg der kette"
                               % ((" fuer %r" % nm) if nm else "")}
    neu = dict(st["req"])
    try:
        plus = int(req.get("plus", 0) or 0)
    except Exception:
        plus = 0
    neu["start_bei"] = st["schritt"] + max(0, plus)
    return neu, None

_HANDLUNGS_VERBEN = {"menu", "invoke", "write", "insert_text", "typq",
                     "type_geprueft", "waehle", "select_option", "set_value",
                     "select_object", "objekt", "zelle", "goto_cell"}
_CTL_HANDLUNG = {"act"}


def _schritt_quittung(s, zeile, puls0, rest, puls_da):
    verb = str(zeile.get("verb") or s.get("verb") or "")
    if str(zeile.get("quittung") or "").startswith("widerlegt"):
        return None, "verb-quittung widerlegt — kanal ging durch, wirkung fehlt"
    if verb == "gate":
        return "zustand", None
    if isinstance(s.get("erfolg"), dict):
        try:
            frist_e = float(s.get("erfolg_frist", 5.0) or 5.0)
        except Exception:
            frist_e = 5.0
        frist_e = min(max(frist_e, 0.3), max(rest, 0.3))
        ende = time.time() + frist_e
        grund = ""
        while True:
            ok, grund = _gate_pruefen(s["erfolg"])
            if ok:
                return "erfolg", None
            if time.time() >= ende:
                return None, ("erfolgsmeldung blieb aus: praedikat binnen %.1fs "
                              "nicht erfuellt — ist: %s" % (frist_e, grund))
            time.sleep(0.15)
    handelnd = verb in _HANDLUNGS_VERBEN or (
        verb == "ctl"
        and str(s.get("cmd") or "").strip().split(" ", 1)[0] in _CTL_HANDLUNG)
    if not handelnd:
        return "lesend", None
    if zeile.get("bestaetigt") is True \
            or str(zeile.get("quittung") or "").startswith("bestaetigt"):
        return "bestaetigt", None
    if verb == "ctl" and "committet" in str(zeile.get("antwort") or ""):
        return "bestaetigt", None
    if verb == "ctl" and str(zeile.get("takt") or "").startswith("ziel weg"):
        return "bestaetigt", None
    if s.get("blind_ok"):
        return "blind_ok", None
    if not puls_da:
        return "blind", None
    try:
        frist_n = float(s.get("nachweis", 1.2) or 1.2)
    except Exception:
        frist_n = 1.2
    frist_n = min(max(frist_n, 0.2), max(rest, 0.2))
    ende = time.time() + frist_n
    while True:
        if _WELTPULS["n"] != puls0:
            return "indiz", None
        if time.time() >= ende:
            return None, ("keine wirkung messbar binnen %.1fs — erfolgsmeldung "
                          "blieb aus. besser: erfolg-praedikat am schritt; "
                          "blind_ok:true NUR wenn stille hier richtig ist"
                          % frist_n)
        time.sleep(0.1)


def _schritt_mit_frist(s, frist, handle_fn=None, ctl_fn=None):
    handle_fn = handle_fn or handle
    ctl_fn = ctl_fn or phantom_ctl
    verb = str(s.get("verb") or "")
    if verb == "gate":
        return _gate_schritt(s, frist)
    if verb == "ctl":
        cmd = str(s.get("cmd") or "").strip()
        if not cmd:
            return {"ok": False, "verb": "ctl", "error": "cmd fehlt"}
        rep = ctl_fn(cmd, timeout=frist + 5.0)
        unten = rep.lower()
        ok = (not rep.startswith("__PN_CTL_ERROR__")
              and not unten.startswith("error")
              and not unten.startswith("timeout")
              and "capability denied" not in unten)
        aus = {"ok": ok, "verb": "ctl", "cmd": cmd[:120],
               "antwort": rep[-600:].strip()}
        teile = cmd.split()
        if ok and s.get("takt") and teile and teile[0] == "act" and len(teile) > 1:
            takt_rep = ctl_fn("await stable %s 0.15 3" % teile[1],
                              timeout=8.0)[-120:].strip()
            if "kein Fenster" in takt_rep:
                takt_rep = "ziel weg (fenster zu) — gilt als vollzug"
            aus["takt"] = takt_rep
        return aus
    box = {}

    def _lauf():
        try:
            box["r"] = handle_fn(dict(s))
        except Exception as e:
            box["r"] = {"ok": False, "error": "schritt: %s" % e}

    t = threading.Thread(target=_lauf, daemon=True)
    t.start()
    t.join(frist)
    if t.is_alive():
        return {"ok": False, "verb": verb, "haengt": True,
                "error": "schritt haengt nach %.1fs — anwendung eingefroren? "
                         "`probe %s` sagt es" % (frist, s.get("app") or "<app>")}
    r = box.get("r") or {"ok": False, "error": "kein ergebnis"}
    if isinstance(r, dict):
        r.setdefault("verb", verb)
    return r


def _kette_lauf(conn, req):
    start = time.time()
    erfolgreich = False
    puls_da = False

    def sende(z):
        try:
            conn.sendall((json.dumps(z, ensure_ascii=False) + "\n").encode())
            return True
        except Exception:
            return False

    try:
        schritte = req.get("schritte") or []
        if not isinstance(schritte, list) or not schritte:
            sende({"ok": False, "error": "kette braucht schritte: [{verb:…}, …]"})
            return
        frist_gesamt = min(max(float(req.get("frist", 120.0) or 120.0), 1.0), 600.0)
        _KETTE.update(name=str(req.get("name") or "kette"), seit=start)
        puls_da = _puls_anfordern()
        quittungen = {}
        try:
            start_bei = int(req.get("start_bei", 1) or 1)
        except Exception:
            start_bei = 1
        start_bei = max(1, min(start_bei, len(schritte)))
        sende({"kette": "start", "name": _KETTE["name"], "schritte": len(schritte),
               "frist_s": frist_gesamt, "weltpuls": puls_da})
        if start_bei > 1:
            sende({"kette": "weiter", "ab": start_bei,
                   "uebersprungen": start_bei - 1})
        for i, s in enumerate(schritte, 1):
            if i < start_bei:
                continue
            rest = frist_gesamt - (time.time() - start)
            if rest <= 0.5:
                _kette_stand_merken(_KETTE["name"], req, i)
                sende({"kette": "abbruch", "schritt": i, "grund": "summen-frist "
                       "%.0fs erschoepft" % frist_gesamt, "weiter_ab": i})
                return
            if not isinstance(s, dict):
                _kette_stand_merken(_KETTE["name"], req, i)
                sende({"kette": "abbruch", "schritt": i,
                       "grund": "schritt ist kein objekt", "weiter_ab": i})
                return
            try:
                frist = float(s.get("frist", 20.0) or 20.0)
            except Exception:
                frist = 20.0
            frist = min(max(frist, 0.5), 120.0, rest)
            _KETTE["schritt"] = "%d/%d %s" % (i, len(schritte), s.get("verb"))
            t0 = time.time()
            puls0 = _WELTPULS["n"]
            r = _schritt_mit_frist(s, frist)
            zeile = {"schritt": i, "von": len(schritte),
                     "dauer_ms": int((time.time() - t0) * 1000)}
            if isinstance(r, dict):
                zeile.update(r)
            else:
                zeile.update({"ok": False, "error": "ergebnis kein objekt"})
            for feld in ("bedienbar", "texte"):
                if isinstance(zeile.get(feld), list) and len(zeile[feld]) > 5:
                    zeile[feld] = "in der kette gekuerzt: %d eintraege — %s " \
                        "direkt rufen fuer die liste" % (len(zeile[feld]),
                                                         zeile.get("verb"))
            fehler_q = None
            if zeile.get("ok"):
                rest2 = frist_gesamt - (time.time() - start)
                label, fehler_q = _schritt_quittung(s, zeile, puls0, rest2,
                                                    puls_da)
                if fehler_q is not None:
                    zeile["verifikation"] = "ausgeblieben"
                else:
                    zeile["verifikation"] = label
                    quittungen[label] = quittungen.get(label, 0) + 1
            if not sende(zeile):
                _kette_stand_merken(_KETTE["name"], req,
                                    min(i + 1, len(schritte))
                                    if zeile.get("ok") else i)
                return
            if not zeile.get("ok") and not s.get("weiter_bei_fehler"):
                _kette_stand_merken(_KETTE["name"], req, i)
                sende({"kette": "abbruch", "schritt": i,
                       "grund": zeile.get("error") or zeile.get("gate")
                       or "schritt meldete ok=false",
                       "weiter_ab": i,
                       "hinweis": "ist-zustand steht im schritt-ergebnis darueber; "
                                  "von hand richten, dann `phantom kette weiter` — "
                                  "die kette ist ein PLAN, kein skript"})
                return
            if fehler_q is not None:
                if s.get("weiter_bei_fehler"):
                    quittungen["ausgeblieben"] = quittungen.get("ausgeblieben", 0) + 1
                else:
                    _kette_stand_merken(_KETTE["name"], req, i)
                    sende({"kette": "abbruch", "schritt": i, "grund": fehler_q,
                           "weiter_ab": i,
                           "hinweis": "schritt lief, aber die wirkung ist nicht "
                                      "belegt — erfolg-praedikat setzen oder "
                                      "blind_ok:true; von hand richten und "
                                      "`phantom kette weiter` geht auch"})
                    return
        erfolgreich = True
        _KETTE_STAND.pop(_KETTE["name"], None)
        sende({"kette": "ende", "ok": True, "schritte": len(schritte),
               "dauer_ms": int((time.time() - start) * 1000),
               "verifikation": quittungen})
    finally:
        if req.get("_routine"):
            try:
                _routine_bilanz(req["_routine"], erfolgreich)
            except Exception:
                pass
        if puls_da:
            try:
                _puls_freigeben()
            except Exception:
                pass
        with _KETTE_LOCK:
            _KETTE.update(laeuft=False, name=None, seit=0.0, schritt=None)
        try:
            conn.close()
        except Exception:
            pass


def _kette_annehmen(conn, req):
    if req.get("weiter"):
        neu, fehler_w = _kette_weiter_req(req)
        if fehler_w is not None:
            try:
                conn.sendall((json.dumps(fehler_w, ensure_ascii=False)
                              + "\n").encode())
                conn.close()
            except Exception:
                pass
            return
        req = neu
    kette_req, fehler, rname = _routine_zu_kette(req)
    if fehler is not None:
        try:
            conn.sendall((json.dumps(fehler, ensure_ascii=False) + "\n").encode())
            conn.close()
        except Exception:
            pass
        return
    if kette_req is not None:
        kette_req["_routine"] = rname
        req = kette_req
    with _KETTE_LOCK:
        if _KETTE["laeuft"]:
            try:
                conn.sendall((json.dumps(
                    {"ok": False, "error": "es laeuft schon eine kette",
                     "kette": {"name": _KETTE["name"],
                               "seit_s": round(time.time() - _KETTE["seit"], 1),
                               "schritt": _KETTE["schritt"]}}) + "\n").encode())
                conn.close()
            except Exception:
                pass
            return
        _KETTE["laeuft"] = True
    threading.Thread(target=_kette_lauf, args=(conn, req), daemon=True).start()


def v_kette_hinweis(req):
    return {"ok": False, "error": "kette streamt JSON-zeilen und braucht die "
            "daemon-verbindung — im call-modus nicht verfuegbar",
            "schema": {"verb": "kette", "name": "…", "frist": 120,
                       "schritte": [{"verb": "menu", "app": "soffice",
                                     "pfad": "Insert>Chart", "frist": 10},
                                    {"verb": "ctl",
                                     "cmd": "await stable @Chart 0.4 8"},
                                    {"verb": "type_geprueft", "app": "soffice",
                                     "text": "Titel", "weiter_bei_fehler": False}]}}


def _routinen_dir():
    return os.environ.get("PN_ROUTINEN_DIR") or "/work/phantom-routinen"


def _routine_pfad(name_):
    sauber = re.sub(r"[^a-z0-9._-]+", "_", str(name_ or "").lower()).strip("_")
    return os.path.join(_routinen_dir(), (sauber or "routine") + ".json")


def _routine_lesen(name_):
    try:
        with open(_routine_pfad(name_), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _routine_schreiben(name_, d):
    import fcntl
    import tempfile
    verz = _routinen_dir()
    try:
        os.makedirs(verz, exist_ok=True)
        with open(_routine_pfad(name_) + ".lock", "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            fd, tmp = tempfile.mkstemp(dir=verz, prefix=".rout-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _routine_pfad(name_))
        return True
    except Exception:
        return False


def _platzhalter_fuellen(wert, params, fehlend):
    if isinstance(wert, str):
        def _ersatz(m):
            k = m.group(1)
            if k in params:
                return str(params[k])
            fehlend.add(k)
            return m.group(0)
        return re.sub(r"\{\{([a-zA-Z0-9_]+)\}\}", _ersatz, wert)
    if isinstance(wert, dict):
        return {k: _platzhalter_fuellen(v, params, fehlend) for k, v in wert.items()}
    if isinstance(wert, list):
        return [_platzhalter_fuellen(v, params, fehlend) for v in wert]
    return wert


def _gate_pruefen(p):
    if not isinstance(p, dict):
        return False, "praedikat ist kein objekt"
    if not _ENGINE_LOCK.acquire(timeout=15.0):
        return False, "engine belegt"
    try:
        return _gate_pruefen_roh(p)
    finally:
        _ENGINE_LOCK.release()


def _gate_pruefen_roh(p):
    if "nicht" in p:
        ok, grund = _gate_pruefen(p["nicht"])
        return (not ok), ("nicht(%s)" % grund)
    if "fenster_titel" in p:
        nadel = str(p["fenster_titel"]).lower()
        titel = [c.get("title") or "" for c in phantom_clients()]
        return (any(nadel in t.lower() for t in titel),
                "fenster_titel~%r in %s" % (p["fenster_titel"],
                                            [t[:30] for t in titel[:6]]))
    if "knoten" in p:
        k = p["knoten"] or {}
        o = resolve_by_role_name(k.get("app"), k.get("role"), k.get("name"))
        if o is None:
            return False, "knoten fehlt (%s/%s)" % (k.get("role"), k.get("name"))
        st = states(o)
        fehlt = [z for z in (k.get("zustand") or []) if z not in st]
        return (not fehlt, "knoten da, fehlt: %s" % fehlt if fehlt else "knoten da")
    if "text" in p:
        t = p["text"] or {}
        stand = _schrift_walzen(t.get("app"), 2500)
        if stand is None:
            return False, "app fehlt"
        nadel = str(t.get("enthaelt") or "").lower()
        da = any(nadel in (e.get("text") or "").lower()
                 or nadel in (e.get("name") or "").lower()
                 for e in stand["knoten"].values())
        return da, "text %r %s" % (t.get("enthaelt"), "da" if da else "fehlt")
    return False, "unbekanntes praedikat %s" % sorted(p)


def _gate_schritt(s, frist):
    p = s.get("praedikat")
    t0 = time.time()
    ende = t0 + frist
    grund = ""
    while True:
        ok, grund = _gate_pruefen(p)
        if ok:
            dauer = time.time() - t0
            if dauer > 3.0:
                _gate_frist_lernen(s, dauer)
            return {"ok": True, "verb": "gate", "erfuellt": grund}
        if time.time() >= ende:
            return {"ok": False, "verb": "gate", "gate": "frist",
                    "error": "praedikat nicht erfuellt binnen %.1fs — ist: %s"
                             % (frist, grund)}
        time.sleep(0.2)


def v_routine_speichern(req):
    name_ = str(req.get("name") or "").strip()
    kette = req.get("kette") or {}
    schritte = kette.get("schritte") if isinstance(kette, dict) else None
    if not name_ or not isinstance(schritte, list) or not schritte:
        return {"ok": False, "error": "routine_speichern braucht name und "
                                      "kette:{schritte:[…]}"}
    alt = _routine_lesen(name_) or {}
    d = {"name": name_, "beschreibung": str(req.get("beschreibung") or "")[:300],
         "kette": {"schritte": schritte,
                   "frist": kette.get("frist", 120)},
         "aliase": req.get("aliase") or alt.get("aliase") or {},
         "locale": os.environ.get("LANG") or "",
         "angelegt": alt.get("angelegt") or round(time.time(), 1),
         "geaendert": round(time.time(), 1),
         "geprueft_ts": alt.get("geprueft_ts"),
         "laeufe": alt.get("laeufe", 0), "fehlschlaege": alt.get("fehlschlaege", 0)}
    platz = set()
    _platzhalter_fuellen(schritte, {}, platz)
    d["platzhalter"] = sorted(platz)
    if not _routine_schreiben(name_, d):
        return {"ok": False, "error": "routinen-verzeichnis nicht beschreibbar (%s)"
                                      % _routinen_dir()}
    return {"ok": True, "name": name_, "schritte": len(schritte),
            "platzhalter": d["platzhalter"], "pfad": _routine_pfad(name_)}


def v_routinen(req):
    verz = _routinen_dir()
    aus = []
    try:
        namen = sorted(n for n in os.listdir(verz) if n.endswith(".json"))
    except OSError:
        namen = []
    for n in namen[:100]:
        try:
            with open(os.path.join(verz, n), encoding="utf-8") as f:
                d = json.load(f)
            zeile = {k: d.get(k) for k in ("name", "beschreibung", "platzhalter",
                                           "laeufe", "fehlschlaege",
                                           "geprueft_ts", "locale")}
            zeile["schritte"] = len((d.get("kette") or {}).get("schritte") or [])
            aus.append(zeile)
        except Exception:
            aus.append({"name": n, "error": "unlesbar"})
    return {"ok": True, "tier": 0, "anzahl": len(aus), "routinen": aus,
            "hinweis": None if aus else "noch keine — eine gelungene kette mit "
                                        "routine_speichern festhalten"}


def _routine_zu_kette(req):
    name_ = req.get("routine")
    if not name_:
        return None, None, None
    d = _routine_lesen(name_)
    if d is None:
        return None, {"ok": False, "error": "unbekannte routine %r" % name_,
                      "katalog": "phantom routinen"}, name_
    params = req.get("params") or {}
    fehlend = set()
    schritte = _platzhalter_fuellen(
        (d.get("kette") or {}).get("schritte") or [], params, fehlend)
    if fehlend:
        return None, {"ok": False, "error": "platzhalter unaufgeloest: %s"
                                            % sorted(fehlend),
                      "verlangt": d.get("platzhalter")}, name_
    aliase = d.get("aliase") or {}
    if aliase:
        def _alias(w):
            if isinstance(w, str):
                return aliase.get(w, w)
            if isinstance(w, dict):
                return {k: _alias(v) for k, v in w.items()}
            if isinstance(w, list):
                return [_alias(v) for v in w]
            return w
        schritte = _alias(schritte)
    neu = {"verb": "kette", "name": "routine:%s" % name_,
           "frist": req.get("frist") or (d.get("kette") or {}).get("frist", 120),
           "schritte": schritte}
    return neu, None, name_


def _routine_bilanz(name_, ok):
    d = _routine_lesen(name_)
    if d is None:
        return
    d["laeufe"] = int(d.get("laeufe", 0)) + 1
    if ok:
        d["geprueft_ts"] = round(time.time(), 1)
    else:
        d["fehlschlaege"] = int(d.get("fehlschlaege", 0)) + 1
    _routine_schreiben(name_, d)


_NOTIZ_KLASSEN = ("dialog", "fehler", "eingefroren", "unsichtbar", "karte")


def _hub_notiz(zeile):
    if not _hat_ctl():
        return
    try:
        kurz = {k: zeile[k] for k in ("ereignis", "app", "titel", "text", "schwere",
                                      "fenster", "haengt_seit_s", "delta") if k in zeile}
        kn = [k.get("name") for k in (zeile.get("knoepfe") or []) if k.get("name")][:6]
        if kn:
            kurz["knoepfe"] = kn
        text = json.dumps(kurz, ensure_ascii=False)[:380]
        threading.Thread(target=phantom_ctl, args=("notiz " + text,),
                         kwargs={"timeout": 3.0}, daemon=True).start()
        if kurz.get("ereignis") not in ("druck", "steckbrief", "verlauf"):
            _verlauf_schreiben("notiz", kurz)
    except Exception:
        pass


_VERLAUF_DIR = os.environ.get("PN_VERLAUF_DIR") or "/work/phantom-verlauf"
_VERLAUF_DECKEL = 20 * 1024 * 1024
_VERLAUF = {"tag": None, "n": 0, "voll_gemeldet": False}


def _verlauf_pfad(tag=None):
    return os.path.join(_VERLAUF_DIR, (tag or time.strftime("%Y-%m-%d")) + ".jsonl")


def _verlauf_trimmen():
    def lauf():
        try:
            tage = sorted(f for f in os.listdir(_VERLAUF_DIR)
                          if f.endswith(".jsonl"))
            for f in tage[:-7]:
                os.unlink(os.path.join(_VERLAUF_DIR, f))
        except Exception:
            pass
    threading.Thread(target=lauf, daemon=True).start()


def _verlauf_schreiben(art, zeile):
    try:
        tag = time.strftime("%Y-%m-%d")
        if _VERLAUF["tag"] not in (None, tag):
            _verlauf_trimmen()
            _VERLAUF["n"] = 0
            _VERLAUF["voll_gemeldet"] = False
        _VERLAUF["tag"] = tag
        if _VERLAUF["voll_gemeldet"]:
            return
        pfad = _verlauf_pfad(tag)
        _VERLAUF["n"] += 1
        if _VERLAUF["n"] % 64 == 1:
            try:
                if os.path.getsize(pfad) > _VERLAUF_DECKEL:
                    _VERLAUF["voll_gemeldet"] = True
                    _hub_notiz({"ereignis": "verlauf", "schwere": "warnung",
                                "text": "verlaufs-tagesdatei voll (%d MB) — "
                                        "weitere zeilen werden bis zum "
                                        "tageswechsel verworfen"
                                        % (_VERLAUF_DECKEL // 1048576)})
                    return
            except OSError:
                pass
        os.makedirs(_VERLAUF_DIR, exist_ok=True)
        d = {"ts": round(time.time(), 3), "art": art}
        d.update(zeile)
        with open(pfad, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False)[:2000] + "\n")
    except Exception:
        pass


def v_historie(req):
    try:
        seit_min = float(req.get("seit_min") or 60)
    except (TypeError, ValueError):
        seit_min = 60.0
    art = req.get("art")
    deckel = min(int(req.get("max") or 80), 400)
    ab = time.time() - seit_min * 60
    zeilen = []
    tage = sorted({time.strftime("%Y-%m-%d", time.localtime(ab)),
                   time.strftime("%Y-%m-%d")})
    for tag in tage:
        try:
            with open(_verlauf_pfad(tag), encoding="utf-8") as f:
                for z in f:
                    try:
                        d = json.loads(z)
                    except Exception:
                        continue
                    if d.get("ts", 0) < ab or (art and d.get("art") != art):
                        continue
                    zeilen.append(d)
        except OSError:
            pass
    gesamt = len(zeilen)
    zeilen = zeilen[-deckel:]
    return {"ok": True, "seit_min": seit_min, "gesamt": gesamt,
            "gezeigt": len(zeilen), "zeilen": zeilen,
            "hinweis": None if gesamt else
            "leer — der verlauf beginnt mit dieser kiste (W5) und ueberlebt "
            "ab jetzt ringtiefe und boot"}


def _zahl_aus(pfad):
    try:
        with open(pfad) as f:
            t = f.read().strip()
        return None if t == "max" else int(t)
    except Exception:
        return None


def _cgroup_stand():
    basis = "/sys/fs/cgroup"
    cur = _zahl_aus(basis + "/memory.current")
    high = _zahl_aus(basis + "/memory.high")
    mx = _zahl_aus(basis + "/memory.max")
    aus = {"memory_current_mb": None if cur is None else round(cur / 1048576.0, 1),
           "memory_high_mb": None if high is None else round(high / 1048576.0, 1),
           "memory_max_mb": None if mx is None else round(mx / 1048576.0, 1)}
    grenze = high or mx
    if cur is not None and grenze:
        aus["abstand_mb"] = round((grenze - cur) / 1048576.0, 1)
        aus["abstand_prozent"] = round(100.0 * (grenze - cur) / grenze, 1)
    try:
        with open(basis + "/cpu.pressure") as f:
            z = f.read().splitlines()[0]
        aus["cpu_druck_avg10"] = float(z.split("avg10=")[1].split()[0])
    except Exception:
        aus["cpu_druck_avg10"] = None
    return aus


def _prozesse_top(n=15):
    aus = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/status" % pid) as f:
                st = f.read()
            m = re.search(r"VmRSS:\s*(\d+) kB", st)
            rss = int(m.group(1)) if m else 0
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8",
                                                           "replace").strip()
            if not cmd:
                cmd = "[" + st.split("\n", 1)[0].split("\t")[-1].strip() + "]"
            aus.append({"pid": int(pid), "rss_mb": round(rss / 1024.0, 1),
                        "cmd": cmd[:100]})
        except Exception:
            continue
    aus.sort(key=lambda p: -p["rss_mb"])
    return aus[:n]


def _fuellstand(pfad):
    try:
        s = os.statvfs(pfad)
        gesamt = s.f_blocks * s.f_frsize
        frei = s.f_bavail * s.f_frsize
        return {"gesamt_mb": round(gesamt / 1048576.0, 1),
                "frei_mb": round(frei / 1048576.0, 1),
                "belegt_prozent": (round(100.0 * (1 - float(frei) / gesamt), 1)
                                   if gesamt else None)}
    except Exception:
        return {"gesamt_mb": None, "frei_mb": None, "belegt_prozent": None}


def v_zellstatus(req):
    tmp = _fuellstand("/tmp")
    tmp["achtung"] = "/tmp ist RAM — grosse dateien nach /work"
    kette = None
    try:
        kette = {"laeuft": bool(_KETTE.get("laeuft")),
                 "name": _KETTE.get("name"), "schritt": _KETTE.get("schritt")}
    except Exception:
        pass
    seit = _LAEUFT.get("seit") or 0.0
    return {"ok": True,
            "cgroup": _cgroup_stand(),
            "prozesse_top": _prozesse_top(15),
            "tmp": tmp,
            "work": _fuellstand("/work"),
            "kette": kette,
            "beschaeftigt": {"was": _LAEUFT.get("was"),
                             "seit_s": round(time.time() - seit, 1)
                             if seit else None},
            "hinweis_jobs": "tafel-jobs kennt nur die tafel: phantom jobs"}


_DRUCK = {"ram": False, "tmp": False}


def _druck_wache():
    while True:
        try:
            c = _cgroup_stand()
            p = c.get("abstand_prozent")
            if p is not None:
                if p < 15 and not _DRUCK["ram"]:
                    _DRUCK["ram"] = True
                    z = {"ereignis": "druck", "schwere": "warnung",
                         "text": "RAM-abstand nur noch %.1f%% bis memory.high "
                                 "(%.0f MB) — memory.high SPERRT statt zu "
                                 "queuen: schweres beenden/verschieben"
                                 % (p, c.get("abstand_mb") or 0)}
                    _hub_notiz(z)
                    _verlauf_schreiben("druck", {"text": z["text"]})
                elif p > 25 and _DRUCK["ram"]:
                    _DRUCK["ram"] = False
            t = _fuellstand("/tmp")
            bp = t.get("belegt_prozent")
            if bp is not None:
                if bp > 80 and not _DRUCK["tmp"]:
                    _DRUCK["tmp"] = True
                    z = {"ereignis": "druck", "schwere": "warnung",
                         "text": "/tmp zu %.0f%% voll — /tmp IST RAM; "
                                 "nach /work verschieben" % bp}
                    _hub_notiz(z)
                    _verlauf_schreiben("druck", {"text": z["text"]})
                elif bp < 60 and _DRUCK["tmp"]:
                    _DRUCK["tmp"] = False
        except Exception:
            pass
        time.sleep(30.0)


_ANGELESEN = set()


def _steck_anlesen(app):
    a = str(app or "").strip().lower()
    if not a or a in _ANGELESEN:
        return
    _ANGELESEN.add(a)

    def lauf():
        try:
            d = _steck_lesen(a)
            teile = []
            strikes = d.get("strikes") or {}
            if strikes:
                top = sorted(strikes.items(),
                             key=lambda kv: -int((kv[1] or {}).get("anzahl", 0)))[:2]
                teile.append("strikes: " + ", ".join(
                    "%s(%d)" % (k[:40], int((v or {}).get("anzahl", 0)))
                    for k, v in top))
            notizen = d.get("notizen") or []
            if notizen:
                letzte = notizen[-1]
                teile.append("merke: " + str((letzte or {}).get("text", ""))[:100])
            if d.get("fristen"):
                teile.append("fristen: " + ", ".join(
                    "%s=%ss" % (k, v) for k, v in sorted(d["fristen"].items())[:4]))
            elif a.startswith("soffice"):
                teile.append("faustregel: calc text-gates nach massen-edits >=20s")
            if d.get("dialog_tabs"):
                teile.append("dialog_tabs bekannt (%d)" % len(d["dialog_tabs"]))
            if not teile:
                return
            z = {"ereignis": "steckbrief", "app": a,
                 "text": " · ".join(teile)[:340]}
            _hub_notiz(z)
            _verlauf_schreiben("steckbrief", {"app": a, "text": z["text"]})
        except Exception:
            pass
    threading.Thread(target=lauf, daemon=True).start()


def _gate_frist_lernen(s, dauer):
    try:
        p = s.get("praedikat") or {}
        if not isinstance(p.get("text"), dict):
            return
        app = p["text"].get("app")
        if not app:
            return
        mess = round(dauer + 2.0, 1)

        def aendern(d):
            f = d.setdefault("fristen", {})
            if mess > float(f.get("gate_text") or 0):
                f["gate_text"] = mess

        threading.Thread(target=_steck_schreiben, args=(app, aendern),
                         daemon=True).start()
    except Exception:
        pass


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
    "select_option": v_select_option,
    "set_value": v_set_value,
    "insert_text": v_insert_text,
    "press_keys": v_press_keys,
    "click_at": v_click_at,
    "clients": v_clients,
    "apps": v_apps,
    "grab_focus": v_grab_focus,
    "read_text": v_read_text,
    "mcp_schema": mcp_tool_schema,
    "ping": lambda req: {"ok": True, "pong": True, "pid": os.getpid(),
                         "seit_s": round(time.time() - _START, 1)},
    "subscribe": v_subscribe_hinweis,
    "probe": v_probe,
    "unsichtbar": v_unsichtbar,
    "karte": v_karte,
    "menu": v_menu,
    "select_object": v_select_object,
    "goto_cell": v_goto_cell,
    "focus": v_focus,
    "type_geprueft": v_type_geprueft,
    "schrift": v_schrift,
    "such": v_such,
    "steckbrief": v_steckbrief,
    "merke": v_merke,
    "kette": v_kette_hinweis,
    "routine_speichern": v_routine_speichern,
    "routinen": v_routinen,
    "zellstatus": v_zellstatus,
    "historie": v_historie,
}

_OHNE_ENGINE_LOCK = ("ping", "probe", "steckbrief", "merke", "mcp_schema",
                     "subscribe", "kette", "routine_speichern", "routinen",
                     "zellstatus", "historie")
_ENGINE_LOCK = threading.RLock()

def handle(req):
    verb = req.get("verb")
    fn = VERBS.get(verb)
    if fn is None:
        return {"ok": False, "error": f"unknown verb {verb!r}",
                "verbs": sorted(VERBS.keys())}
    try:
        _steck_anlesen(req.get("app"))
    except Exception:
        pass
    braucht_lock = verb not in _OHNE_ENGINE_LOCK
    if braucht_lock and not _ENGINE_LOCK.acquire(timeout=30.0):
        return {"ok": False,
                "error": "engine belegt — ein anderer aufruf haelt die a11y-verbindung",
                "kette": {"laeuft": _KETTE["laeuft"], "name": _KETTE["name"],
                          "schritt": _KETTE["schritt"]},
                "naechster_schritt": "probe <app> — haengt die anwendung selbst?"}
    try:
        tid = req.get("ziel")
        if isinstance(tid, str) and re.match(r"^t[0-9a-f]{6}$", tid):
            o, e = _ziel_aufloesen(tid)
            if o is None:
                return {"ok": False, **e}
            req = dict(req)
            req["_knoten"] = o
            req.setdefault("app", e["app"])
            req.setdefault("role", e["rolle"])
            req.setdefault("name", e["name"])
        return fn(req)
    except Exception as e:
        return {"ok": False, "error": f"engine: {e}",
                "trace": traceback.format_exc().splitlines()[-3:]}
    finally:
        if braucht_lock:
            _ENGINE_LOCK.release()

def sock_path():
    return os.environ.get("PN_ATSPID_SOCK") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "pn-atspid.sock")

_LAEUFT = {"was": None, "seit": 0.0}
_MAX_S = float(os.environ.get("PN_ATSPID_MAX_S", "120") or 120)


def _wachhund():
    while True:
        time.sleep(5.0)
        was, seit = _LAEUFT["was"], _LAEUFT["seit"]
        if was is None or seit <= 0:
            continue
        dauer = time.time() - seit
        if dauer > _MAX_S:
            sys.stderr.write(
                "pn-atspid: '%s' haengt seit %.0fs (Grenze %.0fs) -- Dienst beendet sich. "
                "Die Anwendung antwortet nicht auf AT-SPI. `phantom up` startet neu.\n"
                % (was, dauer, _MAX_S))
            sys.stderr.flush()
            os._exit(3)


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
    threading.Thread(target=_wachhund, daemon=True).start()
    threading.Thread(target=_druck_wache, daemon=True).start()
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
            if req.get("verb") == "subscribe":
                _abo_annehmen(conn, req)
                conn = None
                continue
            if req.get("verb") == "kette":
                _kette_annehmen(conn, req)
                conn = None
                continue
            _LAEUFT["was"] = str(req.get("verb") or "?")
            _LAEUFT["seit"] = time.time()
            try:
                reply = handle(req)
            finally:
                _LAEUFT["was"] = None
                _LAEUFT["seit"] = 0.0
            _antwort = json.dumps(reply, ensure_ascii=False)
            conn.sendall((_antwort + "\n").encode())
            for _tot in ("disconnected from message bus", "Connection is closed",
                         "The connection is closed"):
                if _tot in _antwort:
                    sys.stderr.write(
                        "pn-atspid: Verbindung zum a11y-Bus ist tot (%s) -- Dienst beendet "
                        "sich, damit er frisch startet. `phantom up` holt ihn zurueck.\n" % _tot)
                    sys.stderr.flush()
                    try:
                        _hub_notiz({"ereignis": "atspid_exit", "schwere": "warnung",
                                    "text": "atspid beendet sich SELBST (a11y-bus "
                                            "tot: %s) — naechstes verb/`phantom up` "
                                            "startet frisch" % _tot[:60]})
                        _verlauf_schreiben("notiz", {"text": "atspid selbst-exit "
                                                             "(a11y-bus tot)"})
                        time.sleep(0.2)
                    except Exception:
                        pass
                    try:
                        conn.close()
                    except Exception:
                        pass
                    os._exit(4)
        except Exception as e:
            try:
                conn.sendall((json.dumps({"ok": False, "error": f"server: {e}"}) + "\n").encode())
            except Exception:
                pass
        finally:
            try:
                if conn is not None:
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
