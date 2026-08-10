#!/usr/bin/env python3

import os, re, sys, json, socket, ssl, time, threading, struct, collections, itertools

UPSTREAM_HOST = "api.anthropic.com"
UPSTREAM_PORT = 443
CREDS = os.path.expanduser("~/.claude/.credentials.json")
LOG = os.environ.get("PN_BROKER_LOG", "/tmp/pn-http-broker.log")
MAX_FRAME = 8 << 20
MAX_STREAMS = 256

STRIP_TOOLS = [p.strip() for p in os.environ.get("PN_STRIP_SERVER_TOOLS", "").split(",") if p.strip()]
POLICY_FILE = os.environ.get("PN_POLICY_FILE", "")
_strip_cache = {"mtime": 0.0, "val": None}
_TOOL_TO_SERVER = {"websearch": "web_search", "webfetch": "web_fetch"}

def _strip_tools():

    if not POLICY_FILE:
        return STRIP_TOOLS
    try:
        mt = os.path.getmtime(POLICY_FILE)
    except OSError:
        return STRIP_TOOLS
    if _strip_cache["val"] is None or mt != _strip_cache["mtime"]:
        val = list(STRIP_TOOLS)
        try:
            d = json.load(open(POLICY_FILE))
            dis = {str(x).lower() for x in (d.get("disallowed_tools") or [])}
            val = [srv for cli, srv in _TOOL_TO_SERVER.items() if cli in dis]
        except Exception:
            pass
        _strip_cache.update(mtime=mt, val=val)
    return _strip_cache["val"]

_POOL = None
try:
    sys.path.insert(0, os.path.expanduser("~/.local/bin"))
    import llmpool as _llmpool_mod
    _POOL = _llmpool_mod.LLMPool(
        os.environ.get("PN_LLMPOOL_CFG", os.path.expanduser("~/.config/brainbox-portal/llmpool.json")),
        os.environ.get("PN_LLMPOOL_STATE", os.path.expanduser("~/.local/share/brainbox-portal/llmpool_state.json")),
        os.path.expanduser("~"))
    if os.environ.get("PN_LLMPOOL_DISABLE"):
        _POOL = None
except Exception:
    _POOL = None

try:
    _llm_refresh = _llmpool_mod.ensure_fresh_credentials
except Exception:
    _llm_refresh = None

def _pool_tries():
    try:
        return max(1, _POOL.enabled_count()) if _POOL is not None else 1
    except Exception:
        return 1

_POOL_CFG_MTIME = [0]

def _pool_fresh():

    if _POOL is None:
        return
    try:
        mt = os.stat(_POOL.cfg_path).st_mtime_ns
    except (OSError, AttributeError):
        return
    if mt != _POOL_CFG_MTIME[0]:
        _POOL_CFG_MTIME[0] = mt
        try:
            _POOL.reload()
        except Exception as e:
            log("POOL_RELOAD_ERR %r" % e)

def _pool_pick(exclude):

    if _POOL is None:
        return None, os.path.expanduser("~")
    _pool_fresh()
    try:
        acct = _POOL.pick(exclude=tuple(exclude))
        if acct:
            return acct["id"], acct["home"]
    except Exception as e:
        log("POOL_PICK_ERR %r" % e)
    return None, os.path.expanduser("~")

def _pool_record(acct_id, ok, rate_limited, head=b""):

    if _POOL is None or acct_id is None:
        return
    try:
        _POOL.record(acct_id, ok=ok,
                     rate_events=_synth_rate_events(head) if rate_limited else None,
                     was_rate_limited=rate_limited)
    except Exception as e:
        log("POOL_RECORD_ERR %r" % e)

def _status_code(head):
    try:
        return int(head.split(b" ", 2)[1])
    except Exception:
        return 0

def _synth_rate_events(head):

    resets_at, rtype = 0.0, "five_hour"
    try:
        for l in head.split(b"\r\n"):
            kl = l.lower()
            if kl.startswith(b"retry-after:"):
                try:
                    resets_at = time.time() + float(l.split(b":", 1)[1].strip())
                except Exception:
                    pass
            elif b"ratelimit" in kl and b"reset" in kl:
                try:
                    v = float(l.split(b":", 1)[1].strip())
                    resets_at = v / 1000.0 if v > 1e12 else v
                except Exception:
                    pass
            if b"day" in kl or b"week" in kl:
                rtype = "seven_day"
    except Exception:
        pass
    return [{"rate_limit_info": {"status": "rejected", "rateLimitType": rtype, "resetsAt": resets_at}}]

_BUD = {"rpm": int(os.environ.get("PN_LLM_MAX_RPM", "0") or 0),
        "max_req": int(os.environ.get("PN_LLM_MAX_REQ", "0") or 0),
        "max_tokens": int(os.environ.get("PN_LLM_MAX_TOKENS", "0") or 0)}
_BUD_ON = any(_BUD.values())
_bud_lock = threading.Lock()
_bud_state = {"reqs": collections.deque(), "total_req": 0, "total_tokens": 0}

def _budget_check(body):

    if not _BUD_ON:
        return None
    now = time.time()
    with _bud_lock:
        st = _bud_state
        dq = st["reqs"]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if _BUD["rpm"] and len(dq) >= _BUD["rpm"]:
            return "rate limit %d/min" % _BUD["rpm"]
        if _BUD["max_req"] and st["total_req"] >= _BUD["max_req"]:
            return "request budget %d exhausted" % _BUD["max_req"]
        mt = 0
        try:
            mt = int(json.loads(body).get("max_tokens", 0))
        except Exception:
            mt = 0
        if _BUD["max_tokens"] and st["total_tokens"] + mt > _BUD["max_tokens"]:
            return "token budget %d exhausted" % _BUD["max_tokens"]
        dq.append(now); st["total_req"] += 1; st["total_tokens"] += mt
    return None

PN_PRINCIPAL = os.environ.get("PN_PRINCIPAL", "owner")
PN_SESSION_CELL = os.environ.get("PN_SESSION_CELL", "")
PN_SESSION_JOB_FILE = os.environ.get("PN_SESSION_JOB_FILE", "")
ADMIT_WEIGHT = int(os.environ.get("PN_ADMIT_WEIGHT", os.environ.get("PN_LEASE_WEIGHT", "1")) or 1)
ADMIT_POLL_S = float(os.environ.get("PN_ADMIT_POLL_S", "0.25") or 0.25)

ADMIT_MAX_WAIT_S = float(os.environ.get("PN_ADMIT_MAX_WAIT_S", "600") or 600)

EXEC_MAX_HOLD_S = float(os.environ.get("PN_EXEC_MAX_HOLD_S", "600") or 600)
EXEC_CONTINGENT_BURST = int(os.environ.get("PN_EXEC_CONTINGENT_BURST", "8") or 8)
ADMIT_RENEW_S = 45.0
_GOV_OFF = os.environ.get("PN_GOV_DISABLE", "") == "1"
_RT = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
_LLMD_SOCK = os.environ.get("PN_LLMD_SOCK", os.path.join(_RT, "pn-llmd.sock"))
_PND_SOCK = os.environ.get("PN_PND_SOCK", os.path.join(_RT, "pnd.sock"))
_CALL_SEQ = itertools.count(1)
_job_cache = {"id": None}

_PREGRANT = {"ticket": None, "at": 0.0}
_pregrant_lock = threading.Lock()
CTL_PATH = "/pn/ctl"

_HELD_POS = {}
_LAST_GRANT_TS = [0.0]
_held_lock = threading.Lock()

def _held_set(call_id, position, kind="llm", name=None):

    with _held_lock:
        _HELD_POS[call_id] = {"pos": int(position), "kind": kind, "name": name}

def _held_grant(call_id):
    with _held_lock:
        _HELD_POS.pop(call_id, None)
        _LAST_GRANT_TS[0] = time.time()

def _held_clear(call_id):
    with _held_lock:
        _HELD_POS.pop(call_id, None)

def _held_snapshot():

    with _held_lock:
        items = [v for v in _HELD_POS.values() if v and v.get("pos", 0) > 0]
        held = bool(items)
        head = min(items, key=lambda v: v["pos"]) if items else None
        return {"held": held, "position": (head["pos"] if head else 0),
                "count": len(items),
                "kind": (head["kind"] if head else None),
                "name": (head["name"] if head else None),
                "granted_recent": (not held) and (time.time() - _LAST_GRANT_TS[0] < 1.6)}

UPSTREAM_HOST = os.environ.get("PN_UPSTREAM_HOST", UPSTREAM_HOST)
UPSTREAM_PORT = int(os.environ.get("PN_UPSTREAM_PORT", str(UPSTREAM_PORT)) or UPSTREAM_PORT)
UPSTREAM_PLAIN = os.environ.get("PN_UPSTREAM_PLAIN", "") == "1"

def _unix_rpc(path, req, timeout=2.0):

    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(path)
        c.sendall((json.dumps(req, separators=(",", ":")) + "\n").encode())
        buf = b""
        while b"\n" not in buf and len(buf) < (1 << 20):
            d = c.recv(65536)
            if not d:
                break
            buf += d
        c.close()
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace") or "{}")
    except Exception:
        return None

def _session_job_id():

    if _job_cache["id"] is not None:
        return _job_cache["id"]
    if not PN_SESSION_JOB_FILE:
        return None
    try:
        jid = int(open(PN_SESSION_JOB_FILE).read().strip())
        _job_cache["id"] = jid
        return jid
    except (OSError, ValueError):
        return None

def _admit(call_id, klass="interactive"):

    return _unix_rpc(_LLMD_SOCK, {"verb": "admit", "id": call_id, "cell_principal": PN_PRINCIPAL,
                                  "cell": PN_SESSION_CELL or "tcp", "klass": klass,
                                  "weight": ADMIT_WEIGHT})

def _admit_poll(call_id):
    return _unix_rpc(_LLMD_SOCK, {"verb": "admit-poll", "id": call_id})

def _admit_release(call_id, status=None, rate_hits=None, served=None):

    req = {"verb": "admit-release", "id": call_id}
    if status is not None:
        req["status"] = int(status or 0)
        req["rate_hits"] = int(rate_hits or 0)
        req["served"] = bool(served)
    _unix_rpc(_LLMD_SOCK, req)

def _exec_admit(turn, klass, argv0):
    return _unix_rpc(_LLMD_SOCK, {"verb": "exec-admit", "id": turn, "cell_principal": PN_PRINCIPAL,
                                  "cell": PN_SESSION_CELL or "tcp", "klass": klass,
                                  "weight": 1, "argv0": argv0})

def _exec_admit_poll(turn):
    return _unix_rpc(_LLMD_SOCK, {"verb": "exec-admit-poll", "id": turn})

def _exec_admit_release(turn):
    _unix_rpc(_LLMD_SOCK, {"verb": "exec-admit-release", "id": turn})

def _exec_acquire_stream(client, turn, argv0, klass):

    try:
        client.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
    except OSError:
        return
    r = _exec_admit(turn, klass, argv0)
    t0 = time.time()
    deadline = t0 + EXEC_MAX_HOLD_S
    while True:

        if r is None or not r.get("ok", True):
            _held_clear(turn)
            try: client.sendall(b'{"granted":true,"position":0,"failopen":true}\n')
            except OSError: pass
            return
        if r.get("granted"):
            _held_grant(turn)
            try: client.sendall(b'{"granted":true,"position":0}\n')
            except OSError: pass
            return
        pos = int(r.get("position", -1))
        _held_set(turn, pos, kind="exec", name=argv0)
        try:
            client.sendall(('{"held":true,"position":%d}\n' % pos).encode())
        except OSError:
            _held_clear(turn); return
        if time.time() > deadline:
            _held_clear(turn)
            try: client.sendall(b'{"granted":true,"position":0,"failopen":true}\n')
            except OSError: pass
            return
        time.sleep(ADMIT_POLL_S)
        r = _exec_admit_poll(turn)

def _admit_wait(call_id, klass="interactive", on_position=None):

    t0 = time.time()
    r = _admit(call_id, klass)
    if r is None:
        return "down", 0, -1
    pos = int(r.get("position", -1)); waiting = int(r.get("waiting", 0))
    if callable(on_position):
        on_position(pos, waiting)
    if r.get("granted"):
        _held_grant(call_id)
        return "ok", int((time.time() - t0) * 1000), 0
    _held_set(call_id, pos)
    last = pos
    deadline = t0 + ADMIT_MAX_WAIT_S
    try:
        while time.time() < deadline:
            time.sleep(ADMIT_POLL_S)
            r = _admit_poll(call_id)
            if r is None:
                _held_clear(call_id)
                return "down", int((time.time() - t0) * 1000), last
            if r.get("granted"):
                _held_grant(call_id)
                return "ok", int((time.time() - t0) * 1000), 0
            pos = int(r.get("position", last))
            if pos != last:
                _held_set(call_id, pos)
                if callable(on_position):
                    on_position(pos, int(r.get("waiting", 0)))
            last = pos
        _held_clear(call_id)
        return "cap", int((time.time() - t0) * 1000), last
    except BaseException:
        _held_clear(call_id)
        raise

def _pregrant_set(ticket):
    with _pregrant_lock:
        _PREGRANT["ticket"] = ticket
        _PREGRANT["at"] = time.time()

def _pregrant_take():

    with _pregrant_lock:
        tk, at = _PREGRANT["ticket"], _PREGRANT["at"]
        if tk and (time.time() - at) <= ADMIT_MAX_WAIT_S:
            _PREGRANT["ticket"] = None
            _PREGRANT["at"] = 0.0
            return tk
        return None

def _usage_tokens(buf):

    try:
        tin = re.findall(rb'"input_tokens"\s*:\s*(\d+)', buf)
        tout = re.findall(rb'"output_tokens"\s*:\s*(\d+)', buf)
        return (int(tin[-1]) if tin else 0, int(tout[-1]) if tout else 0)
    except Exception:
        return (0, 0)

def _report_turn(status, tokens_in, tokens_out, wait_ms):

    jid = _session_job_id()
    if not jid:
        return
    _unix_rpc(_PND_SOCK, {"verb": "session-turn", "job_id": jid,
                          "tokens_in": int(tokens_in), "tokens_out": int(tokens_out),
                          "status": int(status or 0), "wait_ms": int(wait_ms)}, timeout=3.0)

def _refuse(client, http_status, err_type, msg):

    body = json.dumps({"type": "error", "error": {"type": err_type, "message": msg}}).encode()
    hdr = ("HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
           "Connection: close\r\n\r\n" % (http_status, len(body))).encode()
    try:
        client.sendall(hdr + body)
    except OSError:
        pass

def _reply_json(client, obj, http_status="200 OK"):
    body = json.dumps(obj, separators=(",", ":")).encode()
    hdr = ("HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
           "Connection: close\r\n\r\n" % (http_status, len(body))).encode()
    try:
        client.sendall(hdr + body)
    except OSError:
        pass

def _handle_ctl(client, body):

    try:
        req = json.loads(body or b"{}")
    except Exception:
        _reply_json(client, {"ok": False, "error": "bad ctl json"}, "400 Bad Request"); return
    op = str(req.get("op") or "")
    turn = str(req.get("turn_id") or "").strip()
    if op == "cellwait":

        _reply_json(client, {"ok": True, **_held_snapshot()}); return
    if op == "exec-acquire":

        argv0 = str(req.get("argv0") or "?")
        klass = str(req.get("klass") or "interactive")
        if not turn:
            _reply_json(client, {"ok": False, "error": "exec-acquire needs turn_id"}, "400 Bad Request"); return
        _exec_acquire_stream(client, turn, argv0, klass); return
    if op == "exec-release":
        if turn:
            _held_clear(turn)
            _exec_admit_release(turn)
        _reply_json(client, {"ok": True, "released": True}); return
    if op == "exec-contingent":

        snap = _unix_rpc(_LLMD_SOCK, {"verb": "exec-admit-snapshot"})
        free = int(snap.get("free", 0)) if snap and snap.get("ok", True) else 0

        permits = max(0, free) * EXEC_CONTINGENT_BURST
        _reply_json(client, {"ok": True, "permits": permits}); return
    if op == "exec-note":

        log("EXEC_NOTE cell=%s argv0=%s" % (PN_SESSION_CELL or "?", str(req.get("argv0") or "?")))
        _reply_json(client, {"ok": True, "noted": True}); return
    if op == "status":
        snap = _unix_rpc(_LLMD_SOCK, {"verb": "admit-snapshot"})
        _reply_json(client, snap or {"ok": False, "down": True}); return
    if not turn:
        _reply_json(client, {"ok": False, "error": "ctl needs turn_id"}, "400 Bad Request"); return
    if op in ("acquire", "poll"):
        r = _admit(turn) if op == "acquire" else _admit_poll(turn)
        if r is None:
            _reply_json(client, {"ok": False, "down": True,
                                 "error": "pn-llmd (Governance) nicht erreichbar"}); return
        if r.get("granted"):
            _pregrant_set(turn)
        _reply_json(client, {"ok": True, "granted": bool(r.get("granted")),
                             "position": int(r.get("position", -1)),
                             "waiting": int(r.get("waiting", 0)),
                             "slots": int(r.get("slots", 0)),
                             "in_use": int(r.get("in_use", 0))}); return
    if op == "done":
        with _pregrant_lock:
            unconsumed = (_PREGRANT["ticket"] == turn)
            if unconsumed:
                _PREGRANT["ticket"] = None; _PREGRANT["at"] = 0.0
        if unconsumed:
            _admit_release(turn)
        _reply_json(client, {"ok": True, "released": unconsumed}); return
    if op == "release":
        with _pregrant_lock:
            if _PREGRANT["ticket"] == turn:
                _PREGRANT["ticket"] = None; _PREGRANT["at"] = 0.0
        _admit_release(turn)
        _reply_json(client, {"ok": True, "released": True}); return
    _reply_json(client, {"ok": False, "error": "unknown ctl op"}, "400 Bad Request")

def log(m):
    line = "[%.3f] %s" % (time.time(), m)
    try:
        with open(LOG, "a") as _f:
            _f.write(line + "\n")
        try:
            os.chmod(LOG, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    print(line, flush=True)

def oauth_token(home=None):

    path = os.path.join(home, ".claude", ".credentials.json") if home else CREDS
    if _llm_refresh is not None:
        try:
            _hm = home or os.path.dirname(os.path.dirname(CREDS))
            _st, _tok = _llm_refresh(_hm, margin_s=300, timeout=8)
            if _st == "refreshed":
                log("TOKEN_REFRESH proactive home=%s ok" % os.path.basename(_hm.rstrip("/")))
            if _tok:
                return _tok
        except Exception as e:
            log("TOKEN_REFRESH_ERR %r" % e)
    d = json.load(open(path))
    return d["claudeAiOauth"]["accessToken"]

def read_headers(sock):
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = sock.recv(4096)
        if not d:
            break
        buf += d
        if len(buf) > (1 << 20):
            break
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest

def parse_req(head):
    lines = head.split(b"\r\n")
    method, path, _ = lines[0].split(b" ", 2)
    headers = []
    for l in lines[1:]:
        if b":" in l:
            k, v = l.split(b":", 1)
            headers.append((k.strip(), v.strip()))
    return method.decode(), path.decode(), headers

def rewrite(method, headers, token):
    drop = {b"authorization", b"x-api-key", b"host", b"connection",
            b"content-length", b"accept-encoding", b"transfer-encoding", b"expect"}
    out, clen, has_beta = [], 0, False
    for k, v in headers:
        kl = k.lower()
        if kl == b"content-length":
            clen = int(v)
        if kl in drop:
            continue
        if kl == b"anthropic-beta":
            has_beta = True
        out.append((k, v))
    out.append((b"Host", UPSTREAM_HOST.encode()))
    out.append((b"Authorization", b"Bearer " + token.encode()))
    out.append((b"Connection", b"close"))
    out.append((b"Accept-Encoding", b"identity"))
    if not has_beta:
        out.append((b"anthropic-beta", b"oauth-2025-04-20"))
    return clen, out

def strip_server_tools(body, strip):

    if not strip:
        return body
    obj = json.loads(body)
    tools = obj.get("tools")
    if isinstance(tools, list):
        kept = []
        for t in tools:
            ty = str(t.get("type", "")) if isinstance(t, dict) else ""
            nm = str(t.get("name", "")) if isinstance(t, dict) else ""
            if any(ty.startswith(p) or nm.startswith(p) for p in strip):
                continue
            kept.append(t)
        if len(kept) != len(tools):
            obj["tools"] = kept
            return json.dumps(obj, separators=(",", ":")).encode()
    return body

def handle(client):
    call_id, _acct_buf, last_status, served = None, b"", 0, False
    rate_hits = 0
    try:
        head, rest = read_headers(client)
        if not head:
            client.close(); return
        method, path, headers = parse_req(head)

        if method == "POST" and path.split("?", 1)[0] == CTL_PATH:
            _clen = 0
            for _k, _v in headers:
                if _k.lower() == b"content-length":
                    try:
                        _clen = int(_v)
                    except ValueError:
                        _clen = 0
            cbody = rest
            while len(cbody) < _clen:
                d = client.recv(min(65536, _clen - len(cbody)))
                if not d:
                    break
                cbody += d
            _handle_ctl(client, cbody)
            client.close(); return

        _pathonly = path.split("?", 1)[0]
        _bad = (".." in _pathonly) or any(ord(c) < 0x21 for c in path)
        for _hk, _hv in headers:
            if b"\r" in _hk or b"\n" in _hk or b"\r" in _hv or b"\n" in _hv:
                _bad = True
                break
        if _bad:
            log("BLOCKED %s %s (traversal/control char in request)" % (method, path))
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            client.close(); return

        if not (method == "POST" and (path == "/v1/messages" or path.startswith("/v1/messages?") or path.startswith("/v1/messages/"))):
            log("BLOCKED %s %s (path not in allowlist)" % (method, path))
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            client.close(); return

        clen = 0
        for _k, _v in headers:
            if _k.lower() == b"content-length":
                clen = int(_v)
        body = rest
        while len(body) < clen:
            d = client.recv(min(65536, clen - len(body)))
            if not d:
                break
            body += d

        _st = _strip_tools()
        if _st:
            try:
                body = strip_server_tools(body, _st)
            except Exception as e:
                log("STRIP_REJECT %r" % e)
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client.close(); return
        _bud = _budget_check(body)
        if _bud is not None:
            log("BUDGET_BLOCK %s" % _bud)
            _eb = ('{"type":"error","error":{"type":"rate_limit_error","message":"pn-budget: %s"}}' % _bud).encode()
            client.sendall(b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\nContent-Length: " + str(len(_eb)).encode() + b"\r\nConnection: close\r\n\r\n" + _eb)
            client.close(); return

        call_id = None
        wait_ms = 0
        if not _GOV_OFF:
            adopted = _pregrant_take()
            if adopted:
                call_id = adopted
                r0 = _admit_poll(call_id)
                if r0 is None:
                    log("ADMIT_DOWN call=%s (pn-llmd unreachable on adopt) -> honest 503" % call_id)
                    _refuse(client, "503 Service Unavailable", "api_error",
                            "pn-governance (pn-llmd) nicht erreichbar — Anfrage wird nicht "
                            "ungoverned ausgefuehrt. Bitte gleich erneut.")
                    client.close(); return
                log("ADMIT_ADOPT call=%s (shim pre-grant; turn admitted once)" % call_id)
            else:
                call_id = "sess:%s:%d:%d" % (PN_SESSION_CELL or "tcp", os.getpid(), next(_CALL_SEQ))

                def _log_pos(pos, waiting, _cid=call_id):
                    log("ADMIT_WAIT call=%s position=%d waiting=%d (HELD — upstream not yet contacted)"
                        % (_cid, pos, waiting))
                verdict, wait_ms, pos = _admit_wait(call_id, on_position=_log_pos)
                if verdict == "down":
                    log("ADMIT_DOWN call=%s (pn-llmd unreachable) -> honest 503, NO ungoverned bypass" % call_id)
                    _refuse(client, "503 Service Unavailable", "api_error",
                            "pn-governance (pn-llmd) nicht erreichbar — diese Anfrage wird nicht "
                            "ungoverned ausgefuehrt. Bitte gleich erneut versuchen.")
                    client.close(); return
                if verdict == "cap":
                    log("ADMIT_CAP call=%s wait_ms=%d position=%d -> honest 503 (liveness backstop)"
                        % (call_id, wait_ms, pos))
                    _report_turn(503, 0, 0, wait_ms)
                    _refuse(client, "503 Service Unavailable", "api_error",
                            "pn-governance: Warteschlange nach %ds noch nicht dran — bitte gleich "
                            "erneut." % int(ADMIT_MAX_WAIT_S))
                    client.close(); return
                log("ADMIT_GRANT call=%s wait_ms=%d weight=%d/interactive (now forwarding upstream)"
                    % (call_id, wait_ms, ADMIT_WEIGHT))

        tried, max_tries, served, last_status = set(), _pool_tries(), False, 0
        _acct_buf = b""
        _refreshed = set()
        _attempt = 0
        while _attempt < max_tries:
            _attempt += 1
            acct_id, home = _pool_pick(tried)
            picked, up, committed = acct_id, None, False
            try:
                token = oauth_token(home)
                _c2, out = rewrite(method, headers, token)
                line = ("%s %s HTTP/1.1\r\n" % (method, path)).encode()
                hdrs = b"\r\n".join(k + b": " + v for k, v in out)
                tail = (b"\r\nContent-Length: %d\r\n\r\n" % len(body)) if method in ("POST", "PUT", "PATCH") else b"\r\n\r\n"
                req = line + hdrs + tail + body
                log("REQ %s %s body=%dB -> %s://%s:%d (acct=%s bearer injected, try %d/%d) [after grant]"
                    % (method, path, len(body), "http" if UPSTREAM_PLAIN else "https",
                       UPSTREAM_HOST, UPSTREAM_PORT, acct_id, _attempt, max_tries))
                raw = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=120)
                if UPSTREAM_PLAIN:
                    up = raw
                else:
                    ctx = ssl.create_default_context()
                    up = ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
                up.sendall(req)

                head_buf = b""
                while b"\r\n\r\n" not in head_buf and len(head_buf) < (1 << 16):
                    d = up.recv(65536)
                    if not d:
                        break
                    head_buf += d
                status = _status_code(head_buf); last_status = status
                if status in (429, 503, 529):
                    rate_hits += 1
                if status in (429, 503, 529) and _attempt < max_tries:
                    _pool_record(picked, ok=False, rate_limited=True, head=head_buf); picked = None
                    tried.add(acct_id)
                    log("FAILOVER acct=%s status=%d -> retry sibling" % (acct_id, status))
                    continue

                if status == 401 and acct_id not in _refreshed:
                    _refreshed.add(acct_id)
                    _pool_record(picked, ok=False, rate_limited=False, head=head_buf); picked = None
                    _rst = "no-refresher"
                    if _llm_refresh is not None:
                        try:
                            _rst, _ = _llm_refresh(home, force=True, timeout=10)
                        except Exception as _re:
                            _rst = "failed"
                            log("TOKEN_REFRESH_ERR acct=%s %r" % (acct_id, _re))
                    if _rst == "refreshed":
                        max_tries += 1
                        log("TOKEN_REFRESH acct=%s refreshed -> retry" % acct_id)
                    else:
                        tried.add(acct_id)
                        log("TOKEN_REFRESH acct=%s %s -> failover sibling" % (acct_id, _rst))
                    continue

                _pool_record(picked, ok=(status < 400), rate_limited=(status == 429), head=head_buf); picked = None
                committed = True
                client.sendall(head_buf); total = len(head_buf)
                _acct_buf = head_buf[:1 << 20]
                _t_renew = time.time()
                while True:
                    d = up.recv(65536)
                    if not d:
                        break
                    client.sendall(d); total += len(d)
                    if len(_acct_buf) < (1 << 20):
                        _acct_buf += d
                    if call_id is not None and time.time() - _t_renew > ADMIT_RENEW_S:
                        _admit_poll(call_id)
                        _t_renew = time.time()
                log("RESP streamed %dB back to cell (acct=%s status=%s)" % (total, acct_id, status))
                served = True
                break
            except Exception as e:
                log("UPSTREAM_ERR acct=%s %r" % (acct_id, e))
                if picked is not None:
                    _pool_record(picked, ok=False, rate_limited=False, head=b""); picked = None
                if committed:
                    served = True; break
                tried.add(acct_id)
                continue
            finally:
                if up is not None:
                    try:
                        up.close()
                    except OSError:
                        pass
        if not served:
            log("POOL_EXHAUSTED last_status=%s" % last_status)
            try:
                if last_status == 401:

                    _refuse(client, "401 Unauthorized", "authentication_error",
                            "Claude-Konto-Anmeldung abgelaufen und nicht auffrischbar - bitte im "
                            "Portal unter Einstellungen -> Mein LLM-Konto neu verbinden.")
                else:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
    except Exception as e:
        log("HANDLE_ERR %r" % e)
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    finally:
        if call_id is not None:
            try:
                _held_clear(call_id)
            except Exception:
                pass
            try:
                _admit_release(call_id,
                               status=last_status, rate_hits=rate_hits, served=served)
            except Exception:
                pass
            try:
                if served:
                    tin, tout = _usage_tokens(_acct_buf)
                    log("TURN_ACCT call=%s status=%s tokens_in=%d tokens_out=%d wait_ms=%d"
                        % (call_id, last_status, tin, tout, wait_ms))
                    _report_turn(last_status, tin, tout, wait_ms)
            except Exception:
                pass
        try:
            client.close()
        except OSError:
            pass

def mux_serve(conn):

    HDRM = struct.Struct("!IBI"); DATA, CLOSE = 0, 1
    wlock = threading.Lock()
    streams = {}

    def send_frame(sid, typ, payload=b""):
        with wlock:
            conn.sendall(HDRM.pack(sid, typ, len(payload)) + payload)

    def out_forwarder(sid, a):
        while True:
            try:
                d = a.recv(65536)
            except OSError:
                break
            if not d:
                break
            send_frame(sid, DATA, d)
        send_frame(sid, CLOSE)
        try:
            a.close()
        except OSError:
            pass
        streams.pop(sid, None)

    buf = b""
    while True:
        while len(buf) < HDRM.size:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        sid, typ, ln = HDRM.unpack(buf[:HDRM.size]); buf = buf[HDRM.size:]
        if ln > MAX_FRAME:
            log("OVERSIZE frame ln=%d (sid=%d) — closing mux to protect the host" % (ln, sid))
            return
        while len(buf) < ln:
            d = conn.recv(65536)
            if not d:
                return
            buf += d
        payload = buf[:ln]; buf = buf[ln:]
        if typ == DATA:
            a = streams.get(sid)
            if a is None:
                if len(streams) >= MAX_STREAMS:
                    log("STREAM_CAP %d reached — dropping sid=%d" % (MAX_STREAMS, sid))
                    continue
                a, b = socket.socketpair()
                streams[sid] = a
                threading.Thread(target=handle, args=(b,), daemon=True).start()
                threading.Thread(target=out_forwarder, args=(sid, a), daemon=True).start()
            try:
                a.sendall(payload)
            except OSError:
                pass
        elif typ == CLOSE:
            a = streams.get(sid)
            if a:
                try:
                    a.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--tcp"
    if mode == "--tcp":
        hostport = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:8765"
        host, port = hostport.split(":")
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, int(port))); srv.listen(8)
        log("HTTP_BROKER_TCP %s (transparent -> %s, subscription auth injected)" % (hostport, UPSTREAM_HOST))
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    elif mode == "--unix":
        sock = sys.argv[2]
        if os.path.exists(sock):
            os.unlink(sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock); srv.listen(1); srv.settimeout(200)
        log("HTTP_BROKER_UNIX %s (transparent -> %s)" % (sock, UPSTREAM_HOST))
        conn, _ = srv.accept()
        handle(conn)
        log("HTTP_BROKER_UNIX_DONE")
    elif mode == "--unix-mux":
        sock = sys.argv[2]
        if os.path.exists(sock):
            os.unlink(sock)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock); srv.listen(1); srv.settimeout(None)
        log("HTTP_BROKER_UNIX_MUX %s (transparent -> %s, subscription auth injected, multiplexed)" % (sock, UPSTREAM_HOST))

        while True:
            try:
                conn, _ = srv.accept()
            except OSError as e:
                log("ACCEPT_ERR %r" % e)
                time.sleep(0.2)
                continue
            try:
                mux_serve(conn)
            except Exception as e:
                log("MUX_SERVE_ERR %r" % e)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                log("HTTP_BROKER_UNIX_MUX_CONN_DONE (still listening)")

if __name__ == "__main__":
    main()
