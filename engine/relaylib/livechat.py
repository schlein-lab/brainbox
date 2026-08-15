
import time

from relaylib.livevoice import (
    _canon, _hkdf, lane_keys, seal, open_blob, Lane, mint_ticket, verify_ticket,
)

__all__ = [

    "_canon", "_hkdf", "lane_keys", "seal", "open_blob", "Lane",
    "mint_ticket", "verify_ticket",

    "serve_chat",

    "SAY", "SUB", "UNSUB", "LIST", "DECISIONS", "ANSWER", "CUR", "PING", "BYE",

    "TX", "SESSIONS", "ACK", "PONG",
]

SAY = "say"
SUB = "sub"
UNSUB = "unsub"
LIST = "list"
DECISIONS = "decisions"
ANSWER = "answer"
CUR = "cur"
PING = "ping"
BYE = "bye"

TX = "tx"
SESSIONS = "sessions"

ACK = "ack"
PONG = "pong"

def _as_int(v, default=-1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def _normalize_result(res):

    if isinstance(res, tuple):
        payload = res[0] if len(res) >= 1 and isinstance(res[0], dict) else {}
        code = _as_int(res[1], 500) if len(res) >= 2 else 200
        ok = 200 <= code < 300
        err = payload.get("error") if not ok else None
        if not ok and not err:
            err = "http %d" % code
        return ok, bool(payload.get("closed")), err
    if isinstance(res, dict):
        ok = bool(res.get("ok", not res.get("error")))
        return ok, bool(res.get("closed")), (None if ok else (res.get("error") or "rejected"))
    return False, False, "bad adapter result"

def serve_chat(lane, *, say_fn, transcript_fn, sessions_fn, decisions_fn, decide_fn,
               should_stop=None, idle_timeout=120.0):

    subscribed = set()
    cursor = {}
    stats = {"say": 0, "answer": 0, "sub": 0, "unsub": 0, "list": 0,
             "decisions": 0, "cur": 0, "tx": 0, "ping": 0}
    last = time.time()

    def _ack(fr, ok, closed=False, error=None):
        ref = fr.get("ref", fr.get("sid", fr.get("aid")))
        a = {"t": ACK, "ref": ref, "ok": bool(ok)}
        if closed:
            a["closed"] = True
        if error:
            a["error"] = str(error)
        return a

    def _push(sid):
        try:
            turns = transcript_fn(sid) or []
        except Exception:
            return
        c = cursor.get(sid, -1)
        new = [t for t in turns if _as_int(t.get("i")) > c]
        if not new:
            return
        top = max(_as_int(t.get("i")) for t in new)
        lane.send({"t": TX, "sid": sid, "turns": new, "cursor": top})
        cursor[sid] = top
        stats["tx"] += 1

    while True:
        if should_stop and should_stop():
            break

        fr = lane.recv(timeout=0.5)
        if fr is not None:
            last = time.time()
            t = fr.get("t")
            if t == BYE:
                break
            elif t == SAY:
                stats["say"] += 1
                try:
                    ok, closed, err = _normalize_result(say_fn(fr.get("sid"), fr.get("text", "")))
                except Exception as e:
                    ok, closed, err = False, False, "say failed: %s" % e
                lane.send(_ack(fr, ok, closed, err))
            elif t == ANSWER:
                stats["answer"] += 1
                try:
                    ok, closed, err = _normalize_result(
                        decide_fn(fr.get("aid"), fr.get("decision"), fr.get("answer")))
                except Exception as e:
                    ok, closed, err = False, False, "answer failed: %s" % e
                lane.send(_ack(fr, ok, closed, err))
            elif t == SUB:
                stats["sub"] += 1
                sid = fr.get("sid")
                if sid is not None:
                    subscribed.add(sid)
                    cursor[sid] = _as_int(fr.get("since"), -1)
            elif t == UNSUB:
                stats["unsub"] += 1
                sid = fr.get("sid")
                subscribed.discard(sid)
                cursor.pop(sid, None)
            elif t == LIST:
                stats["list"] += 1
                try:
                    lst = sessions_fn() or []
                except Exception:
                    lst = []
                lane.send({"t": SESSIONS, "list": lst})
            elif t == DECISIONS:
                stats["decisions"] += 1
                try:
                    lst = decisions_fn() or []
                except Exception:
                    lst = []
                lane.send({"t": DECISIONS, "list": lst})
            elif t == CUR:
                stats["cur"] += 1
                sid = fr.get("sid")
                if sid is not None:
                    cursor[sid] = max(cursor.get(sid, -1), _as_int(fr.get("i")))
            elif t == PING:
                stats["ping"] += 1
                lane.send({"t": PONG, "ts": fr.get("ts")})

        for sid in list(subscribed):
            _push(sid)

        if fr is None and (time.time() - last) > idle_timeout:
            break

    return stats
