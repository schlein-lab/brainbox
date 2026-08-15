#!/usr/bin/env python3

import argparse
import json
import sys

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

def get_text(o):
    for end in (None, -1):
        try:
            n = o.get_character_count() if end is None else -1
            return o.get_text(0, n)
        except Exception:
            continue
    return None

def app_by(token):
    d = desktop()
    if token.isdigit():
        i = int(token)
        return d.get_child_at_index(i) if 0 <= i < d.get_child_count() else None
    tl = token.lower()
    best = None
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        nm = (name(a)).lower()
        if nm == tl:
            return a
        if tl in nm and best is None:
            best = a
    return best

def _resolve_query(rootapp, query):

    root = app_by(rootapp)
    if root is None:
        return None
    want = {}
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            want[k.strip().lower()] = v.strip().lower()
    budget = [20000]
    stack = [root]
    while stack and budget[0] > 0:
        o = stack.pop()
        budget[0] -= 1
        ok = True
        if "role" in want and want["role"] not in role(o).lower():
            ok = False
        if ok and "name" in want and want["name"] not in name(o).lower():
            ok = False
        if ok and "editable" in want and "editable" not in states(o):
            ok = False
        if ok and (("role" in want) or ("name" in want) or ("editable" in want)):
            return o
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, 200)):
            try:
                stack.append(o.get_child_at_index(j))
            except Exception:
                pass
    return None

def resolve(selector):

    if selector is None:
        return None
    if "?" in selector:
        app, query = selector.split("?", 1)
        return _resolve_query(app, query)
    parts = [p for p in selector.split("/") if p != ""]
    if not parts:
        return None
    node = app_by(parts[0])
    for seg in parts[1:]:
        if node is None:
            return None
        try:
            node = node.get_child_at_index(int(seg))
        except Exception:
            return None
    return node

def node_dict(o, path, want_text=False):
    d = {
        "path": path,
        "role": role(o),
        "name": name(o),
        "states": states(o),
        "actions": actions(o),
    }
    if want_text:
        t = get_text(o)
        if t is not None:
            d["text"] = t[:400]
    return d

def out(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.write("\n")

def cmd_list(a):
    d = desktop()
    apps = []
    for i in range(d.get_child_count()):
        ap = d.get_child_at_index(i)
        apps.append({"idx": i, "name": name(ap), "role": role(ap),
                     "kids": ap.get_child_count()})
    out({"ok": True, "apps": apps})

def cmd_tree(a):
    root = resolve(a.sel)
    if root is None:
        out({"ok": False, "error": "not found", "sel": a.sel})
        return
    budget = [a.max]
    nodes = []
    base = a.sel.split("?", 1)[0].rstrip("/")

    def walk(o, prefix, depth):
        if budget[0] <= 0 or depth > a.depth:
            return
        budget[0] -= 1
        nodes.append(node_dict(o, prefix, a.text))
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, a.fanout)):
            try:
                walk(o.get_child_at_index(j), prefix + "/" + str(j), depth + 1)
            except Exception:
                pass

    walk(root, base, 0)
    out({"ok": True, "nodes": nodes})

def cmd_read(a):
    o = resolve(a.sel)
    if o is None:
        out({"ok": False, "error": "not found", "sel": a.sel})
        return
    t = get_text(o)
    out({"ok": t is not None, "text": t if t is not None else ""})

def cmd_write(a):
    o = resolve(a.sel)
    if o is None:
        out({"ok": False, "error": "not found", "sel": a.sel})
        return

    try:
        ok = o.set_text_contents(a.text)
        if ok:
            out({"ok": True, "method": "set_text_contents"})
            return
    except Exception as e1:
        try:
            o.insert_text(0, a.text, len(a.text))
            out({"ok": True, "method": "insert_text"})
            return
        except Exception as e2:
            out({"ok": False, "error": f"{e1} / {e2}"})
            return

    try:
        o.insert_text(0, a.text, len(a.text))
        out({"ok": True, "method": "insert_text"})
    except Exception as e:
        out({"ok": False, "error": f"set_text_contents False, insert failed: {e}"})

def cmd_action(a):
    o = resolve(a.sel)
    if o is None:
        out({"ok": False, "error": "not found", "sel": a.sel})
        return
    try:
        n = o.get_n_actions()
    except Exception as e:
        out({"ok": False, "error": f"no action interface: {e}"})
        return
    if a.action is None:
        out({"ok": True, "actions": [o.get_action_name(i) or "" for i in range(n)]})
        return
    for i in range(n):
        an = o.get_action_name(i) or ""
        if (a.action.isdigit() and int(a.action) == i) or a.action.lower() in an.lower():
            res = o.do_action(i)
            out({"ok": bool(res), "did": an, "index": i})
            return
    out({"ok": False, "error": "action not found", "action": a.action})

def cmd_find(a):
    root = app_by(a.app)
    if root is None:
        out({"ok": False, "error": "app not found", "app": a.app})
        return
    budget = [a.max_scan]
    hits = []

    def walk(o, path, depth):
        if budget[0] <= 0 or len(hits) >= a.limit or depth > 40:
            return
        budget[0] -= 1
        ok = (a.role or a.name or a.editable)
        if a.role and a.role.lower() not in role(o).lower():
            ok = False
        if a.name and a.name.lower() not in name(o).lower():
            ok = False
        if a.editable and "editable" not in states(o):
            ok = False
        if ok:
            hits.append(node_dict(o, path))
        try:
            cc = o.get_child_count()
        except Exception:
            cc = 0
        for j in range(min(cc, a.fanout)):
            try:
                walk(o.get_child_at_index(j), path + "/" + str(j), depth + 1)
            except Exception:
                pass

    walk(root, a.app, 0)
    out({"ok": True, "hits": hits})

def main():
    p = argparse.ArgumentParser(prog="phantom-atspi-helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")

    t = sub.add_parser("tree")
    t.add_argument("sel")
    t.add_argument("--max", type=int, default=400)
    t.add_argument("--depth", type=int, default=25)
    t.add_argument("--fanout", type=int, default=80)
    t.add_argument("--text", action="store_true")

    r = sub.add_parser("read")
    r.add_argument("sel")

    w = sub.add_parser("write")
    w.add_argument("sel")
    w.add_argument("text")

    ac = sub.add_parser("action")
    ac.add_argument("sel")
    ac.add_argument("action", nargs="?")

    f = sub.add_parser("find")
    f.add_argument("app")
    f.add_argument("--role")
    f.add_argument("--name")
    f.add_argument("--editable", action="store_true")
    f.add_argument("--limit", type=int, default=50)
    f.add_argument("--max-scan", dest="max_scan", type=int, default=40000)
    f.add_argument("--fanout", type=int, default=120)

    a = p.parse_args()
    try:
        {
            "list": cmd_list, "tree": cmd_tree, "read": cmd_read,
            "write": cmd_write, "action": cmd_action, "find": cmd_find,
        }[a.cmd](a)
    except Exception as e:
        out({"ok": False, "error": f"engine: {e}"})
        sys.exit(0)

if __name__ == "__main__":
    main()
