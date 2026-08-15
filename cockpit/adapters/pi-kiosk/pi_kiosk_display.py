#!/usr/bin/env python3

import argparse
import html
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_LOCK = threading.Lock()
CFG = {"photos": "", "state": "", "token": ""}
_SEQ = {"n": 0}

def _now():
    return time.time()

def load_state():
    try:
        with open(CFG["state"]) as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"mode": "idle", "content": None, "seq": 0, "updated": _now()}

def save_state(d):
    d["seq"] = _SEQ["n"] = _SEQ["n"] + 1
    d["updated"] = _now()
    tmp = "%s.tmp.%d" % (CFG["state"], os.getpid())
    os.makedirs(os.path.dirname(CFG["state"]) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, CFG["state"])
    return d

def list_photos():
    d = CFG["photos"]
    if not d or not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if n.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp"):
            out.append(n)
    return out

PAGE = """<!doctype html><html lang=de><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Kiosk</title><style>
 html,body{margin:0;height:100%;background:#0b0d10;color:#eef2f6;overflow:hidden;
   font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
 #stage{position:fixed;inset:0;display:flex;align-items:center;justify-content:center}
 img.full{max-width:100%;max-height:100%;object-fit:contain}
 iframe{border:0;width:100%;height:100%}
 #text{padding:6vh 8vw;font-size:5.5vh;line-height:1.35;text-align:center;text-wrap:balance;max-width:90vw}
 #photo{width:100%;height:100%;object-fit:cover;transition:opacity 1.2s}
 .badge{position:fixed;left:12px;bottom:10px;font-size:12px;color:#7d8794;opacity:.6}
</style></head><body>
<div id=stage></div><div class=badge id=badge>kiosk</div>
<script>
let seq=-1, photos=[], pi=0, photoTimer=null;
const stage=document.getElementById('stage'), badge=document.getElementById('badge');
async function loadPhotos(){ try{ const r=await fetch('/photos'); photos=(await r.json()).photos||[]; }catch(e){ photos=[]; } }
function idleLoop(){
  if(photoTimer) return;
  const show=()=>{ if(!photos.length){ stage.innerHTML='<div id=text>Bereit.</div>'; return; }
    const n=photos[pi%photos.length]; pi++;
    stage.innerHTML='<img id=photo src="/photos/'+encodeURIComponent(n)+'">'; };
  show(); photoTimer=setInterval(show, 8000);
}
function stopIdle(){ if(photoTimer){ clearInterval(photoTimer); photoTimer=null; } }
function render(st){
  const m=st.mode, c=st.content||{};
  if(m==='idle'){ idleLoop(); badge.textContent='idle'; return; }
  stopIdle(); badge.textContent='zeigt an';
  if(c.kind==='url'){ stage.innerHTML='<iframe src="'+(c.value||'about:blank').replace(/"/g,'&quot;')+'"></iframe>'; }
  else if(c.kind==='image'){ stage.innerHTML='<img class=full src="'+(c.value||'').replace(/"/g,'&quot;')+'">'; }
  else { const t=(c.text!=null?c.text:(c.value!=null?c.value:'')); const d=document.createElement('div'); d.id='text'; d.textContent=t; stage.innerHTML=''; stage.appendChild(d); }
}
async function poll(){
  try{ const r=await fetch('/current',{cache:'no-store'}); const st=await r.json();
    if(st.seq!==seq){ seq=st.seq; render(st); } }catch(e){}
  setTimeout(poll, 1500);
}
loadPhotos().then(()=>{ idleLoop(); poll(); });
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _token_ok(self):
        if not CFG["token"]:
            return True
        return self.headers.get("X-Kiosk-Token", "") == CFG["token"]

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/" or p == "/index.html":
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if p == "/health":
            with STATE_LOCK:
                st = load_state()
            return self._json({"ok": True, "service": "pi-kiosk-display", "node": socket.gethostname(),
                               "state": st.get("mode"), "seq": st.get("seq", 0),
                               "photos": len(list_photos())})
        if p == "/current":
            with STATE_LOCK:
                return self._json(load_state())
        if p == "/photos":
            return self._json({"photos": list_photos()})
        if p.startswith("/photos/"):
            name = os.path.basename(p[len("/photos/"):])
            full = os.path.join(CFG["photos"], name) if CFG["photos"] else ""
            if full and os.path.isfile(full):
                try:
                    with open(full, "rb") as f:
                        data = f.read()
                except OSError:
                    return self._json({"error": "read"}, 500)
                ext = name.lower().rsplit(".", 1)[-1]
                mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", mt)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        body = self._body()
        if p == "/show":
            if not self._token_ok():
                return self._json({"error": "forbidden"}, 403)
            try:
                req = json.loads(body or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            kind = req.get("kind") or ("url" if str(req.get("value", "")).startswith(("http://", "https://")) else "text")
            if kind not in ("url", "image", "text"):
                return self._json({"error": "kind must be url|image|text"}, 400)
            content = {"kind": kind, "value": req.get("value"), "text": req.get("text")}
            with STATE_LOCK:
                st = save_state({"mode": "show", "content": content})
            return self._json({"ok": True, "mode": "show", "seq": st["seq"]})
        if p == "/idle":
            if not self._token_ok():
                return self._json({"error": "forbidden"}, 403)
            with STATE_LOCK:
                st = save_state({"mode": "idle", "content": None})
            return self._json({"ok": True, "mode": "idle", "seq": st["seq"]})
        return self._json({"error": "not found"}, 404)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PI_KIOSK_PORT", "8095")))
    ap.add_argument("--photos", default=os.environ.get("PI_KIOSK_PHOTOS",
                    os.path.expanduser("~/.local/share/pi-kiosk/photos")))
    ap.add_argument("--state", default=os.environ.get("PI_KIOSK_STATE",
                    os.path.expanduser("~/.local/share/pi-kiosk/state.json")))
    ap.add_argument("--token", default=os.environ.get("PI_KIOSK_TOKEN", ""))
    a = ap.parse_args()
    CFG["photos"] = a.photos
    CFG["state"] = a.state
    CFG["token"] = a.token
    os.makedirs(os.path.dirname(CFG["state"]) or ".", exist_ok=True)
    with STATE_LOCK:
        st = load_state()
        _SEQ["n"] = st.get("seq", 0)
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    print("pi_kiosk_display on :%d  photos=%s state=%s token=%s" % (
        a.port, CFG["photos"], CFG["state"], "on" if CFG["token"] else "off"), flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
