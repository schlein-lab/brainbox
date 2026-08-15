#!/usr/bin/env python3

import sys, os, json, socket, struct, threading

PROTO_FALLBACK = "2025-06-18"
PRIMER = ("This cell can see its own screen and drive real programs via phantom MCP tools "
          "(read_screen, drive_program, inject_input, list_capabilities). EVERY call is a governed "
          "action: it may be QUEUED (you get a position — wait, do not retry-loop) or DENIED (you get a "
          "reason — respect it, do not route around it). Call list_capabilities first if unsure, and "
          "read_screen before you drive or inject.")

def elog(*a):
    sys.stderr.write("[pn-cell-mcp] " + " ".join(str(x) for x in a) + "\n"); sys.stderr.flush()

_lane_lock = threading.Lock()
_lane = None

def _connect_lane():
    t = os.environ.get("PN_MCP_TRANSPORT", "vsock:2:9400")
    kind, rest = t.split(":", 1)
    if kind == "vsock":
        cid, port = rest.split(":"); s = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        s.connect((int(cid), int(port)))
    elif kind == "tcp":
        host, port = rest.rsplit(":", 1); s = socket.create_connection((host, int(port)), timeout=None)
    elif kind == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(rest)
    else:
        raise ValueError("bad PN_MCP_TRANSPORT " + t)
    return s

def _lane_send(sock, obj):
    b = json.dumps(obj, separators=(",", ":")).encode()
    sock.sendall(struct.pack("!I", len(b)) + b)

def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        d = sock.recv(n - len(buf))
        if not d: raise ConnectionError("lane closed")
        buf += d
    return buf

def _lane_recv(sock):
    (ln,) = struct.unpack("!I", _recvn(sock, 4))
    return json.loads(_recvn(sock, ln))

def host_call(req_obj, on_progress=None):

    global _lane
    for attempt in (1, 2):
        try:
            with _lane_lock:
                if _lane is None:
                    _lane = _connect_lane()
                _lane_send(_lane, req_obj)
                while True:
                    f = _lane_recv(_lane)
                    if f.get("status") == "queued":
                        if on_progress: on_progress(f)
                        continue
                    return f
        except Exception as e:
            elog("lane error (attempt %d): %r" % (attempt, e))
            with _lane_lock:
                try:
                    if _lane: _lane.close()
                except Exception: pass
                _lane = None
            if attempt == 2:
                return {"status": "error", "reason": "host reference monitor unreachable"}

def send(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n"); sys.stdout.flush()

def result(id_, res): send({"jsonrpc": "2.0", "id": id_, "result": res})
def error(id_, code, msg): send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}})

def to_content(f):

    st = f.get("status")
    if st == "ok":
        out = []
        if isinstance(f.get("result"), (dict, list)):
            out.append({"type": "text", "text": json.dumps(f["result"], ensure_ascii=False)})
            return {"content": out, "structuredContent": f["result"], "isError": False}
        out.append({"type": "text", "text": str(f.get("result", ""))})
        return {"content": out, "isError": False}
    if st == "denied":
        return {"content": [{"type": "text", "text": "denied: " + str(f.get("reason", "policy"))}], "isError": True}
    return {"content": [{"type": "text", "text": str(f.get("reason", "error"))}], "isError": True}

def handle(msg):
    m = msg.get("method"); id_ = msg.get("id")
    if m == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or PROTO_FALLBACK
        result(id_, {"protocolVersion": proto,
                     "capabilities": {"tools": {"listChanged": True}},
                     "serverInfo": {"name": "pn-phantom", "version": "1.0.0"},
                     "instructions": PRIMER})
    elif m == "notifications/initialized":
        pass
    elif m == "ping":
        result(id_, {})
    elif m == "tools/list":
        f = host_call({"op": "list_tools"})
        if f.get("status") == "ok":
            result(id_, {"tools": f.get("result", [])})
        else:
            result(id_, {"tools": []})
    elif m == "tools/call":
        p = msg.get("params") or {}
        name = p.get("name"); args = p.get("arguments") or {}
        def prog(fr):

            send({"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {"progressToken": id_, "message": "queued, position %s" % fr.get("pos", "?")}})
        f = host_call({"op": "call", "tool": name, "args": args}, on_progress=prog)
        result(id_, to_content(f))
    elif m and m.startswith("notifications/"):
        pass
    else:
        if id_ is not None:
            error(id_, -32601, "method not found: %s" % m)

def main():
    elog("up, transport=%s" % os.environ.get("PN_MCP_TRANSPORT", "vsock:2:9400"))
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            handle(msg)
        except Exception as e:
            elog("handle error: %r" % e)
            if msg.get("id") is not None:
                error(msg.get("id"), -32603, "internal error")

if __name__ == "__main__":
    main()
