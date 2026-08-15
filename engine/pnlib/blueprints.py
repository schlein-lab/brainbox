
import os
import re
import json

_pylist = list

try:
    from pnlib import site as _site
except Exception:
    _site = None
try:
    from pnlib import devices as _devices
except Exception:
    _devices = None

def _cfg(key, default=None):
    if _site is not None:
        return _site.get(key, default)
    return os.environ.get(key, default)

def _dirs():
    home = _cfg("SERVICE_HOME") or os.path.expanduser("~")
    data = os.path.join(home, ".local", "share", "brainbox-portal")
    return [
        os.path.join(home, "portioneer", "blueprints"),
        os.path.join(data, "blueprints"),
    ]

_LOAD_ERRORS = []

def _validate(bp):

    if not isinstance(bp, dict):
        return "Manifest ist kein JSON-Objekt"
    bid = bp.get("id")
    if not isinstance(bid, str) or not bid.strip():
        return "Feld 'id' fehlt oder ist kein nicht-leerer Text"
    for key in ("match", "driver", "invoke"):
        if key in bp and not isinstance(bp[key], dict):
            return "Feld '%s' muss ein Objekt sein" % key
    for key in ("capabilities", "config", "quirks", "when", "steps",
                "tools", "preconditions", "pitfalls"):
        if key in bp and not isinstance(bp[key], _pylist):
            return "Feld '%s' muss eine Liste sein" % key
    for key in ("name", "kind", "device_class", "status", "source", "summary"):
        if key in bp and not isinstance(bp[key], str):
            return "Feld '%s' muss Text sein" % key
    return None

def load():
    seen = {}
    errors = []
    for d in _dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            man = os.path.join(d, name, "blueprint.json")
            if not os.path.isfile(man):
                continue
            if os.path.exists(os.path.join(d, name, ".disabled")):
                continue
            try:
                bp = json.load(open(man, encoding="utf-8"))
            except Exception as e:
                errors.append({"dir": name, "reason": "ungültiges JSON (%s)" % e.__class__.__name__})
                continue
            bad = _validate(bp)
            if bad:
                errors.append({"dir": name,
                               "id": bp.get("id") if isinstance(bp, dict) else None,
                               "reason": bad})
                continue
            bp["_dir"] = os.path.join(d, name)
            seen[bp["id"]] = bp
    global _LOAD_ERRORS
    _LOAD_ERRORS = errors
    return _pylist(seen.values())

def load_errors():

    load()
    return _pylist(_LOAD_ERRORS)

def list():
    return load()

def get(bp_id):
    for bp in load():
        if bp.get("id") == bp_id:
            return bp
    return None

def _dev_addr(dev):
    tr = dev.get("transport") or {}
    return tr.get("addr") or dev.get("ip") or dev.get("host")

def _re_ok(pattern, value):
    if not pattern:
        return True
    try:
        return re.search(pattern, str(value or ""), re.I) is not None
    except re.error:
        return False

def _claims(bp, dev):

    if bp.get("kind") == "action":
        return False
    m = bp.get("match")
    m = m if isinstance(m, dict) else {}
    tr = dev.get("transport") or {}
    if m.get("kind") and dev.get("kind") not in m["kind"]:
        return False
    if m.get("proto") and tr.get("proto") not in m["proto"]:
        return False
    if not _re_ok(m.get("manufacturer_re"), tr.get("manufacturer")):
        return False
    if not _re_ok(m.get("deviceType_re"), tr.get("deviceType")):
        return False
    if not _re_ok(m.get("model_re"), tr.get("model")):
        return False
    return True

def match(device):
    return [bp["id"] for bp in load() if _claims(bp, device)]

def _roster():
    if _devices is not None:
        try:
            return _devices._load_roster()
        except Exception:
            pass

    home = _cfg("SERVICE_HOME") or os.path.expanduser("~")
    p = os.path.join(home, ".local", "share", "brainbox-portal", "devices.json")
    try:
        d = json.load(open(p))
    except Exception:
        return []
    if isinstance(d, _pylist):
        return d
    if isinstance(d, dict):
        items = d.get("devices")
        return items if isinstance(items, _pylist) else [v for v in d.values() if isinstance(v, dict)]
    return []

def annotate(devices=None):
    devs = devices if devices is not None else _roster()
    out = []
    for d in devs:
        if isinstance(d, dict):
            d = dict(d)
            d["blueprints"] = match(d)
            out.append(d)
    return out

def summary():

    devs = _roster()
    cards = []
    for bp in load():
        claimed = [d.get("name") or d.get("id") for d in devs if isinstance(d, dict) and _claims(bp, d)]
        cards.append({
            "id": bp.get("id"), "name": bp.get("name"), "kind": bp.get("kind"),
            "device_class": bp.get("device_class"), "capabilities": bp.get("capabilities") or [],
            "status": bp.get("status") or "reference", "source": bp.get("source") or "builtin",
            "config": [c.get("key") for c in (bp.get("config") or [])],
            "quirks": bp.get("quirks") or [], "claims": claimed,
        })
    return cards

def _site_dir():
    home = _cfg("SERVICE_HOME") or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "brainbox-portal", "blueprints")

def install(manifest):

    if not isinstance(manifest, dict) or not manifest.get("id"):
        return False, "manifest braucht mindestens ein 'id'-Feld"
    cid = re.sub(r"[^A-Za-z0-9_-]", "-", str(manifest["id"]))[:64]
    d = os.path.join(_site_dir(), cid)
    try:
        os.makedirs(d, exist_ok=True)
        manifest.setdefault("source", "installed")
        tmp = os.path.join(d, "blueprint.json.tmp")
        open(tmp, "w", encoding="utf-8").write(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(tmp, os.path.join(d, "blueprint.json"))
        return True, cid
    except OSError as e:
        return False, str(e)

def set_enabled(card_id, on):

    d = os.path.join(_site_dir(), card_id)
    if not os.path.isdir(d):
        return False, "keine installierte Card '%s'" % card_id
    marker = os.path.join(d, ".disabled")
    try:
        if on and os.path.exists(marker):
            os.remove(marker)
        elif not on and not os.path.exists(marker):
            open(marker, "w").close()
        return True, card_id
    except OSError as e:
        return False, str(e)

def resolve_driver(card_id, role=None):

    bp = get(card_id)
    if bp is None:
        return {"ok": False, "error": "keine Card '%s'" % card_id}
    cfg_keys = [c.get("key") for c in (bp.get("config") or []) if c.get("key")]
    addr = None
    if _devices is not None:
        if role:
            addr = _devices.addr(role)
        for k in ([] if addr else cfg_keys):
            binding = _cfg(k)
            if binding:
                addr = _devices.resolve(binding)
                if addr:
                    break
        if not addr:
            for d in _roster():
                if isinstance(d, dict) and _claims(bp, d):
                    addr = _dev_addr(d)
                    break
    return {"ok": True, "card": bp.get("id"), "kind": bp.get("kind"),
            "driver": bp.get("driver") or {}, "address": addr,
            "config_needed": cfg_keys, "capabilities": bp.get("capabilities") or []}

def actions():

    return [bp for bp in load() if bp.get("kind") == "action"]

def match_action(text, limit=5):

    t = (text or "").lower()
    scored = []
    for bp in actions():
        hits = sum(1 for k in (bp.get("when") or []) if str(k).lower() in t)
        if hits:
            scored.append((hits, bp))
    scored.sort(key=lambda x: -x[0])
    keep = ("id", "name", "summary", "steps", "tools", "preconditions", "pitfalls", "proven", "source")
    return [{k: bp.get(k) for k in keep} for _h, bp in scored[:limit]]

def action_summary():

    out = []
    for bp in actions():
        out.append({
            "id": bp.get("id"), "name": bp.get("name"), "kind": "action",
            "summary": bp.get("summary") or "", "when": bp.get("when") or [],
            "steps": bp.get("steps") or [], "pitfalls": bp.get("pitfalls") or [],
            "tools": bp.get("tools") or [], "proven": bool(bp.get("proven")),
            "source": bp.get("source") or "brainbox-learned",
        })
    return out

if __name__ == "__main__":
    for c in summary():
        print("DEVICE %-16s %-7s claims=%s" % (c["id"], c["kind"], c["claims"]))
    for c in action_summary():
        print("ACTION %-20s proven=%s when=%s" % (c["id"], c["proven"], c["when"][:4]))

LANES = ("display", "job")

_TREES = {
    "portal": os.path.join(".local", "bin"),
    ".local/bin": os.path.join(".local", "bin"),
    "engine": "portioneer",
    "portioneer": "portioneer",
    "pnlib": os.path.join("portioneer", "pnlib"),
    "services": os.path.join("brainarbeit", "cockpit", "server"),
    "brainarbeit/cockpit/server": os.path.join("brainarbeit", "cockpit", "server"),
}
_PARAM_NAME = re.compile(r"^\w{1,32}$")
_PARAM_VALUE = re.compile(r"^[\w .,:/@=+%()\[\]\-?!'äöüÄÖÜß]{0,500}$")

def _home():
    return _cfg("SERVICE_HOME") or os.path.expanduser("~")

def driver_file(bp):

    drv = bp.get("driver") or {}
    mod = drv.get("module")
    if not mod:
        return None, "kein driver.module"
    tree = str(drv.get("tree") or "")
    rel = _TREES.get(tree)
    if rel is None:
        return None, "unbekannter driver.tree %r (erlaubt: %s, oder node:<host>)" % (
            tree, ", ".join(sorted(set(_TREES))))
    base = os.path.join(_home(), rel)
    for cand in (mod + ".py", mod):
        p = os.path.join(base, cand)
        if os.path.isfile(p):
            return p, None
    return None, "Treiber-Datei fehlt: %s" % os.path.join(base, mod + ".py")

def _entry_defined(path, entry):

    import ast
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except Exception as e:
        return None, "Treiber nicht parsebar: %s" % e
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == entry:
            return True, None
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == entry for t in n.targets):
            return True, None
    return False, "driver.entry '%s' ist in %s nicht definiert" % (entry, os.path.basename(path))

def _displays():
    p = os.path.join(_home(), ".local", "share", "brainbox-portal", "displays.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}

def _display_for(bp):

    want = (bp.get("driver") or {}).get("display_kind")
    if not want:
        return None
    for did, d in _displays().items():
        if isinstance(d, dict) and d.get("kind") == want:
            return d.get("id") or did
    return None

def dispatchable(bp):

    inv = bp.get("invoke") or {}
    return [c for c in (bp.get("capabilities") or [])
            if isinstance(inv.get(c), dict) and inv[c].get("via") in LANES]

def driver_check(card_id):

    bp = get(card_id)
    if bp is None:
        return {"ok": False, "card": card_id, "problems": ["keine Card '%s'" % card_id]}
    drv = bp.get("driver") or {}
    tree = str(drv.get("tree") or "")
    out = {"card": bp.get("id"), "name": bp.get("name"), "kind": bp.get("kind"),
           "tree": tree or None, "module": drv.get("module"), "entry": drv.get("entry"),
           "remote": False, "problems": []}

    if tree.startswith("node:"):
        out["remote"] = True
        out["node"] = tree[5:]
    elif drv:
        path, prob = driver_file(bp)
        out["module_file"] = path
        if prob:
            out["problems"].append(prob)
        elif drv.get("entry"):
            found, prob2 = _entry_defined(path, drv["entry"])
            out["entry_found"] = found
            if prob2:
                out["problems"].append(prob2)
    else:
        out["problems"].append("kein driver-Block")

    try:
        out["address"] = resolve_driver(card_id).get("address")
    except Exception:
        out["address"] = None
    if drv.get("display_kind"):
        out["binding"] = "display"
        out["display"] = _display_for(bp)
        if not out["display"]:
            out["problems"].append("kein Display in der Registry mit kind='%s'" % drv["display_kind"])
    elif out["remote"]:
        out["binding"] = "node:%s" % out.get("node")
    elif drv.get("needs_address") is False:
        out["binding"] = "parameter"
    else:
        out["binding"] = "registry"
        if not out["address"]:
            out["problems"].append("kein Geraet in der Registry beansprucht diese Card — Adresse unbekannt")

    out["config_unbound"] = [c.get("key") for c in (bp.get("config") or [])
                             if c.get("key") and not _cfg(c["key"])]
    inv = bp.get("invoke") or {}
    bad_lane = [c for c, s in inv.items() if not isinstance(s, dict) or s.get("via") not in LANES]
    if bad_lane:
        out["problems"].append("invoke mit unbekannter Bahn: %s (erlaubt: %s)"
                               % (", ".join(sorted(bad_lane)), "/".join(LANES)))
    undecl = [c for c in inv if c not in (bp.get("capabilities") or [])]
    if undecl:
        out["problems"].append("invoke fuer nicht deklarierte capability: %s" % ", ".join(sorted(undecl)))
    out["dispatchable"] = dispatchable(bp)
    out["ok"] = not out["problems"]
    return out

def audit():

    return [driver_check(bp["id"]) for bp in load() if bp.get("id") and bp.get("kind") != "action"]

def _subst(obj, values):
    if isinstance(obj, str):
        def rep(m):
            k = m.group(1)
            if k not in values:
                raise KeyError(k)
            return str(values[k])
        return re.sub(r"\{(\w+)\}", rep, obj)
    if isinstance(obj, dict):
        return dict((k, _subst(v, values)) for k, v in obj.items())
    if isinstance(obj, _pylist):
        return [_subst(v, values) for v in obj]
    return obj

def plan(card_id, capability, params=None, role=None):

    bp = get(card_id)
    if bp is None:
        return {"ok": False, "error": "keine Card '%s'" % card_id}
    if capability not in (bp.get("capabilities") or []):
        return {"ok": False, "error": "Card '%s' deklariert die capability '%s' nicht" % (card_id, capability)}
    spec = (bp.get("invoke") or {}).get(capability)
    if not isinstance(spec, dict):
        return {"ok": False, "error": "Card '%s' hat kein invoke fuer '%s'" % (card_id, capability)}
    lane = spec.get("via")
    if lane not in LANES:
        return {"ok": False, "error": "unbekannte Bahn '%s' (erlaubt: %s)" % (lane, "/".join(LANES))}

    vals = dict((k, str(v)) for k, v in (spec.get("defaults") or {}).items())
    for k, v in (params or {}).items():
        if not isinstance(k, str) or not _PARAM_NAME.match(k):
            return {"ok": False, "error": "unzulaessiger Parametername"}
        v = str(v)
        if not _PARAM_VALUE.match(v):
            return {"ok": False, "error": "unzulaessiger Parameterwert fuer '%s'" % k}
        vals[k] = v
    addr = resolve_driver(card_id, role=role).get("address")
    vals.setdefault("address", addr or "")

    out = {"ok": True, "card": card_id, "capability": capability, "lane": lane, "address": addr}
    try:
        if lane == "display":
            did = spec.get("display") or _display_for(bp)
            if not did:
                return {"ok": False, "error": "kein Display in der Registry mit kind='%s'"
                        % ((bp.get("driver") or {}).get("display_kind"))}
            out["display"] = did
            out["ref"] = _subst(spec.get("ref") or {"kind": "text", "value": "{text}"}, vals)
        else:
            argv = spec.get("argv")
            if not isinstance(argv, _pylist) or not argv:
                return {"ok": False, "error": "invoke.argv fehlt"}
            path, prob = driver_file(bp)
            if prob:
                return {"ok": False, "error": prob}
            vals["driver_file"] = path
            vals.setdefault("python", "/usr/bin/python3")
            needs = spec.get("needs_address", (bp.get("driver") or {}).get("needs_address", True))
            if needs and not addr:
                return {"ok": False, "error": "keine Adresse aufloesbar (Registry- oder DEV_*-Bindung fehlt)"}
            out["argv"] = [str(x) for x in _subst(argv, vals)]
            out["tag"] = "blueprint:%s" % card_id
            if spec.get("mem"):
                out["mem"] = spec["mem"]
    except KeyError as e:
        return {"ok": False, "error": "Parameter fehlt: {%s}" % e.args[0]}
    return out
