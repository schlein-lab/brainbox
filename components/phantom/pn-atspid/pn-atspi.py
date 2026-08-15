#!/usr/bin/env python3
"""pn-atspi — the thin client for pn-atspid (the AT-SPI control daemon).

Sends ONE JSON request to the daemon's unix socket and prints the JSON reply. This is
the phantomctl-equivalent for the control plane: a driver (a shell, a test, or — via the
rmcp shim — an LLM) speaks one verb and reads one result.

Usage:
  pn-atspi read_tree app=gtk text=1
  pn-atspi invoke app=gtk role="toggle button" name=Mute action=click
  pn-atspi click_at app=gtk role="push button" name=Send        # refused if invoke works
  pn-atspi press_keys app=gtk text="hello"
  pn-atspi mcp_schema
  echo '{"verb":"read_tree","app":"gtk"}' | pn-atspi -        # raw JSON on stdin

key=value args become the JSON request; verb is the first bare token. Values that look
like ints/bools/JSON are coerced; everything else stays a string.
"""
import json
import os
import socket
import sys

def coerce(v):
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v)
    except ValueError:
        pass
    if v and v[0] in "[{":
        try:
            return json.loads(v)
        except Exception:
            pass
    return v

def build_req(argv):
    req = {}
    for a in argv:
        if "=" in a:
            k, val = a.split("=", 1)
            req[k] = coerce(val)
        elif "verb" not in req:
            req["verb"] = a
        else:
            req.setdefault("_args", []).append(a)
    return req

def sock_path():
    return os.environ.get("PN_ATSPID_SOCK") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"), "pn-atspid.sock")

def main():
    args = sys.argv[1:]
    if not args:
        sys.stderr.write(__doc__)
        sys.exit(2)
    if args == ["-"]:
        req = json.loads(sys.stdin.read())
    else:
        req = build_req(args)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(20.0)
    s.connect(sock_path())
    s.sendall((json.dumps(req) + "\n").encode())
    s.shutdown(socket.SHUT_WR)
    buf = b""
    while True:
        b = s.recv(65536)
        if not b:
            break
        buf += b
    s.close()
    sys.stdout.write(buf.decode("utf-8", "replace"))

if __name__ == "__main__":
    main()
