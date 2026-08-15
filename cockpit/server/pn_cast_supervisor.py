#!/usr/bin/env python3

import os, sys, json, time, signal, subprocess, argparse, ssl, tempfile
import urllib.request, urllib.parse, urllib.error

DATA    = os.environ.get("PHANTOM_DATA", os.path.expanduser("~/.local/share/brainbox-portal"))
CFG     = os.path.expanduser("~/.config/brainbox-portal/config.json")

GOVERNED = bool(os.environ.get("PN_JOB_ID"))

def _portal_base():

    if os.environ.get("SEATCAST_PORTAL"):
        return os.environ["SEATCAST_PORTAL"].rstrip("/")
    try:
        port = int(json.load(open(CFG)).get("port") or 8077)
    except Exception:
        port = 8077
    return "https://127.0.0.1:%d" % port

PORTAL  = _portal_base()
VENV_PY = os.environ.get("SEATCAST_VENV") or os.path.expanduser("~/.local/share/celltv-venv/bin/python")
SEATCAST = os.path.expanduser("~/.local/bin/seatcast_service.py")
KEEPWARM_S = 20

def _seatcast_python():

    return VENV_PY if os.path.exists(VENV_PY) else sys.executable

_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE

class _NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, *a, **k):
        return None

_opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ctx))
_cookie = {"v": None}

def _pin():
    try:
        return json.load(open(CFG)).get("pin", "")
    except Exception:
        return ""

def _login():
    tf = os.environ.get("SEATCAST_TOKEN_FILE", "")
    if tf:
        try:
            t = open(tf).read().strip()
            if t:
                _cookie["v"] = t
                return t
        except Exception:
            pass
    try:
        data = urllib.parse.urlencode({"pin": _pin()}).encode()
        req = urllib.request.Request(PORTAL + "/api/login", data=data, method="POST")
        try:
            hdrs = _opener.open(req, timeout=10).headers
        except urllib.error.HTTPError as e:
            hdrs = e.headers
        sc = hdrs.get("Set-Cookie", "") or ""
        if "pp_session=" in sc:
            _cookie["v"] = sc.split("pp_session=", 1)[1].split(";", 1)[0]
    except Exception:
        pass
    return _cookie["v"]

def _keepwarm():

    if not _cookie["v"]:
        _login()
    for _ in range(2):
        try:
            req = urllib.request.Request(
                PORTAL + "/api/screen/start", data=b"{}", method="POST",
                headers={"Cookie": "pp_session=%s" % (_cookie["v"] or ""),
                         "Content-Type": "application/json"})
            with _opener.open(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                _login(); continue
            return {"ok": False, "error": "http %s" % e.code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "keepwarm failed"}

def _slug(s):
    return ("".join(c if c.isalnum() else "-" for c in (s or "")).strip("-").lower()[:48]) or "cast"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="cast device IP (Chromecast/Nest)")
    ap.add_argument("--name", default="Brainbox-Bildschirm")
    ap.add_argument("--http", type=int, default=8121)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--seat-uid", default="owner")
    ap.add_argument("--rfb-host", default="127.0.0.1")
    ap.add_argument("--rfb-port", type=int, default=5900)
    ap.add_argument("--term-sid", default=None, help="mirror a session TERMINAL instead of an RFB screen")
    ap.add_argument("--input-kind", default=None, help="follow-cast target kind: cell|seat|term")
    ap.add_argument("--input-id", default=None, help="follow-cast target id (cell id / term sid)")
    a = ap.parse_args()

    slug = _slug(a.name + "-" + a.device)
    if GOVERNED:
        statefile = os.path.join(tempfile.gettempdir(), "cast-state.json")
    else:
        statedir = os.path.join(DATA, "casts"); os.makedirs(statedir, exist_ok=True)
        statefile = os.path.join(statedir, slug + ".json")
    if a.input_kind:
        input_target = {"kind": a.input_kind, "id": a.input_id}
    elif a.term_sid:
        input_target = {"kind": "term", "id": a.term_sid}
    else:
        input_target = {"kind": "seat", "id": None}
    state = {"device": a.device, "name": a.name, "http": a.http, "fps": a.fps, "slug": slug,
             "pid": os.getpid(), "started": time.time(), "state": "starting", "child": None,
             "seat_uid": a.seat_uid, "term_sid": a.term_sid, "input_target": input_target}

    def save():
        try:
            tmp = statefile + ".tmp"
            json.dump(state, open(tmp, "w")); os.replace(tmp, statefile)
        except Exception:
            pass
    save()

    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))

    if a.term_sid:
        state["seat"] = True; save()
    else:
        w = _keepwarm(); state["seat"] = bool(w.get("ok")); save()

    child = {"p": None}

    def start_child():
        try:
            os.remove(os.path.join(tempfile.gettempdir(), "seatcast-cast-status-%d.json" % a.http))
        except Exception:
            pass
        env = dict(os.environ)
        env.update({"SEATCAST_DEVICE": a.device, "SEATCAST_NAME": a.name, "SEATCAST_HTTP": str(a.http),
                    "SEATCAST_FPS": str(a.fps), "SEATCAST_RFB_HOST": a.rfb_host,
                    "SEATCAST_RFB_PORT": str(a.rfb_port),
                    "SEATCAST_PORTAL": PORTAL})
        if a.term_sid:
            env["SEATCAST_TERM_SID"] = a.term_sid
            env["SEATCAST_TERM_UID"] = a.seat_uid

        p = subprocess.Popen(["nice", "-n", "12", _seatcast_python(), SEATCAST], env=env,
                             stdout=open(os.path.join(tempfile.gettempdir(),
                                                      "seatcast-%s.log" % slug), "ab"),
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        child["p"] = p; state["child"] = p.pid; state["state"] = "casting"; save()

    start_child()
    last_warm = time.time(); backoff = 2
    while not stop["v"]:
        time.sleep(1)
        try:
            _cs = json.load(open(os.path.join(tempfile.gettempdir(),
                                              "seatcast-cast-status-%d.json" % a.http)))
            if (_cs.get("cast"), _cs.get("cast_err")) != (state.get("cast"), state.get("cast_err")):
                state["cast"] = _cs.get("cast"); state["cast_err"] = _cs.get("cast_err"); save()
        except Exception:
            pass
        if not a.term_sid and time.time() - last_warm >= KEEPWARM_S:
            r = _keepwarm(); state["seat"] = bool(r.get("ok")); last_warm = time.time(); save()
        p = child["p"]
        if p and p.poll() is not None:
            state["state"] = "restarting"; save()
            for _ in range(int(backoff)):
                if stop["v"]:
                    break
                time.sleep(1)
            backoff = min(backoff * 2, 30)
            if not stop["v"]:
                if not a.term_sid:
                    _keepwarm()
                start_child()
        elif p and p.poll() is None:
            backoff = 2

    state["state"] = "stopping"; save()
    p = child["p"]
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        try:
            p.wait(timeout=6)
        except Exception:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
    try:
        os.remove(statefile)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main() or 0)
