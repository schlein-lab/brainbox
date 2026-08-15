#!/usr/bin/env python3

import json, os, secrets, ssl, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))
import apikeys

DATA_DIR = os.path.expanduser("~/.local/share/brainbox-portal")
BASE = "https://127.0.0.1:8077"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
ks = apikeys.KeyStore(DATA_DIR)

made = []
def mint(label, scopes):
    kid, raw = ks.create("owner", label=label, scopes=scopes)
    made.append((kid, label))

    stored = {e["id"]: e for e in ks.list()}.get(kid, {})
    if (stored.get("scopes") or []) != scopes:
        print("!! WARNING scope mismatch for %s: stored=%r wanted=%r" % (label, stored.get("scopes"), scopes))
    return raw

def call(method, path, key=None, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if key: req.add_header("Authorization", "Bearer " + key)
    if body is not None: req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items(): req.add_header(k, v)
    try:
        r = urllib.request.urlopen(req, context=CTX, timeout=40)
        raw = r.read().decode()
        try: return r.status, json.loads(raw)
        except Exception: return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw

checks = {}
try:
    full = mint("v1-e2e-full", ["/api/v1"])
    submit_only = mint("v1-e2e-submit", ["POST /api/v1/jobs"])
    read_only = mint("v1-e2e-read", ["GET /api/v1"])

    st, cap = call("GET", "/api/v1/capabilities", full)
    checks["capabilities 200 + principal owner + commission kind"] = (
        st == 200 and cap.get("principal") == "owner" and "commission" in (cap.get("kinds") or {}))

    IDEM = "e2e-" + secrets.token_hex(4)
    st, job = call("POST", "/api/v1/jobs", full, {"prompt": "sag kurz hallo und sonst nichts"},
                   {"Idempotency-Key": IDEM})
    jid = (job or {}).get("id", "")
    checks["submit -> 202 + job_ id + state queued"] = (
        st == 202 and jid.startswith("job_") and (job.get("status") or {}).get("state") == "queued")

    st2, job2 = call("POST", "/api/v1/jobs", full, {"prompt": "different text, same idem key"},
                     {"Idempotency-Key": IDEM})
    checks["idempotent replay -> 200 + SAME id"] = (st2 == 200 and job2.get("id") == jid)

    st, got = call("GET", "/api/v1/jobs/" + jid, full)
    checks["get job -> 200 + same id"] = (st == 200 and got.get("id") == jid)

    st, lst = call("GET", "/api/v1/jobs?limit=50", full)
    checks["list -> job present"] = (st == 200 and any(d.get("id") == jid for d in (lst.get("data") or [])))

    st, ev = call("GET", "/api/v1/jobs/" + jid + "/events?since=-1", full)
    checks["events -> seq + state"] = (st == 200 and "seq" in ev and "state" in ev)

    st, prob = call("GET", "/api/v1/capabilities", submit_only)
    checks["submit-only key GET -> 403 insufficient_scope"] = (
        st == 403 and (prob or {}).get("code") == "insufficient_scope")

    st, _ = call("POST", "/api/v1/jobs", submit_only, {"prompt": "hallo von submit-only"})
    checks["submit-only key POST -> 202"] = (st == 202)

    st, prob = call("POST", "/api/v1/jobs", read_only, {"prompt": "sollte scheitern"})
    checks["read-only key POST -> 403 insufficient_scope"] = (
        st == 403 and (prob or {}).get("code") == "insufficient_scope")

    st, _ = call("GET", "/api/v1/capabilities", read_only)
    checks["read-only key GET capabilities -> 200"] = (st == 200)

    st, prob = call("GET", "/api/v1/capabilities")
    checks["no key -> 401 unauthorized problem"] = (
        st == 401 and (prob or {}).get("code") == "unauthorized")

    st, prob = call("POST", "/api/v1/jobs", full, {"kind": "commission"})
    checks["missing prompt -> 422 missing_prompt"] = (
        st == 422 and (prob or {}).get("code") == "missing_prompt")

    st, prob = call("POST", "/api/v1/jobs", full, {"prompt": "x", "kind": "nonsense"})
    checks["unknown kind -> 422 unknown_kind"] = (
        st == 422 and (prob or {}).get("code") == "unknown_kind")

    st, jc = call("POST", "/api/v1/jobs", full, {"prompt": "cancel me"})
    cjid = (jc or {}).get("id", "")
    st, cancel = call("POST", "/api/v1/jobs/" + cjid + "/cancel", full)
    ok_cancel = (st == 200 and (cancel.get("status") or {}).get("state") == "canceled") or \
                (st == 409 and (cancel or {}).get("code") in ("already_running", "already_finished"))
    checks["cancel -> canceled OR honest 409"] = ok_cancel

    print("primary job:", jid, "| submit body type:", type(job).__name__, "| get type:", type(got).__name__)
    print("\n===== VERDICT =====")
    for k, v in checks.items():
        print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    fails = [k for k, v in checks.items() if not v]
    print("  RESULT:", "V1_E2E_PROVEN" if not fails else ("NEEDS_LOOK — failing: " + "; ".join(fails)))
finally:

    revoked = 0
    for kid, _label in made:
        try:
            ks.revoke(kid); revoked += 1
        except Exception:
            pass
    print("cleanup: revoked", revoked, "test keys")
