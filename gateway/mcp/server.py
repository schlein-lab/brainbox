#!/usr/bin/env python3

from __future__ import annotations
import os, sys, json, time, urllib.request, urllib.error, ssl

_HERE = os.path.dirname(os.path.realpath(__file__))

def _load_totp():

    raw = (os.environ.get("BRAINARBEIT_TOTP") or "").strip()
    if not raw:
        return lambda: None
    if raw.isdigit() and len(raw) == 6:
        return lambda: raw

    sys.path.insert(0, os.path.dirname(_HERE))
    try:
        from gateway.server.auth import twofactor as _totp
    except Exception:
        return lambda: raw
    return lambda: _totp.code_at(raw)

_TOTP = _load_totp()
_BASE = (os.environ.get("BRAINARBEIT_URL") or "http://127.0.0.1:8810/v1").rstrip("/")
_DID = os.environ.get("BRAINARBEIT_DID", "")
_TOKEN = os.environ.get("BRAINARBEIT_TOKEN", "")

def _headers():
    h = {"Content-Type": "application/json"}
    if _DID and _TOKEN:
        h["Authorization"] = f"Bearer {_DID}.{_TOKEN}"
    code = _TOTP()
    if code:
        h["X-Brainarbeit-2FA"] = code
    return h

def _request(method, path, body=None, timeout=30):
    url = _BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    ctx = ssl.create_default_context() if url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def _load_tools():
    with open(os.path.join(_HERE, "tools.json")) as f:
        return json.load(f)["tools"]

_TOOLS = _load_tools()
_TOOL_NAMES = {t["name"] for t in _TOOLS}

def _dispatch(name: str, args: dict) -> dict:
    if name == "brainarbeit_submit":
        return _request("POST", "/jobs", args)
    if name == "brainarbeit_status":
        return _request("GET", f"/jobs/{int(args['id'])}/cvm")
    if name == "brainarbeit_result":
        return _request("GET", f"/jobs/{int(args['id'])}/result")
    if name == "brainarbeit_list_jobs":
        q = f"?limit={int(args.get('limit', 50))}"
        if args.get("state"):
            return _request("GET", f"/jobs/mine{q}&state={args['state']}")
        return _request("GET", f"/jobs/mine{q}")
    if name == "brainarbeit_pending_approvals":
        return _request("GET", "/approvals")
    if name == "brainarbeit_resolve_approval":
        return _request("POST", f"/approvals/{args['nonce']}",
                        {"decision": args["decision"], "feedback": args.get("feedback")})
    if name == "brainarbeit_steer":
        return _request("POST", f"/jobs/{int(args['id'])}/steer", {"input": args.get("input")})
    if name == "brainarbeit_cancel":
        return _request("POST", f"/jobs/{int(args['id'])}/cancel")
    if name == "brainarbeit_outputs":
        return _request("GET", f"/outputs?limit={int(args.get('limit', 200))}")
    if name == "brainarbeit_engine_status":
        return _request("GET", "/engine/status")
    return {"ok": False, "error": f"unknown tool {name}"}

PROTOCOL_VERSION = "2025-06-18"

def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}

def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}

def _handle(msg: dict):
    method = msg.get("method")
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "brainarbeit", "version": "1.0.0"},
            "instructions": ("Drive a Brainarbeit Brainbox: hand it tasks (brainarbeit_submit), "
                             "watch them (brainarbeit_status/result), and gate irreversible actions "
                             "through the human (brainarbeit_pending_approvals + "
                             "brainarbeit_resolve_approval). Never approve without the user's yes."),
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        tools = [{"name": t["name"], "description": t["description"],
                  "inputSchema": t["input_schema"]} for t in _TOOLS]
        return _result(id_, {"tools": tools})
    if method == "tools/call":
        name = params.get("name")
        if name not in _TOOL_NAMES:
            return _error(id_, -32602, f"unknown tool: {name}")
        args = params.get("arguments") or {}
        try:
            out = _dispatch(name, args)
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        is_error = isinstance(out, dict) and out.get("ok") is False
        return _result(id_, {
            "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
            "isError": is_error,
        })
    if id_ is not None:
        return _error(id_, -32601, f"method not found: {method}")
    return None

def main():

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
