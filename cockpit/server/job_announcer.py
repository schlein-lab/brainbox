#!/usr/bin/env python3

import json
import os
import ssl
import time
import urllib.request

ENV_PATH = os.path.expanduser("~/.config/job-announcer.env")
LOG_PATH = os.path.expanduser("~/.local/share/job-announcer.log")
STATE_PATH = os.path.expanduser("~/.local/share/job-announcer-seen.json")

DEFAULT_ANNOUNCE_STATES = "done,failed,timeout"
MAX_LEN = 240
SEEN_CAP = 5000

_SSL = ssl._create_unverified_context()

def _env():
    d = {}
    try:
        for ln in open(ENV_PATH):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip()
    except OSError:
        pass
    return d

def log(m):
    line = "%s %s\n" % (time.strftime("%H:%M:%S"), m)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass

class Cfg:
    def __init__(self):
        e = _env()
        self.url = (e.get("PORTAL_URL") or "https://127.0.0.1:8077").rstrip("/")
        self.key = e.get("PORTAL_KEY", "")
        self.display = e.get("DISPLAY") or "nabu-durchsage"
        self.prefix = e.get("ANNOUNCE_TAG_PREFIX", "announce")
        self.states = [s.strip() for s in (e.get("ANNOUNCE_STATES") or DEFAULT_ANNOUNCE_STATES).split(",") if s.strip()]
        try:
            self.poll_s = max(3, int(e.get("POLL_S") or 10))
        except ValueError:
            self.poll_s = 10
        try:
            self.limit = max(50, int(e.get("QUEUE_LIMIT") or 400))
        except ValueError:
            self.limit = 400
        try:
            self.max_per_min = max(1, int(e.get("MAX_PER_MIN") or 4))
        except ValueError:
            self.max_per_min = 4

def _req(cfg, path, method="GET", body=None):
    url = cfg.url + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if cfg.key:
        req.add_header("Authorization", "Bearer " + cfg.key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL) as r:
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}, None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:
        return None, "%r" % (e,)

def fetch_jobs(cfg):
    d, err = _req(cfg, "/api/queue?limit=%d" % cfg.limit)
    if err:
        return None, err
    if not isinstance(d, dict) or not d.get("ok"):
        return None, "queue: %s" % (d.get("error") if isinstance(d, dict) else "bad")
    return d.get("jobs") or [], None

def announce(cfg, text):
    body = {"display": cfg.display, "ref": {"kind": "text", "value": text[:MAX_LEN]}}
    d, err = _req(cfg, "/api/displays/show", "POST", body)
    if err:
        return False, err
    if isinstance(d, dict) and not d.get("ok"):
        return False, d.get("error", "show abgelehnt")
    return True, "ok"

def j_id(j):
    return j.get("id")

def j_state(j):
    return j.get("state")

def j_tag(j):
    return (j.get("client_tag") or j.get("tag") or "").strip()

def j_exit(j):
    return j.get("exit_code")

def opted_in(cfg, j):
    tag = j_tag(j).lower()
    pref = cfg.prefix.strip().lower()
    if not pref:
        return True
    return tag.startswith(pref)

def spoken_label(cfg, j):

    tag = j_tag(j)
    if tag:
        low = tag.lower()
        pref = cfg.prefix.strip().lower()
        if pref and low.startswith(pref):
            rest = tag[len(cfg.prefix):].lstrip("-:_ ")
            if rest:
                return rest[:48]
        return tag[:48]
    cmd = j.get("cmd")
    try:
        parts = json.loads(cmd) if isinstance(cmd, str) else (cmd or [])
        toks = [p for p in parts if isinstance(p, str) and "=" not in p and p != "/usr/bin/env"]
        base = os.path.basename(toks[0]) if toks else ""
        if base in ("bash", "sh", "python3", "python", "env", "claude") and len(toks) > 1:
            base = os.path.basename(toks[1])
        return (base or "die Aufgabe")[:48]
    except Exception:
        return "die Aufgabe"

def compose(cfg, j, next_label):
    st = j_state(j)
    label = spoken_label(cfg, j)
    if st == "done":
        if next_label:
            return "Job %s ist fertig. Willst du mit %s weitermachen?" % (label, next_label)
        return "Job %s ist fertig." % label
    if st == "timeout":
        return "Job %s hat das Zeitlimit überschritten." % label
    if st == "cancelled":
        return "Job %s wurde abgebrochen." % label

    code = j_exit(j)
    if isinstance(code, int) and code not in (0, None):
        return "Job %s ist fehlgeschlagen, Fehlercode %d." % (label, code)
    return "Job %s ist fehlgeschlagen." % label

def load_state():
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("announced", {})
            d.setdefault("bootstrapped", False)
            return d
    except Exception:
        pass
    return {"announced": {}, "bootstrapped": False}

def save_state(st):
    ann = st.get("announced", {})
    if len(ann) > SEEN_CAP:
        keep = sorted(ann.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)[:SEEN_CAP]
        st["announced"] = dict(keep)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(st).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, STATE_PATH)

def next_suggestion(cfg, jobs, exclude_id):

    cand = [j for j in jobs
            if opted_in(cfg, j) and j_state(j) in ("queued", "running") and j_id(j) != exclude_id]
    if not cand:
        return None
    cand.sort(key=lambda j: (j.get("submitted_at") or 0))
    return spoken_label(cfg, cand[0])

class RateLimiter:
    def __init__(self, per_min):
        self.per_min = per_min
        self.hits = []

    def allow(self):
        now = time.time()
        self.hits = [t for t in self.hits if t > now - 60.0]
        if len(self.hits) >= self.per_min:
            return False
        self.hits.append(now)
        return True

def cycle(cfg, st, rl):
    jobs, err = fetch_jobs(cfg)
    if err:
        log("queue nicht lesbar: %s" % err)
        return
    ann = st["announced"]

    if not st.get("bootstrapped"):
        n = 0
        for j in jobs:
            if opted_in(cfg, j) and j_state(j) in cfg.states:
                jid = j_id(j)
                if jid is not None:
                    ann[str(jid)] = {"state": j_state(j), "at": time.time()}
                    n += 1
        st["bootstrapped"] = True
        save_state(st)
        log("bootstrap: %d bereits fertige Opt-in-Jobs als gesehen markiert (keine Ansage)" % n)
        return

    fresh = [j for j in jobs
             if opted_in(cfg, j) and j_state(j) in cfg.states and str(j_id(j)) not in ann]

    fresh.sort(key=lambda j: (j.get("finished_at") or j.get("submitted_at") or 0))
    for j in fresh:
        if not rl.allow():
            log("rate-limit: %d Durchsage(n) auf nächsten Zyklus verschoben" % (len(fresh)))
            break
        nxt = next_suggestion(cfg, jobs, j_id(j))
        text = compose(cfg, j, nxt)
        ok, info = announce(cfg, text)
        if ok:
            ann[str(j_id(j))] = {"state": j_state(j), "at": time.time()}
            save_state(st)
            log("ANSAGE job=%s state=%s -> %r" % (j_id(j), j_state(j), text))
        else:

            log("ANSAGE-FEHLER job=%s: %s" % (j_id(j), info))

def main():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    cfg = Cfg()
    if not cfg.key:
        log("KEIN PORTAL_KEY in %s — Melder ohne Key nutzlos, schlafe." % ENV_PATH)
    log("job_announcer startet: url=%s display=%s prefix=%r states=%s poll=%ds max/min=%d"
        % (cfg.url, cfg.display, cfg.prefix, cfg.states, cfg.poll_s, cfg.max_per_min))
    st = load_state()
    rl = RateLimiter(cfg.max_per_min)
    while True:
        try:
            cfg = Cfg()
            rl.per_min = cfg.max_per_min
            cycle(cfg, st, rl)
        except Exception as e:
            log("Zyklus-Fehler: %r" % (e,))
        time.sleep(cfg.poll_s)

if __name__ == "__main__":
    main()
