#!/usr/bin/env python3

from __future__ import annotations
import os, sys, json, time, socket, threading, argparse, secrets

class Ledger:
    def __init__(self, principal="admin"):
        self.principal = principal
        self.lock = threading.Lock()
        self.events = []
        self.jobs = {}
        self._eid = 0
        self._jid = 0

        self.cond = threading.Condition(self.lock)

    def _add_event(self, job_id, kind, data: dict):

        self._eid += 1
        ev = {"id": self._eid, "job_id": job_id, "ts": time.time(),
              "kind": kind, "topic": f"user/{self.principal}",
              "data": json.dumps(data, separators=(",", ":"))}
        self.events.append(ev)
        self.cond.notify_all()
        return ev

    def max_event_id(self):
        return self._eid

    def events_since(self, topics, after_id=0, limit=500):
        ts = set(topics or [])
        return [e for e in self.events if e["topic"] in ts and e["id"] > after_id][:limit]

    def submit(self, cmd, tag, task_type, needs_confirmation):
        self._jid += 1
        jid = self._jid
        job = {"id": jid, "state": "staged" if needs_confirmation else "queued",
               "task_type": task_type or "(raw)", "submitter_principal": self.principal,
               "tag": tag, "needs_confirmation": bool(needs_confirmation),
               "approval_state": "pending" if needs_confirmation else None,
               "approval_nonce": None, "cmd": cmd,
               "progress": {"done": None, "total": None, "msg": None},
               "partial": None, "exit_code": None,
               "submitted_at": time.time()}
        self.jobs[jid] = job
        if needs_confirmation:
            nonce = secrets.token_urlsafe(18)
            job["approval_nonce"] = nonce

            ar = {"job_id": jid, "nonce": nonce, "task_type": task_type,
                  "summary": tag or task_type, "submitter_principal": self.principal}

            if task_type in ("device.bind", "firmware.flash", "net.egress") or \
               (tag and any(w in tag.lower() for w in ("bind", "flash", "firmware", "egress"))):
                ar["action"] = ar.get("action") or f"{task_type or 'side-effect'}: {tag}"
                if any(w in (tag or "").lower() for w in ("flash", "firmware")):
                    ar["brick_warning"] = "irreversible; a failed flash can brick the device"
            job["approval_request"] = ar
            self._add_event(jid, "approval-request", ar)
            return {"ok": True, "id": jid, "state": "staged", "nonce": nonce,
                    "needs_confirmation": True}
        self._add_event(jid, "state", {"state": "queued"})
        return {"ok": True, "id": jid, "pos": jid}

    def resolve(self, nonce, decision):

        if not nonce:
            return {"ok": False, "error": "missing nonce"}
        job = next((j for j in self.jobs.values() if j.get("approval_nonce") == nonce), None)
        if not job:
            return {"ok": False, "error": "unknown or expired nonce"}
        want = "approved" if decision == "approve" else "denied"
        cur = job.get("approval_state")
        if cur == want:
            return {"ok": True, "id": job["id"], "state": job["state"],
                    "decision": want, "idempotent": True}
        if cur in ("approved", "denied") and cur != want:
            return {"ok": False, "error": f"already {cur}; cannot {decision}"}
        job["approval_state"] = want
        job["state"] = "queued" if decision == "approve" else "cancelled"

        self._add_event(job["id"], "state",
                        {"state": job["state"], "decision": want})

        if decision == "approve":
            self._add_event(job["id"], "state", {"state": "running"})
        return {"ok": True, "id": job["id"], "state": job["state"], "decision": want}

    def steer(self, jid, inp):
        job = self.jobs.get(jid)
        if not job:
            return {"ok": False, "error": "no such job"}
        self._add_event(jid, "steer", {"seq": 1, "input": inp})
        return {"ok": True, "seq": 1}

    def cvm(self, jid):
        j = self.jobs.get(jid)
        if not j:
            return None
        return {"schema": "pn-cvm/1", "id": j["id"], "state": j["state"],
                "task_type": j.get("task_type") or "(raw)",
                "principal": j.get("submitter_principal"),
                "topic": f"job/{jid}", "progress": j.get("progress"),
                "exit_code": j.get("exit_code"),
                "needs_confirmation": bool(j.get("needs_confirmation")),
                "approval_state": j.get("approval_state"),
                "approval_request": j.get("approval_request"),
                "partial": j.get("partial"),
                "last_event_id": self.max_event_id()}

SUB_PING_S = 20.0
SUB_POLL_S = 0.05

def _send_line(conn, obj):
    conn.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

def handle_conn(conn, led: Ledger):
    try:
        buf = b""
        while b"\n" not in buf:
            ch = conn.recv(65536)
            if not ch:
                return
            buf += ch
        line = buf.split(b"\n", 1)[0]
        try:
            req = json.loads(line.decode())
        except ValueError:
            _send_line(conn, {"ok": False, "error": "bad json"}); return
        verb = req.get("verb")

        if verb == "subscribe":
            topics = req.get("topics") or [f"user/{led.principal}"]

            topics = [t for t in topics if t == f"user/{led.principal}" or t == "user/me"]
            topics = [f"user/{led.principal}" if t == "user/me" else t for t in topics] \
                or [f"user/{led.principal}"]
            after = req.get("after_id")
            with led.lock:
                cursor = led.max_event_id() if after is None else int(after)
            _send_line(conn, {"ok": True, "type": "subscribed", "topics": topics,
                              "principal": led.principal, "cursor": cursor})
            last_ping = time.time()
            while True:
                with led.cond:
                    evs = led.events_since(topics, after_id=cursor, limit=500)
                    if not evs:
                        led.cond.wait(timeout=SUB_POLL_S)
                        evs = led.events_since(topics, after_id=cursor, limit=500)
                for e in evs:
                    _send_line(conn, {"type": "event", "event": e})
                    cursor = max(cursor, e["id"])
                if evs:
                    last_ping = time.time()
                elif time.time() - last_ping >= SUB_PING_S:
                    _send_line(conn, {"type": "ping", "cursor": cursor})
                    last_ping = time.time()
            return

        with led.lock:
            if verb == "ping":
                resp = {"ok": True, "version": "mock-pnd/1", "pid": os.getpid(),
                        "uptime": 0}
            elif verb == "submit":
                tt = req.get("task_type")
                tag = req.get("tag") or (tt + (": " + json.dumps(req.get("params"))
                      if req.get("params") else "") if tt else None)
                resp = led.submit(req.get("cmd"), tag, tt,
                                  bool(req.get("needs_confirmation")))
            elif verb in ("approve", "deny"):
                resp = led.resolve(req.get("nonce"),
                                   "approve" if verb == "approve" else "deny")
            elif verb == "steer":
                resp = led.steer(req.get("id"), req.get("input"))
            elif verb == "cvm":
                c = led.cvm(req.get("id"))
                resp = {"ok": True, "cvm": c} if c else {"ok": False, "error": "no such job"}
            elif verb == "replay":
                topics = req.get("topics") or []
                if isinstance(topics, str):
                    topics = topics.split(",")
                topics = [f"user/{led.principal}" if t == "user/me" else t for t in topics]
                evs = led.events_since(topics, after_id=req.get("after_id", 0),
                                       limit=req.get("limit", 500))
                resp = {"ok": True, "events": evs, "topics": topics,
                        "cursor": led.max_event_id()}
            elif verb == "status":
                pend = sum(1 for j in led.jobs.values()
                           if j.get("approval_state") == "pending")
                resp = {"ok": True, "version": "mock-pnd/1",
                        "counts": {"jobs": len(led.jobs), "pending_approvals": pend},
                        "snap": {"mock": True}, "now": time.time(), "uptime": 0}
            elif verb == "list":
                resp = {"ok": True, "jobs": list(led.jobs.values()),
                        "counts": {"jobs": len(led.jobs)}, "now": time.time()}
            elif verb == "job":
                j = led.jobs.get(req.get("id"))
                resp = {"ok": True, "job": j} if j else {"ok": False, "error": "no such job"}
            else:
                resp = {"ok": False, "error": f"unknown verb {verb}"}
        _send_line(conn, resp)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass

def seed(led: Ledger):

    led.submit(["/bin/echo", "flash"], "flash firmware v2.3 to hp-4500 over TFTP",
               "firmware.flash", True)
    led.submit(["/bin/echo", "render"], "Render a 12s sunset clip", "media.render_video", True)

def main():
    ap = argparse.ArgumentParser(description="mock_pnd — a fake Brainarbeit engine for the cockpit")
    ap.add_argument("--sock", default=os.environ.get("PN_SOCK")
                    or os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                                    "pnd-mock.sock"))
    ap.add_argument("--principal", default="admin",
                    help="the single tenant this mock serves (the SO_PEERCRED stand-in)")
    ap.add_argument("--seed", action="store_true",
                    help="pre-stage two pending approvals for an immediately-populated inbox")
    args = ap.parse_args()

    led = Ledger(principal=args.principal)
    if args.seed:
        seed(led)

    try:
        os.unlink(args.sock)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(args.sock)
    os.chmod(args.sock, 0o600)
    srv.listen(64)
    print(f"mock_pnd on {args.sock}  principal={args.principal}"
          f"{'  (seeded)' if args.seed else ''}", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handle_conn, args=(conn, led), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        try:
            os.unlink(args.sock)
        except OSError:
            pass

if __name__ == "__main__":
    main()
