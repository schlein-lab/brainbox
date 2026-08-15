#!/usr/bin/env python3

import os, sys, json, time, threading, argparse, smtplib
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CFG_PATH = os.environ.get("PN_BOX_DEADMAN_CFG", os.path.expanduser("~/.pn_box_deadman.json"))
STATE = {"last_seen": 0.0, "pings": 0, "alerted": False, "started": time.time(), "last_alert": 0.0}
_LOCK = threading.RLock()

def load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def send_email(cfg, subject, body):

    user = cfg.get("smtp_user"); pw = cfg.get("smtp_pass")
    to = cfg.get("to_addr"); frm = cfg.get("from_addr") or user
    if not (user and pw and to):
        return (False, "no smtp creds")
    msg = EmailMessage()
    msg["From"] = frm; msg["To"] = to; msg["Subject"] = subject
    msg.set_content(body)
    host = cfg.get("smtp_host", "smtp.gmail.com"); port = int(cfg.get("smtp_port", 587))
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(); s.login(user, pw); s.send_message(msg)
        return (True, "smtp")
    except Exception as e:
        return (False, "smtp:" + str(e)[:150])

def send_webhook(cfg, subject, body):

    import urllib.request, json as _j
    url = cfg.get("webhook_url")
    if not url:
        return (False, "no webhook")
    field = cfg.get("webhook_field", "text")
    payload = dict(cfg.get("webhook_extra") or {})
    payload[field] = "%s\n\n%s" % (subject, body)
    try:
        req = urllib.request.Request(url, data=_j.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=15).read(256)
        return (True, "webhook")
    except Exception as e:
        return (False, "webhook:" + str(e)[:150])

def send_email(cfg, subject, body, _orig=send_email):
    return _orig(cfg, subject, body)

def send_alert(cfg, subject, body):

    infos = []; any_ok = False
    for fn in (send_webhook, send_email):
        ok, info = fn(cfg, subject, body)
        if info not in ("no webhook", "no smtp creds"):
            infos.append(info); any_ok = any_ok or ok
    if not infos:
        return (False, "kein Kanal konfiguriert")
    return (any_ok, "; ".join(infos))

def _alert(cfg, subject, body, sender=None):
    sender = sender or send_alert
    ok, info = sender(cfg, subject, body)
    print("[deadman] ALARM: %s -> %s (%s)" % (subject, "ok" if ok else "FEHLGESCHLAGEN", info), flush=True)
    return ok

def check_once(cfg, now, sender=None):

    grace = float(cfg.get("grace_s", 300))
    box = cfg.get("box_name", "Brainbox")
    with _LOCK:
        ls = STATE["last_seen"]; alerted = STATE["alerted"]; pings = STATE["pings"]
    if pings == 0:
        return "warten-auf-ersten-ping"
    age = now - ls
    if age > grace and not alerted:
        subj = "🔴 %s ANTWORTET NICHT (Dead-Man)" % box
        body = ("Der externe Dead-Man auf der Standby-Box hat seit %d s keinen Herzschlag der "
                "Hauptbox '%s' mehr empfangen (Grenze %d s).\n\nLetzter Ping: vor %d s\nPings gesamt: %d\n\n"
                "Wahrscheinlich ist Portal, Watchdog oder die ganze Box aus. Bitte pruefen." %
                (int(age), box, int(grace), int(age), pings))
        if _alert(cfg, subj, body, sender):
            pass
        with _LOCK:
            STATE["alerted"] = True; STATE["last_alert"] = now
        return "STALE-ALARM"
    if age <= grace and alerted:
        _alert(cfg, "🟢 %s wieder erreichbar" % box,
               "Der Herzschlag der Hauptbox '%s' ist zurueck (vor %d s)." % (box, int(age)), sender)
        with _LOCK:
            STATE["alerted"] = False
        return "RECOVERED"
    return "STALE" if alerted else "ok"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); parts = u.path.strip("/").split("/")
        cfg = self.server.cfg
        if len(parts) == 2 and parts[0] == "dm":
            if parts[1] != cfg.get("token", ""):
                return self._send(403, '{"error":"bad token"}')
            with _LOCK:
                STATE["last_seen"] = time.time(); STATE["pings"] += 1
            return self._send(200, '{"ok":true}')
        if u.path == "/status":
            with _LOCK:
                st = dict(STATE)
            st["age_s"] = (time.time() - st["last_seen"]) if st["last_seen"] else None
            st["grace_s"] = float(cfg.get("grace_s", 300)); st["box"] = cfg.get("box_name", "Brainbox")
            return self._send(200, json.dumps(st))
        if u.path == "/healthz":
            return self._send(200, '{"ok":true}')
        return self._send(404, '{"error":"not found"}')

def serve(cfg, host="0.0.0.0", port=8079):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.cfg = cfg

    def _checker():
        time.sleep(5)
        while True:
            try:
                check_once(cfg, time.time())
            except Exception as e:
                print("[deadman] checker-Fehler:", e, flush=True)
            time.sleep(float(cfg.get("check_s", 60)))
    threading.Thread(target=_checker, name="deadman-checker", daemon=True).start()
    print("[deadman] hoert auf %s:%d (grace=%ss, box=%s, smtp=%s)" %
          (host, port, cfg.get("grace_s", 300), cfg.get("box_name", "Brainbox"),
           bool(cfg.get("smtp_user") and cfg.get("smtp_pass"))), flush=True)
    httpd.serve_forever()

def _selftest():
    ok = True
    def ck(name, cond):
        nonlocal ok
        ok = ok and bool(cond); print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
    sent = []
    def fake_sender(cfg, subj, body):
        sent.append(subj); return (True, "fake")
    cfg = {"grace_s": 100, "box_name": "TestBox"}
    global STATE
    STATE = {"last_seen": 0.0, "pings": 0, "alerted": False, "started": 0, "last_alert": 0.0}
    r = check_once(cfg, 1000.0, fake_sender)
    ck("ohne Ping: warten (kein Fehlalarm)", r == "warten-auf-ersten-ping" and not sent)
    STATE["pings"] = 1; STATE["last_seen"] = 1000.0
    r = check_once(cfg, 1050.0, fake_sender)
    ck("frischer Ping: ok, kein Alarm", r == "ok" and not sent)
    r = check_once(cfg, 1000.0 + 150, fake_sender)
    ck("Stille > grace: EIN Alarm", r == "STALE-ALARM" and len(sent) == 1)
    r = check_once(cfg, 1000.0 + 200, fake_sender)
    ck("weiter still: kein zweiter Alarm (dedup)", len([s for s in sent if "ANTWORTET NICHT" in s]) == 1)
    STATE["last_seen"] = 1000.0 + 260
    r = check_once(cfg, 1000.0 + 265, fake_sender)
    ck("Rueckkehr: Entwarnung", r == "RECOVERED" and any("wieder erreichbar" in s for s in sent))
    r = check_once(cfg, 1000.0 + 500, fake_sender)
    ck("neue Stille: erneuter Alarm moeglich", any("ANTWORTET NICHT" in s for s in sent[-1:]) or r == "STALE-ALARM")
    print("\nSELFTEST:", "ALL GREEN" if ok else "FAILURES")
    return 0 if ok else 1

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8079)
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    serve(load_cfg(), a.host, a.port)
