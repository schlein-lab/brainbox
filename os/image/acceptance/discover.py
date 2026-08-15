

import json
import re

API_RE = re.compile(r"""["'`](/api/[A-Za-z0-9_\-/.]*)""")
WS_RE = re.compile(r"""["'`](/ws/[A-Za-z0-9_\-/.]*)""")
ASSET_RE = re.compile(r"""\b(?:src|href)\s*=\s*["'](/[^"'#?]*)["']""", re.I)
BUTTON_RE = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.I | re.S)
ONCLICK_RE = re.compile(r"""\bon(?:click|change|submit)\s*=\s*["']([^"']+)["']""", re.I)
ID_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.I)
DATAGO_RE = re.compile(r"""\bdata-(?:go|lens)\s*=\s*["']([^"']+)["']""", re.I)
NAVLINK_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["'](#[A-Za-z0-9_\-]+)["']""", re.I)

_HELPER_GET = r"jget|aget|getJSON"
_HELPER_POST = r"jpost|apost|postJSON"
METHOD_PATTERNS = [
    (re.compile(r"""\b(?:%s)\(\s*["'`](/(?:api|ws)/[^"'`]*)""" % _HELPER_GET), "GET"),
    (re.compile(r"""\b(?:%s)\(\s*["'`](/(?:api|ws)/[^"'`]*)""" % _HELPER_POST), "POST"),

    (re.compile(r"""\bapi\(\s*["'`](GET|POST|PUT|DELETE|PATCH)["'`]\s*,\s*["'`](/[^"'`]*)"""), "@1"),

    (re.compile(r"""\b(?:api|fetch)\(\s*["'`](/(?:api|ws)/[^"'`]*)["'`]\s*,\s*\{[^{}]{0,200}?method\s*:\s*["'`]([A-Za-z]+)["'`]""", re.S), "@2"),

    (re.compile(r"""\b(?:api|fetch)\(\s*["'`](/(?:api|ws)/[^"'`]*)["'`]\s*\)"""), "GET"),

    (re.compile(r"""\b(GET|POST|PUT|DELETE|PATCH)\s+(/(?:api|ws)/[A-Za-z0-9_\-/.]+)"""), "@1"),
]

FETCHABLE = (".js", ".html", ".htm", ".json", ".webmanifest", ".mjs")

DESTRUCTIVE = re.compile(
    r"/(logout|shutdown|reboot|poweroff|power|factory|wipe|erase|reset|kill)\b", re.I
)

SAFE_POST = re.compile(r"/(status|summary|list|check|test|ping|validate)\b", re.I)

class Control:
    def __init__(self, kind, label, handler=None, source=""):
        self.kind = kind
        self.label = label
        self.handler = handler
        self.source = source
        self.endpoints = []
        self.mapped_by = None

    def as_dict(self):
        return {"kind": self.kind, "label": self.label, "handler": self.handler,
                "source": self.source, "endpoints": sorted(self.endpoints),
                "mapped_by": self.mapped_by}

class Surface:
    def __init__(self):
        self.assets = {}
        self.api = set()
        self.ws = set()
        self.methods = {}
        self.controls = []
        self.docs = {}
        self.errors = []

    def method_for(self, ep):
        m = self.methods.get(ep) or set()
        if "GET" in m:
            return "GET"
        if m:
            return sorted(m)[0]
        return None

    def mapped_fraction(self):
        if not self.controls:
            return 0.0, 0, 0
        n = len([c for c in self.controls if c.endpoints])
        return (n / float(len(self.controls))), n, len(self.controls)

def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

def _balanced_body(text, start, limit=24000):
    i = text.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, min(len(text), i + limit)):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:i + 4000]

def _find_function_body(js, name):

    if not name or len(name) < 2:
        return ""
    n = re.escape(name)
    pats = [
        r"\bfunction\s+%s\s*\(" % n,
        r"\b%s\s*[:=]\s*(?:async\s*)?function\s*\(" % n,
        r"\b%s\s*[:=]\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{" % n,
        r"\b(?:async\s+)?%s\s*\([^)]*\)\s*\{" % n,
    ]
    for p in pats:
        m = re.search(p, js)
        if m:
            body = _balanced_body(js, m.end() - 1)
            if body:
                return body
    return ""

def _endpoints_in(text):
    eps = set(m.group(1) for m in API_RE.finditer(text))
    eps |= set(m.group(1) for m in WS_RE.finditer(text))
    return eps

def _resolve(js, names, depth=2, seen=None):

    seen = seen if seen is not None else set()
    found = set()
    for name in names:
        if name in seen or len(seen) > 40:
            continue
        seen.add(name)
        body = _find_function_body(js, name)
        if not body:
            continue
        found |= _endpoints_in(body)
        if not found and depth > 0:
            inner = set(re.findall(r"\bthis\.([A-Za-z_$][\w$]*)\s*\(", body))
            inner |= set(re.findall(r"\b([A-Za-z_$][\w$]{2,})\s*\(", body))
            inner -= {"if", "for", "while", "switch", "catch", "return", "function",
                      "typeof", "console", "parseInt", "parseFloat", "String",
                      "Number", "Boolean", "Array", "Object", "JSON", "setTimeout",
                      "setInterval", "querySelector", "getElementById", "addEventListener"}
            found |= _resolve(js, list(inner)[:12], depth - 1, seen)
        if found:
            break
    return found

def collect(portal, roots=("/",), extra_pages=()):
    surf = Surface()
    seen = set()
    queue = list(roots) + list(extra_pages)

    while queue:
        path = queue.pop(0)
        key = path.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        r = portal.get(path)
        surf.assets[key] = r.status
        if r.status != 200:
            continue
        ctype = (r.headers.get("content-type") or "").lower()
        is_text = any(t in ctype for t in ("text", "json", "javascript", "html", "xml")) \
            or key.endswith(FETCHABLE)
        if not is_text:
            continue
        text = r.text
        surf.docs[key] = text
        for m in API_RE.finditer(text):
            surf.api.add(m.group(1))
        for m in WS_RE.finditer(text):
            surf.ws.add(m.group(1))
        for m in ASSET_RE.finditer(text):
            a = m.group(1).split("?")[0]
            if a in seen:
                continue

            if a.endswith("/") and a != "/":
                continue
            surf.assets.setdefault(a, None)
            if a.endswith(FETCHABLE):
                queue.append(a)

    for a, st in list(surf.assets.items()):
        if st is None:
            surf.assets[a] = portal.get(a).status

    js_blob = "\n".join(v for k, v in surf.docs.items() if k.endswith((".js", ".mjs")))
    html_blob = "\n".join(v for k, v in surf.docs.items() if not k.endswith((".js", ".mjs")))
    all_blob = js_blob + "\n" + html_blob

    _extract_methods(surf, all_blob)
    _extract_controls(surf, html_blob, all_blob)
    return surf

def _extract_methods(surf, blob):

    for rx, how in METHOD_PATTERNS:
        for m in rx.finditer(blob):
            if how == "@1":
                meth, ep = m.group(1), m.group(2)
            elif how == "@2":
                ep, meth = m.group(1), m.group(2)
            else:
                ep, meth = m.group(1), how
            surf.methods.setdefault(ep, set()).add(meth.upper())

def _extract_controls(surf, html, js):

    for m in BUTTON_RE.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        oc = ONCLICK_RE.search(attrs)
        idm = ID_RE.search(attrs)
        dg = DATAGO_RE.search(attrs)
        label = _strip_tags(inner)[:40] or (idm.group(1) if idm else "?")
        handler = oc.group(1).strip() if oc else ("#" + idm.group(1) if idm else
                                                  ("go:" + dg.group(1) if dg else None))
        surf.controls.append(Control("button", label, handler, "html"))

    for m in NAVLINK_RE.finditer(html):
        surf.controls.append(Control("nav", m.group(1), m.group(1), "html"))
    for m in DATAGO_RE.finditer(html):
        surf.controls.append(Control("nav", "#" + m.group(1), "#" + m.group(1), "html"))

    for m in re.finditer(r"""addEventListener\(\s*["'](?:click|change|submit)["']\s*,""", js):
        back = js[max(0, m.start() - 220):m.start()]
        ids = re.findall(r"""(?:\$|getElementById)\(\s*["']#?([A-Za-z0-9_-]+)["']\s*\)""", back)
        cid = ids[-1] if ids else None
        fwd = js[m.end():m.end() + 300]
        c = Control("listener", "#" + cid if cid else "(anon)", "#" + cid if cid else None, "js")

        eps = _endpoints_in(fwd)
        if not eps:
            names = re.findall(r"\bthis\.([A-Za-z_$][\w$]*)\s*\(", fwd)
            names += re.findall(r"=>\s*([A-Za-z_$][\w$]*)\s*\(", fwd)
            eps = _resolve(js, names)
        if eps:
            c.endpoints = sorted(eps)
            c.mapped_by = "auto"
        surf.controls.append(c)

    for c in surf.controls:
        if c.endpoints or not c.handler:
            continue
        h = c.handler
        if h.startswith("go:") or h.startswith("#"):
            key = h.lstrip("#").replace("go:", "")

            cands = [key, "render" + key.capitalize(), "show" + key.capitalize(),
                     key + "Lens", "lens" + key.capitalize()]
            eps = _resolve(js, cands)
            if eps:
                c.endpoints = sorted(eps)
                c.mapped_by = "auto"
                continue
        names = []
        mm = re.search(r"([A-Za-z_$][\w$]*)\s*\(", h)
        if mm:
            names.append(mm.group(1))
        names += [n for n in re.findall(r"[A-Za-z_$][\w$]*", h)
                  if n not in ("APP", "window", "document", "this", "location", "href")]
        eps = _resolve(js, names)
        if eps:
            c.endpoints = sorted(eps)
            c.mapped_by = "auto"

    for c in surf.controls:
        if c.endpoints:
            continue
        key = (c.handler or c.label or "").lstrip("#.").replace("go:", "").strip()
        ann = ANNOTATIONS.get(key) or ANNOTATIONS.get(c.label.strip())
        if ann:
            c.endpoints = list(ann)
            c.mapped_by = "annotation"

ANNOTATIONS = {
    "sessions": ["/api/sessions", "/api/session/board"],
    "screen": ["/api/screen/apps", "/api/screen/start"],
    "work": ["/api/jobs", "/api/queue"],
    "admin": ["/api/admin/overview", "/api/admin/users"],
    "stats": ["/api/admin/stats"],
    "settings": ["/api/keys", "/api/policy"],
    "devices": ["/api/devices"],
    "start": ["/api/status", "/api/overview"],
    "setClaude": ["/api/admin/llm/oauth/start"],
    "setVault": ["/api/vault/blob"],
    "setLogout": ["/api/logout"],
}

CLIENT_ONLY = {"themeBtn", "paletteBtn", "remoteClose", "(anon)"}

def probe_endpoints(portal, surf, post_probe=False):

    results = {}
    for ep in sorted(surf.api | surf.ws):
        meth = surf.method_for(ep)

        if ep.endswith("/") or ep.rstrip("/").count("/") < 2:
            results[ep] = {"verdict": "base", "status": None, "method": meth,
                           "note": "runtime-concatenated base path"}
            continue
        if DESTRUCTIVE.search(ep):
            results[ep] = {"verdict": "unprobed", "status": None, "method": meth,
                           "note": "destructive - not called by the harness"}
            continue

        if meth == "GET":

            results[ep] = _verdict(r=portal.get(ep, timeout=15), meth="GET")
            continue

        if post_probe and (not DESTRUCTIVE.search(ep)):
            r = portal.post(ep, {}, ctype="json", timeout=20)

            v = _verdict(r, "POST")
            if v["verdict"] == "missing":

                v2 = _verdict(portal.get(ep, timeout=15), "GET")
                if v2["verdict"] != "missing":
                    v2["note"] = "GET works (POST 404) -- method mis-guessed, handler live"
                    v = v2
            results[ep] = v
        else:
            r = portal.get(ep, timeout=15)
            v = _verdict(r, "GET")
            if v["verdict"] == "missing":
                v = {"verdict": "unverified", "status": v["status"], "method": meth or "unknown",
                     "note": "%s route; GET 404 is inconclusive (use --post-probe)"
                             % (meth or "method-unknown")}
            results[ep] = v
    return results

def _verdict(r, meth):
    st = r.status
    if r.has_traceback:
        return {"verdict": "broken", "status": st, "method": meth, "note": _first_exc(r.text)}
    if st == 0:
        return {"verdict": "broken", "status": st, "method": meth, "note": r.text[:80]}
    if st == 404:

        j = None
        try:
            j = json.loads(r.text)
        except Exception:
            pass
        if isinstance(j, dict) and (j.get("error") or j.get("reason")):
            return {"verdict": "ok", "status": st, "method": meth,
                    "note": "404 with handler error: %s" % str(j.get("error") or j.get("reason"))[:40]}
        return {"verdict": "missing", "status": st, "method": meth, "note": r.text.strip()[:60]}
    if st >= 500:

        j = None
        try:
            j = json.loads(r.text)
        except Exception:
            pass
        reason = (j or {}).get("error") or (j or {}).get("reason") if isinstance(j, dict) else None
        if st != 500 and reason:
            return {"verdict": "degraded", "status": st, "method": meth,
                    "note": "HTTP %d, honest reason: %s" % (st, str(reason)[:60])}
        return {"verdict": "broken", "status": st, "method": meth, "note": "HTTP %d" % st}
    if st in (400, 401, 403, 405, 409, 422):
        return {"verdict": "ok", "status": st, "method": meth,
                "note": "HTTP %d (handler present)" % st}
    return {"verdict": "ok", "status": st, "method": meth, "note": "HTTP %d" % st}

def _first_exc(text):
    for line in text.splitlines():
        if re.match(r"^\s*\w*(Error|Exception)\b", line) or "Traceback" in line:
            return line.strip()[:90]
    return "traceback in response body"
