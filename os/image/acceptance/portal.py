

import json
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)|"
    r"<title>500 |Internal Server Error|"
    r"\b(?:AttributeError|KeyError|TypeError|ValueError|NameError|ImportError|"
    r"OSError|RuntimeError)\b:",
    re.I,
)

class Response:
    def __init__(self, status, body, headers, url):
        self.status = status
        self.body = body
        self.headers = headers
        self.url = url

    @property
    def text(self):
        if isinstance(self.body, bytes):
            return self.body.decode("utf-8", "replace")
        return self.body or ""

    def json(self):
        try:
            return json.loads(self.text)
        except Exception:
            return None

    @property
    def has_traceback(self):

        ctype = (self.headers.get("content-type") or "").lower()
        if ctype and not any(t in ctype for t in ("text", "json", "javascript", "html", "xml")):
            return False
        return bool(TRACEBACK_RE.search(self.text[:20000]))

class Portal:
    def __init__(self, target, verbose=False):
        self.target = target.rstrip("/")
        self.verbose = verbose
        self.cookiejar = {}
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            _NoRedirect(),
        )

    @property
    def cookie_header(self):
        return "; ".join("%s=%s" % (k, v) for k, v in self.cookiejar.items())

    def _absorb_cookies(self, headers):
        for k, v in headers.items():
            if k.lower() != "set-cookie":
                continue
            for piece in v.split("\n"):
                nv = piece.split(";", 1)[0].strip()
                if "=" in nv:
                    name, _, val = nv.partition("=")
                    self.cookiejar[name.strip()] = val.strip()

    def url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.target + (path if path.startswith("/") else "/" + path)

    def ws_url(self, path):
        u = self.url(path)
        return "wss://" + u[len("https://"):] if u.startswith("https://") else "ws://" + u[len("http://"):]

    def request(self, method, path, data=None, ctype=None, timeout=20, headers=None):
        url = self.url(path)
        body = None
        hdrs = {"User-Agent": "brainbox-acceptance/1", "Accept": "*/*"}
        if self.cookiejar:
            hdrs["Cookie"] = self.cookie_header
        if isinstance(data, dict) and ctype == "json":
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
        elif isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(data, (bytes, str)):
            body = data.encode() if isinstance(data, str) else data
            if ctype:
                hdrs["Content-Type"] = ctype
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw = r.read()
                h = {k.lower(): v for k, v in r.headers.items()}
                self._absorb_cookies(_multi(r.headers))
                return Response(r.status, raw, h, url)
        except urllib.error.HTTPError as e:
            raw = e.read()
            h = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
            if e.headers:
                self._absorb_cookies(_multi(e.headers))
            return Response(e.code, raw, h, url)
        except Exception as e:
            return Response(0, ("transport: %s: %s" % (type(e).__name__, e)).encode(), {}, url)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, data=None, ctype="form", **kw):
        return self.request("POST", path, data=data, ctype=ctype, **kw)

    def login(self, user, password):

        r = self.post("/api/login", {"user": user, "password": password})
        ok = r.status in (200, 302, 303) and bool(self.cookiejar)
        if ok and r.status == 200:
            j = r.json()
            if isinstance(j, dict) and j.get("ok") is False:
                ok = False
        return ok, r

    def logged_in(self):
        r = self.get("/api/status")
        j = r.json()
        return bool(j and j.get("ok")), j or {}

class _NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_302

def _multi(headers):

    out = {}
    for k, v in headers.items():
        k = k.lower()
        out[k] = (out[k] + "\n" + v) if k in out else v
    return out

class Guest:
    def __init__(self, host, user, password_file=None, port=22, enabled=True, key_file=None):
        self.host = host
        self.user = user
        self.port = port
        self.password_file = password_file

        self.key_file = key_file
        self.enabled = enabled and bool(host)
        self._warned = None

    def run(self, script, timeout=45):

        if not self.enabled:
            return None
        base = [
            "ssh", "-p", str(self.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8",
            "-o", "LogLevel=ERROR",
        ]
        env = dict(os.environ)
        if self.key_file and os.path.exists(self.key_file):
            base += ["-i", self.key_file, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
        elif self.password_file and os.path.exists(self.password_file):
            with open(self.password_file) as f:
                env["SSHPASS"] = f.read().strip()
            base = ["sshpass", "-e"] + base + [
                "-o", "PreferredAuthentications=password",
                "-o", "PubkeyAuthentication=no",
            ]
        cmd = base + ["%s@%s" % (self.user, self.host), "sh -s"]
        try:
            p = subprocess.run(
                cmd, input=script.encode(), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=timeout, env=env,
            )
        except Exception as e:
            self._warned = str(e)
            return None
        if p.returncode != 0 and not p.stdout:
            self._warned = p.stderr.decode("utf-8", "replace").strip()[:200]
            return None
        return p.stdout.decode("utf-8", "replace")

    def available(self):
        return self.run("echo GUEST_OK") is not None
