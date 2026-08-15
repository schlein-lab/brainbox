#!/usr/bin/env python3

import json, os, ssl, sys, urllib.request, urllib.error

sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))
import apikeys

DATA_DIR = os.path.expanduser("~/.local/share/brainbox-portal")
BASE = "https://127.0.0.1:8077"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
ks = apikeys.KeyStore(DATA_DIR)
made = []

def mint(label, scopes):
    kid, raw = ks.create("owner", label=label, scopes=scopes)
    made.append(kid)
    return raw

def rpc(rid, method, params=None, key=None):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(BASE + "/mcp", data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        r = urllib.request.urlopen(req, context=CTX, timeout=40)
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip() else {})

def tool(key, name, args=None):
    st, resp = rpc(1, "tools/call", {"name": name, "arguments": args or {}}, key=key)
    res = (resp or {}).get("result") or {}
    is_err = bool(res.get("isError"))
    text = ""
    for c in res.get("content", []):
        if c.get("type") == "text":
            text = c.get("text", "")
    payload = None
    if not is_err:
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
    return st, is_err, payload, text

checks = {}
try:
    full = mint("mcp-e2e-full", ["/api/v1"])
    submit_only = mint("mcp-e2e-submit", ["POST /api/v1/jobs"])

    st, resp = rpc(1, "initialize", {})
    r = (resp or {}).get("result") or {}
    checks["initialize -> protocolVersion + serverInfo"] = (
        st == 200 and r.get("protocolVersion") and (r.get("serverInfo") or {}).get("name") == "brainbox")

    st, resp = rpc(2, "tools/list", {})
    names = [t["name"] for t in ((resp or {}).get("result") or {}).get("tools", [])]
    checks["tools/list -> submit_job+get_job present"] = ("submit_job" in names and "get_job" in names)

    st, is_err, _p, text = tool(None, "submit_job", {"prompt": "x"})
    checks["no key tools/call -> isError unauthorized"] = (is_err and "nauthorized" in text)

    st, is_err, p, text = tool(full, "submit_job", {"prompt": "sag knapp hallo (mcp)",
                                                    "idempotency_key": "mcp-e2e-1"})
    jid = (p or {}).get("id", "") if p else ""
    checks["submit_job -> job_ id + queued"] = (
        not is_err and jid.startswith("job_") and (p.get("status") or {}).get("state") == "queued")

    st, is_err, p2, _ = tool(full, "submit_job", {"prompt": "anders", "idempotency_key": "mcp-e2e-1"})
    checks["submit_job idempotent -> same id"] = (not is_err and p2 and p2.get("id") == jid)

    st, is_err, p, _ = tool(full, "get_job", {"id": jid})
    checks["get_job -> same id"] = (not is_err and p and p.get("id") == jid)

    st, is_err, p, _ = tool(full, "capabilities", {})
    checks["capabilities tool -> principal owner"] = (not is_err and p and p.get("principal") == "owner")

    st, is_err, p, _ = tool(submit_only, "submit_job", {"prompt": "submit only via mcp"})
    checks["submit-only key submit_job -> ok"] = (not is_err and p and str(p.get("id", "")).startswith("job_"))

    st, is_err, _p, text = tool(submit_only, "get_job", {"id": jid})
    checks["submit-only key get_job -> isError scope"] = (is_err and "scoped" in text.lower())

    st, is_err, p, _ = tool(full, "list_jobs", {"limit": 50})
    checks["list_jobs -> job present"] = (
        not is_err and p and any(d.get("id") == jid for d in (p.get("data") or [])))

    st, resp = rpc(1, "tools/call", {"name": "no_such_tool", "arguments": {}}, key=full)
    checks["unknown tool -> jsonrpc error"] = ("error" in (resp or {}))

    print("mcp primary job:", jid)
    print("\n===== VERDICT =====")
    for k, v in checks.items():
        print("  [%s] %s" % ("PASS" if v else "FAIL", k))
    fails = [k for k, v in checks.items() if not v]
    print("  RESULT:", "MCP_E2E_PROVEN" if not fails else ("NEEDS_LOOK: " + "; ".join(fails)))
finally:
    n = 0
    for kid in made:
        try:
            ks.revoke(kid); n += 1
        except Exception:
            pass
    print("cleanup: revoked", n, "test keys")
