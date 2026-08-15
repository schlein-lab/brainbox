

import argparse
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request

CSRF_RE = re.compile(r"""<meta\s+name=["']bbx-csrf["']\s+content=["']([^"']*)["']""", re.I)

STEPS_RE = re.compile(r"""var\s+STEPS\s*=\s*\[([^\]]*)\]""", re.S)

class Wizard:
    def __init__(self, base, claim_code=""):
        self.base = base.rstrip("/")
        self.claim_code = claim_code
        self.csrf = None
        self.cookies = {}
        self.steps = None

    def _open(self, path, data=None, timeout=90):
        url = self.base + path
        body = None
        hdrs = {"User-Agent": "brainbox-acceptance/1", "Accept": "*/*"}
        if data is not None:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
        if self.csrf:
            hdrs["X-CSRF-Token"] = self.csrf
        if self.cookies:
            hdrs["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        req = urllib.request.Request(url, data=body, headers=hdrs,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                for k, v in r.headers.items():
                    if k.lower() == "set-cookie":
                        nv = v.split(";", 1)[0]
                        if "=" in nv:
                            n, _, val = nv.partition("=")
                            self.cookies[n.strip()] = val.strip()
                return r.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, "%s: %s" % (type(e).__name__, e)

    def load(self):
        st, body = self._open("/")
        if st != 200:
            return False, "wizard page HTTP %s" % st
        m = CSRF_RE.search(body)
        if m:
            self.csrf = m.group(1)
        m = STEPS_RE.search(body)
        if m:
            self.steps = re.findall(r"""["']([^"']+)["']""", m.group(1))
        return True, "csrf=%s steps=%d" % ("yes" if self.csrf else "none",
                                          len(self.steps or []))

    def step(self, name, fields):
        st, body = self._open("/api/validate", {"step": name, "fields": fields})
        if st != 200:
            return False, "HTTP %s %s" % (st, body[:120])
        try:
            j = json.loads(body)
        except Exception:
            return False, "non-JSON: %s" % body[:100]
        if not j.get("ok"):
            return False, "errors: %s" % json.dumps(j.get("errors") or {})[:160]
        return True, "ok"

    def summary(self):

        st, body = self._open("/api/summary", {})
        if st != 200:
            return False, "HTTP %s %s" % (st, body[:120])
        try:
            j = json.loads(body)
        except Exception:
            return False, "non-JSON: %s" % body[:100]
        if not isinstance(j, dict) or "hostname" not in j:
            return False, "recap payload missing hostname: %s" % body[:100]
        return True, "ok"

    def apply(self):
        st, body = self._open("/api/apply", {"claim_code": self.claim_code}, timeout=600)
        if st != 200:
            return False, "HTTP %s %s" % (st, body[:150])
        try:
            j = json.loads(body)
        except Exception:
            return False, "non-JSON: %s" % body[:120]

        report = j.get("report") or []
        bad = [e for e in report if not e.get("ok")]
        for e in bad:
            if e.get("step") == "setup-complete":
                return False, ("setup-complete NOT written; failing steps: %s"
                               % json.dumps([{ "step": x.get("step"),
                                               "detail": str(x.get("detail"))[:80]}
                                             for x in bad])[:300])
        return bool(j.get("ok", True)), ("%d report steps, %d non-fatal failures"
                                         % (len(report), len(bad)))

def gen_pin(nbytes=4):

    return "".join(secrets.choice("0123456789") for _ in range(8))

def build_plan(args, pin):

    return {
        "language": {"LANG_UI": args.lang},

        "access": {k: v for k, v in {"ssh_keys": getattr(args, "ssh_key", ""),
                                     "claim_code": getattr(args, "claim_code", "")}.items() if v},
        "owner": {"owner_name": args.owner, "admin_pin": pin},
        "identity": {"hostname": args.hostname, "keymap": args.keymap},
        "network": {"net_mode": "dhcp"},

        "brain": {"brain_mode": "custom",
                  "base_url": "http://127.0.0.1:9/",
                  "llm_key": "acceptance-placeholder",
                  "model": "acceptance-model"},

        "features": {"CELLS_ENABLED": 1, "VOICE_ENABLED": 1},

        "advanced": {"HA_ENABLED": 0, "HA_VIP": "", "HA_VRID": "",
                     "VRRP_AUTH_PASS": "", "TELEGRAM_TOKEN": "",
                     "RELAY_ENABLED": 0, "PRINTER_ENABLED": 0,
                     "NAS_ENABLED": 0, "NAS_URL": "", "NAS_USER": "",
                     "NAS_PASS": "", "EMAIL_HOST": "", "EMAIL_USER": "",
                     "EMAIL_PASS": "", "EMAIL_FROM": ""},
    }

def wait_for(url, seconds, needle=None):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                body = r.read().decode("utf-8", "replace")
                if needle is None or needle.lower() in body.lower():
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False

def main(argv=None):
    ap = argparse.ArgumentParser(prog="acceptance.wizard",
                                 description="Complete the Brainbox first-run wizard over HTTP.")
    ap.add_argument("--wizard-url", required=True, help="http://host:port of the setup wizard")
    ap.add_argument("--creds-out", required=True,
                    help="write {'user':..,'password':..} here (mode 0600)")
    ap.add_argument("--owner", default="Acceptance Tester")
    ap.add_argument("--ssh-key", default="",
                    help="oeffentlicher Schluessel, den der Assistent hinterlegen soll "
                         "(eine Zeile) -- die Abnahme benutzt ihn danach fuer die Pruefungen IN der Box")
    ap.add_argument("--claim-code", default="",
                    help="Setup-Code vom Bildschirm der Box (Besitznachweis beim "
                         "Beanspruchen ueber Netz; localhost braucht ihn nicht)")
    ap.add_argument("--hostname", default="brainbox")
    ap.add_argument("--lang", default="de")
    ap.add_argument("--keymap", default="de")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for the wizard")
    ap.add_argument("--no-apply", action="store_true",
                    help="walk and validate every step but skip /api/apply "
                         "(standalone wizard verification without root)")
    args = ap.parse_args(argv)

    print("[wizard] waiting for %s" % args.wizard_url)
    if not wait_for(args.wizard_url + "/", args.wait, needle="brainbox"):
        print("[wizard] FAIL: wizard never came up")
        return 2

    w = Wizard(args.wizard_url, getattr(args, "claim_code", ""))
    ok, msg = w.load()
    print("[wizard] load: %s (%s)" % ("ok" if ok else "FAIL", msg))
    if not ok:
        return 2
    if not w.steps or "language" not in w.steps or "summary" not in w.steps:

        print("[wizard] FAIL: could not read STEPS from the served page (got %r) "
              "-- cannot prove every step advances" % (w.steps,))
        return 2
    print("[wizard] steps discovered from page: %s" % " -> ".join(w.steps))

    pin = gen_pin()
    plan = build_plan(args, pin)
    for name in w.steps:

        ok, msg = w.step(name, plan.get(name, {}))

        print("[wizard] step %-11s %s%s" % (name, "ok" if ok else "FAIL",
                                            "" if ok else " -- " + msg))
        if not ok:
            print("[wizard] FAIL: step '%s' cannot advance -- a real owner "
                  "would be STUCK on this card (%s)" % (name, msg))
            return 2

    ok, msg = w.summary()
    print("[wizard] summary recap: %s" % ("ok" if ok else "FAIL -- " + msg))
    if not ok:
        return 2

    if args.no_apply:
        print("[wizard] apply: SKIPPED (--no-apply)")
    else:
        ok, msg = w.apply()
        print("[wizard] apply: %s" % ("ok" if ok else "FAIL -- " + msg))
        if not ok:
            return 2

    fd = os.open(args.creds_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"user": args.owner, "password": pin}, f)
    print("[wizard] credentials written to %s (mode 0600, not printed)" % args.creds_out)
    return 0

if __name__ == "__main__":
    sys.exit(main())
