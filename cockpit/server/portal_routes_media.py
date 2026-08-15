
import os, sys, json, socket, signal, subprocess, time
import re, mimetypes, html
import urllib.parse, urllib.request, http.cookies, http.cookiejar
import shutil as _shutil

try:
    import pn_governed as _PN
except Exception:
    _PN = None

ATTACH_DIR = None
DATA_DIR = None
JOBS_DIR = None
STATIC_DIR = None
VNC3_HTML = None
WEBAPP_DIR = None
_DEVICE_REG = None
_DISPLAY_REG = None
_agent_norm_url = None
_attach_owner = None
_browse = None
_browser_jar = None
_browser_save = None
_cast_slug = None
_decide_placement = None
_fabric = None
_kiosk_post = None
_pid_alive = None
_placement_for = None
_prov_log = None
_rfb_tcp_bridge = None
_session_store = None
_screen_open = None
_vext = None
_vext_ctx = None
_vmcell_sock = None
cell = None
job_get = None
lan_ip = None
links_add = None

def configure(**kw):
    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v

def _cast_state_merged(st):

    live = st.get("live")
    if not live:
        return st
    out = dict(st)
    try:
        lv = json.load(open(live))
        for k in ("state", "child", "seat", "pid"):
            if k in lv:
                out[k] = lv[k]
    except Exception:
        out.setdefault("state", "starting")
    return out

def _cast_alive(st):

    try:
        st = _cast_state_merged(st)
        if st.get("job"):
            j = (_PN.job(int(st["job"])) if _PN is not None else None) or {}
            return bool(j.get("state") == "running"
                        and st.get("state") == "casting"
                        and st.get("child") and _pid_alive(int(st["child"])))
        return bool(st.get("pid") and _pid_alive(int(st["pid"]))
                    and st.get("state") == "casting"
                    and st.get("child") and _pid_alive(int(st["child"])))
    except Exception:
        return False

def _sup_gone(pid):

    try:
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        pass
    try:
        if not _pid_alive(pid):
            return True
    except Exception:
        return True
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[-1].split()[0] == "Z"
    except Exception:
        return True

SCENE_FIRST_FRAME_WAIT = 3.0

SCENE_STREAM_EMPTY_POLLS = 100
SCENE_SEAT_DOWN_DE = "Der Bildschirm läuft nicht — bitte zuerst den Bildschirm starten."
SCENE_NO_WINDOW_DE = ("Der Bildschirm läuft, zeigt aber nichts an — es ist kein Fenster offen. "
                      "Starte ein Programm, damit etwas zu sehen ist.")

class MediaRoutes:
    def _fabric_launch(self, body):

        if _fabric is None:
            return self.send_html(json.dumps({"error": "fabric unavailable"}), 500,
                                  [("Content-Type", "application/json")])
        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        app = str(req.get("app", "browser"))
        caps = _fabric.from_request(self.headers, req.get("caps"))
        host = (self.headers.get("Host", "") or "").split(":")[0]
        out = _fabric.launch(app, self._principal(), caps, {"url": req.get("url"), "host": host})
        out["client_caps"] = caps.as_dict()

        try:
            rp = _placement_for(req.get("caps") or {}, req.get("policy", "auto"), label=app)
            if rp:
                out["render_placement"] = rp
        except Exception:
            pass
        return self.send_html(json.dumps(out), 200, [("Content-Type", "application/json")])

    def _fabric_launcher(self, query):

        if _fabric is None:
            return self.send_html("fabric unavailable", 500)
        qs = urllib.parse.parse_qs(query)
        app_id = (qs.get("app", ["libreoffice"])[0]) or "libreoffice"
        app = _fabric.get_app(app_id)
        if not app or not app.container:
            return self.send_html("# no container image registered for %r\n" % app_id, 404)
        caps = _fabric.from_request(self.headers, None)
        store = _fabric.open_store(self._principal(), app_id)
        host = (self.headers.get("Host", "") or "").split(":")[0]
        man = _fabric.tiers.container_manifest(app, store, caps.arch or "amd64", host)
        return self._browse_send("text/x-shellscript; charset=utf-8", man["recipe"].encode("utf-8"))

    def _browser_navigate(self, body):

        try:
            req = json.loads(body or "{}")
        except Exception:
            req = {}
        url = _agent_norm_url(req.get("url") or req.get("text") or "")
        if not url:
            return self._cer_json({"ok": False, "error": "empty",
                "speak": "Welche Adresse soll ich öffnen?"})
        uid = self._principal()
        res = _screen_open(url, uid)
        if res.get("ok"):
            links_add(uid, url, "opened")
        _prov_log("browser.navigate", uid, url, {"via": "screen_open", "url": res.get("url")})
        return self._cer_json(res)

    def _browse_send(self, ct, body):
        try:
            b = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _browse_fail(self, what, e):

        blocked = "403" in str(e) or "Forbidden" in str(e)
        url = html.escape(str(what))
        urljs = json.dumps(str(what))
        headline = ("Diese Seite blockt den schnellen Proxy (Bot-Schutz)"
                    if blocked else "Laden im schnellen Proxy fehlgeschlagen")

        page = ("<!doctype html><meta charset=utf-8>"
                "<body style='font:15px/1.55 system-ui,sans-serif;padding:26px;color:#e7e7f2;background:#0d0d16'>"
                "<h3 style='margin-top:0'>%s</h3>"
                "<p style='color:#9a9ab5'>%s</p>"
                "<p style='color:#c9c9de'>Ein umschreibender Proxy kann Bot-Schutz + OAuth nicht durchlaufen. Für einen Login mit <b>Session im NAS</b> brauchst du einen echten Browser <b>auf dem NAS</b>:</p>"
                "<button onclick='if(window.top.summon)window.top.summon(\"screen\")' style='background:#6b7cff;color:#0b0b14;border:0;border-radius:10px;padding:12px 18px;font:inherit;font-weight:700;cursor:pointer'>🖥 Im NAS-Browser (Screen) öffnen — Login bleibt im NAS</button>"
                "<p style='color:#6a6a85;font-size:13px;margin-top:20px'>Nur mal schnell ansehen (Login egal)? "
                "<a href='#' onclick='window.open(%s,\"_blank\");return false' style='color:#8aa0ff'>↗ im echten Client-Tab öffnen</a> — funktioniert überall, aber die Session liegt dann clientseitig, nicht im NAS.</p>"
                "<pre style='white-space:pre-wrap;color:#a06;font-size:12px;margin-top:16px'>%s</pre>"
                "</body>") % (headline, url, urljs, html.escape(str(e)))
        return self.send_html(page, 200 if blocked else 502)

    def _browse_form(self, blob, method):

        pairs = urllib.parse.parse_qsl(blob, keep_blank_values=True)
        base = ""
        rest = []
        for k, v in pairs:
            if k == "__pxurl":
                base = v
            else:
                rest.append((k, v))
        if not base.startswith(("http://", "https://")):
            return self.send_html("bad form target", 400)
        try:
            turl, data = _browse.form_target(base, rest, method)
            ct, body = _browse.render(turl, _browser_jar(self._principal()), method=method, data=data)
            _browser_save(self._principal())
        except Exception as e:
            return self._browse_fail(base, e)
        return self._browse_send(ct, body)

    def _browse_proxy(self, query):
        if _browse is None:
            return self.send_html("browse module not available", 500)
        if "__pxurl=" in query:
            return self._browse_form(query, "GET")
        qs = urllib.parse.parse_qs(query)
        url = (qs.get("url", [""])[0]) or ""
        raw = qs.get("raw", ["0"])[0] == "1"
        if not url.startswith(("http://", "https://")):
            return self.send_html("bad or missing url", 400)
        try:
            ct, body = _browse.render(url, _browser_jar(self._principal()))
            _browser_save(self._principal())
        except Exception as e:
            return self._browse_fail(url, e)
        return self._browse_send(ct, body)

    def _browse_shell(self, query):
        qs = urllib.parse.parse_qs(query)
        start = (qs.get("url", ["https://example.org"])[0]) or "https://example.org"
        senc = urllib.parse.quote(start, safe="")
        shell = ("<!doctype html><html><head><meta charset=utf-8><title>browse</title><style>"
                 "html,body{margin:0;height:100%;font:14px system-ui,sans-serif;background:#0d0d16}"
                 "#bar{display:flex;gap:6px;padding:8px;background:#171724;border-bottom:1px solid #23233a}"
                 "#bar input{flex:1;background:#0b0b14;color:#eee;border:1px solid #23233a;border-radius:8px;padding:8px 12px;font:inherit}"
                 "#bar button{background:#6b7cff;color:#0b0b14;border:0;border-radius:8px;padding:8px 12px;font:inherit;font-weight:700;cursor:pointer}"
                 "#pg{border:0;width:100%;height:calc(100% - 53px);background:#fff}"
                 "</style></head><body><div id=bar>"
                 "<button id=bk title=Zurück>◀</button>"
                 "<input id=u placeholder='URL — Ausführung im Client, Netz+Daten im NAS' value='__START__'>"
                 "<button id=go>Los</button>"
                 "<button id=real title='Seite blockt den schnellen Proxy? Im echten Browser-Tab öffnen (deine Session, funktioniert überall)'>↗ echt</button></div>"
                 "<iframe id=pg src='/api/browse?url=__STARTENC__'></iframe><script>"
                 "var u=document.getElementById('u'),pg=document.getElementById('pg');"
                 "function nav(v){if(!v)return;if(!/^https?:/i.test(v))v='https://'+v;pg.src='/api/browse?url='+encodeURIComponent(v);u.value=v;}"
                 "document.getElementById('go').onclick=function(){nav(u.value);};"
                 "document.getElementById('real').onclick=function(){window.open(u.value,'_blank');};"
                 "u.addEventListener('keydown',function(e){if(e.key==='Enter')nav(u.value);});"
                 "document.getElementById('bk').onclick=function(){try{pg.contentWindow.history.back();}catch(e){}};"
                 "</script></body></html>")
        shell = shell.replace("__START__", html.escape(start)).replace("__STARTENC__", senc)
        return self.send_html(shell)

    def _screen_stream_proxy(self):

        host, _, port = cell(self._principal()).stream.partition(":")
        try:
            up = socket.create_connection((host, int(port)), timeout=6)
        except Exception as e:
            return self.send_html(f"seat stream not up: {e}", 503)
        try:
            up.sendall(b"GET /stream HTTP/1.0\r\nConnection: close\r\n\r\n")
            hdr = b""
            while b"\r\n\r\n" not in hdr:
                ch = up.recv(4096)
                if not ch:
                    return self.send_html("seat stream closed early", 502)
                hdr += ch
            head, _, rest = hdr.partition(b"\r\n\r\n")
            ctype = "multipart/x-mixed-replace; boundary=phantomframe"
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-type:"):
                    ctype = line.split(b":", 1)[1].strip().decode("latin1"); break
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Connection", "close")
            self.end_headers()
            if rest:
                self.wfile.write(rest); self.wfile.flush()
            while True:
                ch = up.recv(65536)
                if not ch:
                    break

                self.wfile.write(ch); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try: up.close()
            except Exception: pass

    def _placement_decide(self, query):

        if _decide_placement is None:
            return self.send_html(json.dumps({"error": "placement unavailable"}), 500,
                                  [("Content-Type", "application/json")])
        q = query if isinstance(query, dict) else urllib.parse.parse_qs(query)
        flat = {k: (v[0] if isinstance(v, list) else v) for k, v in q.items()}
        out = _placement_for(flat, flat.get("policy", "auto"), flat.get("workload", "default"))
        return self.send_html(json.dumps(out), 200, [("Content-Type", "application/json")])

    def _screen_placement_proxy(self, query):

        host, _, port = cell(self._principal()).stream.partition(":")
        rel = "/placement" + (("?" + query) if query else "")
        try:
            up = socket.create_connection((host, int(port)), timeout=6)
        except Exception as e:
            return self.send_html(json.dumps({"error": f"seat not up: {e}"}), 503,
                                  [("Content-Type", "application/json")])
        try:
            up.sendall(("GET " + rel + " HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").encode("latin1"))
            raw = b""
            while True:
                ch = up.recv(65536)
                if not ch:
                    break
                raw += ch
            head, _, body = raw.partition(b"\r\n\r\n")
            status = 502
            try:
                status = int(head.split(b" ", 2)[1])
            except Exception:
                pass
            if status != 200:
                return self.send_html(json.dumps({"error": "box placement failed", "status": status}),
                                      502, [("Content-Type", "application/json")])
            return self.send_html(body, 200, [("Content-Type", "application/json")])
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            return self.send_html(json.dumps({"error": f"placement proxy: {e}"}), 502,
                                  [("Content-Type", "application/json")])
        finally:
            try: up.close()
            except Exception: pass

    def _screen_scene_proxy(self):

        host, _, port = cell(self._principal()).stream.partition(":")
        try:
            port = int(port)
        except (TypeError, ValueError):
            return self.send_html(json.dumps({"ok": False, "error": SCENE_SEAT_DOWN_DE}), 503,
                                  [("Content-Type", "application/json")])

        try:
            socket.create_connection((host, port), timeout=6).close()
        except Exception as e:
            return self.send_html(json.dumps({"ok": False, "error": SCENE_SEAT_DOWN_DE,
                                              "detail": str(e)}), 503,
                                  [("Content-Type", "application/json")])

        first, first_gen = None, None
        deadline = time.time() + SCENE_FIRST_FRAME_WAIT
        while True:
            first, first_gen = self._fetch_scene_once(host, port, keyframe=True)
            if first is not None or time.time() >= deadline:
                break
            time.sleep(0.10)
        if first is None:
            return self.send_html(json.dumps({"ok": False, "error": SCENE_NO_WINDOW_DE}), 503,
                                  [("Content-Type", "application/json")])

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")

        self.end_headers()
        last_gen = first_gen
        empty_polls = 0
        try:
            self.wfile.write(len(first).to_bytes(4, "little"))
            self.wfile.write(first)
            self.wfile.flush()
            while True:
                frame, gen = self._fetch_scene_once(host, port)
                if frame is None:

                    empty_polls += 1
                    if empty_polls > SCENE_STREAM_EMPTY_POLLS:
                        return
                    time.sleep(0.10)
                    continue
                empty_polls = 0
                if gen is not None and gen == last_gen:

                    time.sleep(0.03)
                    continue
                last_gen = gen
                self.wfile.write(len(frame).to_bytes(4, "little"))
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _fetch_scene_once(self, host, port, keyframe=False):

        try:
            up = socket.create_connection((host, port), timeout=6)
        except Exception:
            return (None, None)
        try:
            req_path = b"/scene.bin?keyframe=1" if keyframe else b"/scene.bin"
            up.sendall(b"GET " + req_path + b" HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            raw = b""
            while True:
                ch = up.recv(65536)
                if not ch:
                    break
                raw += ch
        except (BrokenPipeError, ConnectionResetError, OSError):
            return (None, None)
        finally:
            try: up.close()
            except Exception: pass
        head, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            return (None, None)
        try:
            status = int(head.split(b" ", 2)[1])
        except Exception:
            status = 502
        if status != 200 or not body:
            return (None, None)
        gen = None
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"x-phantom-gen:"):
                try:
                    gen = int(line.split(b":", 1)[1].strip())
                except Exception:
                    gen = None
                break
        return (body, gen)

    def _cast_state_dir(self):
        d = os.path.join(DATA_DIR, "casts")
        os.makedirs(d, exist_ok=True)
        return d

    def _cast_resolve(self, device):

        dev = str(device or "").strip()
        if not dev:
            return None, None, "device fehlt"
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", dev):
            return dev, dev, None
        if _DEVICE_REG is not None:
            for d in (_DEVICE_REG.list() or []):
                if dev in (d.get("id"), d.get("name")) and (d.get("kind") in ("cast", "nest-hub", "tv") or d.get("driver") == "cast"):
                    tr = d.get("transport") or {}
                    addr = tr.get("addr") or d.get("ip") or d.get("host")
                    if addr:
                        return addr, (d.get("name") or addr), None
        return None, None, "kein cast-fähiges Gerät '%s'" % dev

    def _api_cast_status(self):
        out = []
        try:
            for fn in sorted(os.listdir(self._cast_state_dir())):
                if not fn.endswith(".json"):
                    continue
                try:
                    st = json.load(open(os.path.join(self._cast_state_dir(), fn)))
                except Exception:
                    continue
                st = _cast_state_merged(st)
                st["alive"] = _cast_alive(st)
                out.append(st)
        except Exception:
            pass
        return self._vext_json({"ok": True, "casts": out})

    def _cast_source(self, source):

        s = source if isinstance(source, dict) else ({"type": "cell", "id": source} if source else {})
        typ = (s.get("type") or "seat").lower()
        if typ in ("seat", "portal", "box", ""):
            return "127.0.0.1", 5900, ("Ganzes Portal" if typ == "portal" else "Box-Bildschirm"), None
        if typ == "cell":
            cid = re.sub(r"[^A-Za-z0-9_.-]", "", str(s.get("id") or ""))[:64]
            vs = _vmcell_sock(cid)
            if not vs:
                return None, None, None, "Zelle '%s' hat keinen aktiven grafischen Bildschirm (GUI-Zelle nötig)" % cid
            port = _rfb_tcp_bridge(vs)
            if not port:
                return None, None, None, "RFB-Bridge fehlgeschlagen"
            return "127.0.0.1", port, ("Zelle " + cid), None
        return None, None, None, "unbekannte Quelle '%s'" % typ

    def _api_cast_targets(self):

        devs = []
        try:
            for d in ((_DEVICE_REG.list() or []) if _DEVICE_REG else []):
                if d.get("kind") in ("cast", "nest-hub", "tv") or d.get("driver") == "cast":
                    tr = d.get("transport") or {}
                    addr = tr.get("addr") or d.get("ip") or d.get("host")
                    if addr:
                        devs.append({"id": d.get("id"), "name": d.get("name") or addr, "addr": addr,
                                     "kind": d.get("kind"), "screens": [{"id": "", "label": d.get("name") or addr}]})
        except Exception:
            pass
        srcs = [{"type": "seat", "id": "", "label": "🖥 Box-Bildschirm (Seat)"},
                {"type": "portal", "id": "", "label": "🌐 Ganzes Portal"}]
        try:
            reg = json.load(open(os.path.join(DATA_DIR, "vmcells.json"))) or {}
            for cid, e in reg.items():
                if isinstance(e, dict) and e.get("sock") and os.path.exists(e["sock"]):
                    srcs.append({"type": "cell", "id": cid, "label": "🖳 " + (e.get("name") or cid)})
        except Exception:
            pass
        try:
            _st = _session_store(self._principal(), "cockpit") if _session_store else None
            for _sx in (_st.list() if _st else [])[:24]:
                if _sx.get("id"):
                    srcs.append({"type": "term", "id": _sx["id"],
                                 "label": "\U0001f4ac " + (_sx.get("title") or _sx["id"])})
        except Exception:
            pass
        casts = []
        try:
            for fn in sorted(os.listdir(self._cast_state_dir())):
                if fn.endswith(".json"):
                    st = json.load(open(os.path.join(self._cast_state_dir(), fn)))
                    st = _cast_state_merged(st)
                    st["alive"] = _cast_alive(st)
                    casts.append(st)
        except Exception:
            pass
        return self._vext_json({"ok": True, "devices": devs, "sources": srcs, "casts": casts})

    def _api_cast_start(self, raw):
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        addr, label, err = self._cast_resolve(body.get("device"))
        if err:
            return self._vext_json({"ok": False, "error": err}, 400)

        _venv_py = os.path.expanduser("~/.local/share/celltv-venv/bin/python")
        if not os.path.exists(_venv_py):
            try:
                import importlib.util as _ilu
                _have_cc = _ilu.find_spec("pychromecast") is not None
            except Exception:
                _have_cc = False
            if not _have_cc:
                return self._vext_json({"ok": False, "error":
                    "Cast-Laufzeit fehlt: weder ~/.local/share/celltv-venv noch pychromecast im "
                    "System-Python vorhanden — Spiegeln ist auf dieser Box nicht eingerichtet. "
                    "(Einrichtung: python3 -m venv ~/.local/share/celltv-venv && "
                    "~/.local/share/celltv-venv/bin/pip install pychromecast pyte pillow)"}, 503)

        try:
            _probe = socket.create_connection((addr, 8009), timeout=2.0)
            _probe.close()
        except Exception:
            return self._vext_json({"ok": False, "error":
                "Gerät '%s' (%s) ist nicht erreichbar (Port 8009 antwortet nicht) — ist es "
                "eingeschaltet und im selben Netz?" % (label, addr)}, 502)
        _src = body.get("source")
        term_sid = None
        if isinstance(_src, dict) and (_src.get("type") or "").lower() == "term":

            _tsid = re.sub(r"[^a-z0-9_-]", "", str(_src.get("id") or ""))[:32]
            try:
                _trec = _session_store(self._principal(), "cockpit").get(_tsid) if _session_store else None
            except Exception:
                _trec = None
            if not _trec:
                return self._vext_json({"ok": False, "error": "unbekannte Session '%s'" % _tsid}, 404)
            term_sid = _tsid
            rhost, rport, slabel = "127.0.0.1", 0, ("Terminal " + (_trec.get("title") or _tsid))
        else:
            rhost, rport, slabel, serr = self._cast_source(_src)
            if serr:
                return self._vext_json({"ok": False, "error": serr}, 400)

        if term_sid:
            input_kind, input_id = "term", term_sid
        else:
            _s = _src if isinstance(_src, dict) else ({"type": "cell", "id": _src} if _src else {})
            if (_s.get("type") or "seat").lower() == "cell":
                input_kind = "cell"
                input_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(_s.get("id") or ""))[:64]
            else:
                input_kind, input_id = "seat", None
        slug = _cast_slug(label, addr)
        sf = os.path.join(self._cast_state_dir(), slug + ".json")
        if os.path.exists(sf):
            try:
                st = json.load(open(sf))
                if _cast_alive(st):
                    return self._vext_json({"ok": True, "already": True,
                                            "cast": _cast_state_merged(st)})
                if st.get("job") and _PN is not None:

                    _PN.cancel(int(st["job"]))
                elif st.get("pid") and _pid_alive(st["pid"]):

                    try:
                        os.killpg(os.getpgid(int(st["pid"])), signal.SIGKILL)
                    except Exception:
                        try:
                            os.kill(int(st["pid"]), signal.SIGKILL)
                        except Exception:
                            pass
                try:
                    os.remove(sf)
                except OSError:
                    pass
            except Exception:
                pass
        http_port = int(body.get("http") or 8121)
        if term_sid and not body.get("http"):
            http_port = 8131 + (sum(bytearray(term_sid.encode())) % 47)
        fps = int(body.get("fps") or (6 if term_sid else 12))
        args = [sys.executable, os.path.expanduser("~/.local/bin/pn_cast_supervisor.py"),
                "--device", addr, "--name", str(label), "--http", str(http_port), "--fps", str(fps),
                "--seat-uid", self._principal(), "--rfb-host", rhost, "--rfb-port", str(rport)]
        if term_sid:
            args += ["--term-sid", term_sid]
        args += ["--input-kind", input_kind]
        if input_id:
            args += ["--input-id", input_id]

        _portal_url = "https://127.0.0.1:%d" % int(self.cfg.get("port") or 8077)

        _tokf = ""
        try:
            import portal_voice_core as _pvc
            _tok = _pvc._voice_agent_token(self._principal())
            _tdir = os.path.join(os.path.expanduser("~/.local/share/brainbox-portal"), "cast-tokens")
            os.makedirs(_tdir, exist_ok=True)
            _tokf = os.path.join(_tdir, "%s.token" % slug)
            _tfd = os.open(_tokf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(_tfd, _tok.encode()); os.close(_tfd)
        except Exception:
            _tokf = ""
        _envv = ["/usr/bin/env", "SEATCAST_PORTAL=" + _portal_url]
        if _tokf:
            _envv.append("SEATCAST_TOKEN_FILE=" + _tokf)
        if body.get("nocast"):

            _envv.append("SEATCAST_NOCAST=1")
        jid, p, _livef = None, None, sf
        if _PN is not None and _PN.pn_available():

            r = _PN.submit(_envv + args, mem=500, timeout_s=43200, tag="media.cast",
                           latency="realtime", cpu_quota=200, klass="media.cell")
            if not r.get("ok"):

                _why = ("Der Governor (pnd) lässt das Spiegeln gerade nicht zu: %s"
                        % (r.get("error") or "unbekannt"))
                _prov_log("cast.start.fail", self._principal(),
                          json.dumps({"device": addr, "name": label, "error": _why}), {"wire": "api"})
                return self._vext_json({"ok": False, "error": _why}, 503)
            jid = int(r["id"])
            _livef = os.path.join(_PN.job_tmp(jid), "cast-state.json")

            try:
                _tmp = sf + ".tmp"
                json.dump({"device": addr, "name": label, "http": http_port, "fps": fps,
                           "slug": slug, "source": slabel, "job": jid, "live": _livef,
                           "state": "starting", "started": time.time()}, open(_tmp, "w"))
                os.replace(_tmp, sf)
            except Exception:
                pass
        else:

            print("[portal] cast: Governor (pnd) nicht erreichbar — Spiegeln läuft ausnahmsweise "
                  "DIREKT (ungoverned).", file=sys.stderr, flush=True)
            try:
                lp = open("/tmp/cast-sup-%s.log" % slug, "ab")
                _env = dict(os.environ)
                _env["SEATCAST_PORTAL"] = _portal_url
                if _tokf:
                    _env["SEATCAST_TOKEN_FILE"] = _tokf
                if body.get("nocast"):
                    _env["SEATCAST_NOCAST"] = "1"
                p = subprocess.Popen(args, stdout=lp, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True, env=_env)
            except Exception as e:
                return self._vext_json({"ok": False, "error": "spawn: %s" % e}, 500)

        _ok, _why, _st = False, None, {}
        _prev_state, _cast_since = "", None
        _jstate = None
        _deadline = time.time() + (75.0 if jid else 45.0)
        while time.time() < _deadline:
            time.sleep(0.5)
            if jid is not None:
                _j = (_PN.job(jid) or {})
                _jstate = _j.get("state")
                if _jstate in _PN.TERMINAL:
                    _why = "Cast-Dienst sofort beendet (Job %d: %s)" % (jid, _jstate)
                    break
            elif p.poll() is not None:
                _why = "Cast-Dienst sofort beendet (Code %s)" % p.returncode
                break
            try:
                _st = json.load(open(_livef))
            except Exception:
                _st = {}
            _s = _st.get("state") or ""
            if _s == "restarting" and _prev_state != "restarting":
                break
            _prev_state = _s
            if _st.get("cast") in ("error", "stalled") and not body.get("nocast"):

                _why = ("Der Fernseher spielt den Stream nicht ab: %s"
                        % (" ".join((_st.get("cast_err") or "Receiver bleibt IDLE").split())[:200]))
                break
            _need_load = not body.get("nocast")
            if (_s == "casting" and _st.get("child") and _pid_alive(int(_st["child"]))
                    and (not _need_load or _st.get("cast") == "playing")):
                _cast_since = _cast_since or time.time()
                if time.time() - _cast_since >= 3.0:
                    _ok = True
                    break
            else:
                _cast_since = None
        if not _ok:

            if jid is not None:
                _PN.cancel(jid)
                if _jstate in ("queued", "staged", "blocked") and not _why:

                    _why = ("Kein Platz im Echtzeit-Band — der RAM ist durch laufende Sessions "
                            "belegt, das Spiegeln wurde nicht zugelassen. Eine Session schließen "
                            "oder später erneut versuchen.")
            else:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            try:
                os.remove(sf)
            except Exception:
                pass
            _tail = ""
            _logs = ([os.path.join(_PN.job_tmp(jid), "seatcast-%s.log" % slug)] if jid is not None
                     else []) + ["/tmp/seatcast-%s.log" % slug]
            for _lp in _logs:
                try:
                    with open(_lp, "rb") as _lf:
                        _lines = [ln for ln in _lf.read()[-4000:].decode("utf-8", "replace").splitlines()
                                  if ln.strip()]
                        if _lines:
                            _tail = _lines[-1]
                            break
                except Exception:
                    continue
            if not _tail and jid is not None:
                _tail = _PN.log_tail(jid)
            if "celltv-venv" in _tail or "No module named" in _tail:
                _why = ("Cast-Laufzeit fehlt (celltv-venv/pychromecast nicht installiert) — "
                        "Spiegeln ist auf dieser Box nicht eingerichtet.")
            elif not _why:
                _why = "Spiegeln startet nicht — der Cast-Dienst bricht ab" + \
                       ((": " + _tail[:160]) if _tail else " (kein Log).")
            _prov_log("cast.start.fail", self._principal(),
                      json.dumps({"device": addr, "name": label, "error": _why}), {"wire": "api"})
            return self._vext_json({"ok": False, "error": _why}, 502)
        _cast_row = {"device": addr, "name": label, "http": http_port, "fps": fps, "slug": slug,
                     "source": slabel, "seat_uid": self._principal(), "term_sid": term_sid,
                     "started": time.time(), "state": "casting",
                     "input_target": {"kind": input_kind, "id": input_id}}
        if jid is not None:

            _cast_row.update({"job": jid, "live": _livef,
                              "pid": _st.get("pid"), "child": _st.get("child")})
            try:
                _tmp = sf + ".tmp"
                json.dump(_cast_row, open(_tmp, "w"))
                os.replace(_tmp, sf)
            except Exception:
                pass
        else:
            _cast_row["pid"] = p.pid
        _prov_log("cast.start", self._principal(), json.dumps({"device": addr, "name": label, "source": slabel,
                                                               "job": jid}), {"wire": "api"})
        return self._vext_json({"ok": True, "cast": _cast_row})

    def _api_cast_stop(self, raw):
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        want = str(body.get("device") or "").strip()
        addr, label, _err = self._cast_resolve(want)
        stopped = []; killed_sup = False; killed_devs = set(); failed = []
        try:
            d = self._cast_state_dir()
            for fn in os.listdir(d):
                if not fn.endswith(".json"):
                    continue
                p = os.path.join(d, fn)
                try:
                    st = json.load(open(p))
                except Exception:
                    continue
                match = (not want) or want in (st.get("device"), st.get("name"), st.get("slug")) or (addr and st.get("device") == addr)
                if not match:
                    continue
                if st.get("job"):

                    _jid = int(st["job"])
                    if _PN is None:
                        failed.append(_jid)
                        continue
                    _PN.cancel(_jid)
                    _gone = False
                    for _ in range(10):
                        _j = _PN.job(_jid) or {}
                        if _j.get("state") in (None,) + _PN.TERMINAL:
                            _gone = True
                            break
                        time.sleep(0.4)
                    if _gone:
                        stopped.append(_jid)
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                    else:
                        failed.append(_jid)
                    continue
                pid = st.get("pid")
                remove_state = True
                if pid:
                    pid = int(pid)

                    try:
                        cmd = open("/proc/%d/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
                        cmd_ok = True
                    except Exception:
                        cmd = ""; cmd_ok = False
                    if "pn_cast_supervisor" in cmd:
                        killed_sup = True
                        killed_devs.add(str(st.get("device") or ""))
                        for sig in (15, 9):
                            try:
                                os.killpg(os.getpgid(pid), sig)
                            except Exception:
                                try: os.kill(pid, sig)
                                except Exception: pass
                            if _sup_gone(pid):
                                break
                            time.sleep(0.4)
                            if _sup_gone(pid):
                                break
                        if not _sup_gone(pid):

                            remove_state = False; failed.append(pid)
                        else:
                            stopped.append(pid)
                    elif (not cmd_ok) and _pid_alive(pid):

                        remove_state = False

                if remove_state:
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        except Exception:
            pass
        if killed_sup:

            _stop_all = not want
            _devs = set(d for d in killed_devs if d)
            if addr:
                _devs.add(addr)
            try:
                out = subprocess.run(["pgrep", "-f", "seatcast_service.py"], capture_output=True, text=True).stdout.split()
                for spid in out:
                    try:
                        ppid = -1
                        for ln in open("/proc/%s/status" % spid):
                            if ln.startswith("PPid:"):
                                ppid = int(ln.split()[1]); break
                        if ppid != 1:
                            continue
                        if not _stop_all:
                            sdev = ""
                            try:
                                for kv in open("/proc/%s/environ" % spid, "rb").read().split(b"\0"):
                                    if kv.startswith(b"SEATCAST_DEVICE="):
                                        sdev = kv.split(b"=", 1)[1].decode("utf-8", "replace"); break
                            except Exception:
                                sdev = ""
                            if sdev not in _devs:
                                continue
                        os.kill(int(spid), 9)
                    except Exception:
                        pass
            except Exception:
                pass
        _prov_log("cast.stop", self._principal(), json.dumps({"device": want, "stopped": stopped}), {"wire": "api"})
        return self._vext_json({"ok": True, "stopped": stopped, "failed": failed})

    def _api_cast_ingest_start(self, raw):

        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try: body = json.loads(raw or b"{}")
        except Exception: body = {}
        addr, label, err = self._cast_resolve(body.get("device"))
        if err: return self._vext_json({"ok": False, "error": err}, 400)
        screen = int(body.get("screen") or 0); port = int(body.get("port") or 9401); http = int(body.get("http") or 8120)
        name = re.sub(r"[^\w .-]", "", str(body.get("name") or "Laptop"))[:40] or "Laptop"
        ingest = os.path.join(os.path.dirname(os.path.realpath(__file__)), "celltv", "screen_cast_ingest.py")
        vpy = os.path.expanduser("~/.local/share/celltv-venv/bin/python")
        py = vpy if os.path.exists(vpy) else sys.executable
        if py is sys.executable:

            try:
                import importlib.util as _ilu
                _have_cc = _ilu.find_spec("pychromecast") is not None
            except Exception:
                _have_cc = False
            if not _have_cc:
                return self._vext_json({"ok": False, "error":
                    "Cast-Laufzeit fehlt (celltv-venv/pychromecast nicht installiert) — "
                    "Ingest-Cast ist auf dieser Box nicht eingerichtet."}, 503)
        _ing_args = [py, ingest, "--device", addr, "--name", name, "--port", str(port), "--http", str(http)]
        _ing_row = {"device": addr, "name": label, "port": port, "http": http, "kind": "ingest"}

        if _PN is not None and _PN.pn_available():
            r = _PN.submit(_ing_args, mem=400, timeout_s=43200, tag="media.ingest",
                           latency="realtime", cpu_quota=300)
            if not r.get("ok"):
                return self._vext_json({"ok": False, "error":
                    "Der Governor (pnd) lässt den Ingest-Cast gerade nicht zu: %s"
                    % (r.get("error") or "unbekannt")}, 503)
            _ing_row["job"] = int(r["id"])
            p = None

            _ing_state = None
            _ing_deadline = time.time() + 12.0
            while time.time() < _ing_deadline:
                _ing_state = (_PN.job(_ing_row["job"]) or {}).get("state")
                if _ing_state == "running" or _ing_state in _PN.TERMINAL:
                    break
                time.sleep(0.5)
            if _ing_state != "running":
                _PN.cancel(_ing_row["job"])
                return self._vext_json({"ok": False, "error":
                    ("Kein Platz im Echtzeit-Band — der RAM ist durch laufende Sessions belegt, "
                     "der Ingest-Cast wurde nicht zugelassen." if _ing_state in ("queued", "staged")
                     else "Ingest-Cast startete nicht (Job %d: %s)." % (_ing_row["job"], _ing_state))}, 503)
        else:

            print("[portal] ingest: Governor (pnd) nicht erreichbar — Ingest-Cast läuft "
                  "ausnahmsweise DIREKT (ungoverned).", file=sys.stderr, flush=True)
            try:
                lp = open("/tmp/screen-ingest-%d.log" % port, "ab")
                p = subprocess.Popen(_ing_args, stdout=lp, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
            except Exception as e:
                return self._vext_json({"ok": False, "error": "spawn: %s" % e}, 500)
            _ing_row["pid"] = p.pid
        try:
            open(os.path.join(self._cast_state_dir(), "ingest-%d.json" % port), "w").write(
                json.dumps(_ing_row))
        except Exception:
            pass
        boxip = self.cfg.get("lan_ip") or lan_ip()
        push = ("ffmpeg -f lavfi -i ddagrab=output_idx=%d:framerate=12 -vf hwdownload,format=bgra,"
                "scale=1600:900:force_original_aspect_ratio=decrease -c:v libx264 -preset veryfast "
                "-tune zerolatency -pix_fmt yuv420p -b:v 5000k -g 24 -f mpegts tcp://%s:%d" % (screen, boxip, port))
        _prov_log("cast.ingest", self._principal(), json.dumps({"device": addr, "screen": screen,
                                                                "job": _ing_row.get("job")}), {"wire": "api"})
        return self._vext_json({"ok": True, "ingest": {"device": addr, "name": label, "port": port,
                                                       "pid": _ing_row.get("pid"),
                                                       "job": _ing_row.get("job")},
                                "push_cmd": push,
                                "hint": "Diesen ffmpeg-Befehl auf dem Windows-Laptop ausführen (ffmpeg nötig; output_idx = Monitor-Nr.)."})

    def _api_cast_ingest_stop(self, raw):

        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        killed = 0; d = self._cast_state_dir(); any_legacy = False
        for fn in list(os.listdir(d)):
            if fn.startswith("ingest-") and fn.endswith(".json"):
                p = os.path.join(d, fn)
                try:
                    row = json.load(open(p)) or {}
                    if row.get("job") and _PN is not None:

                        _PN.cancel(int(row["job"]))
                        killed += 1
                    elif row.get("pid"):
                        any_legacy = True
                        pid = int(row["pid"])
                        try: os.killpg(os.getpgid(pid), 15)
                        except Exception:
                            try: os.kill(pid, 15)
                            except Exception: pass
                        killed += 1
                except Exception: pass
                try: os.remove(p)
                except Exception: pass
        if any_legacy:

            try: subprocess.run(["pkill", "-f", "screen_cast_ingest.py"])
            except Exception: pass
        _prov_log("cast.ingest.stop", self._principal(), "{}", {"wire": "api"})
        return self._vext_json({"ok": True, "stopped": killed})

    _CELLTV_LANES = {

        "samsung": ("cell_tv_stream.py", 512, 200, "Samsung-TV-Zelle (DLNA)"),
        "cast":    ("cast_tv_stream.py", 900, 300, "Cast-Zellen (Chromecast/Nest, HLS)"),
    }

    def _celltv_state_file(self, lane):
        return os.path.join(self._cast_state_dir(), "celltv-%s.json" % lane)

    def _api_celltv_status(self):

        out = {}
        for lane in self._CELLTV_LANES:
            row = None
            try:
                row = json.load(open(self._celltv_state_file(lane)))
            except Exception:
                row = None
            if row and row.get("job") and _PN is not None:
                j = _PN.job(int(row["job"])) or {}
                row["job_state"] = j.get("state")
            out[lane] = row
        return self._vext_json({"ok": True, "lanes": out})

    def _api_celltv_start(self, raw):

        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        lane = str(body.get("lane") or "samsung").strip().lower()
        if lane not in self._CELLTV_LANES:
            return self._vext_json({"ok": False, "error":
                "Unbekannte TV-Lane '%s' (erlaubt: %s)." % (lane, ", ".join(self._CELLTV_LANES))}, 400)
        script_name, mem, cpuq, label = self._CELLTV_LANES[lane]
        script = os.path.join(os.path.dirname(os.path.realpath(__file__)), "celltv", script_name)
        if not os.path.exists(script):
            return self._vext_json({"ok": False, "error":
                "TV-Lane-Skript fehlt auf dieser Box (%s)." % script_name}, 503)
        if not _shutil.which("ffmpeg"):
            return self._vext_json({"ok": False, "error":
                "ffmpeg fehlt — die TV-Lane kann auf dieser Box nicht kodieren."}, 503)
        vpy = os.path.expanduser("~/.local/share/celltv-venv/bin/python")
        py = vpy if os.path.exists(vpy) else sys.executable
        if lane == "cast" and py == sys.executable and not body.get("no_cast"):

            try:
                import importlib.util as _ilu
                _have_cc = _ilu.find_spec("pychromecast") is not None
            except Exception:
                _have_cc = False
            if not _have_cc:
                return self._vext_json({"ok": False, "error":
                    "Cast-Laufzeit fehlt (celltv-venv/pychromecast nicht installiert)."}, 503)
        sf = self._celltv_state_file(lane)
        try:
            old = json.load(open(sf))
            if old.get("job") and _PN is not None:
                oj = _PN.job(int(old["job"])) or {}
                if oj.get("state") == "running":
                    return self._vext_json({"ok": True, "already": True, "lane": lane,
                                            "job": int(old["job"])})
                _PN.cancel(int(old["job"]))
            os.remove(sf)
        except Exception:
            pass
        args = [py, script]
        if lane == "samsung" and body.get("no_push"):
            args.append("--no-push")
        if lane == "cast":
            if body.get("no_cast"):
                args.append("--no-cast")
            dev = str(body.get("device") or "").strip()
            if dev:
                if not re.match(r"^[0-9A-Za-z_.:-]{1,64}$", dev):
                    return self._vext_json({"ok": False, "error": "Ungültige Geräteadresse."}, 400)
                args += ["--device", dev]
        if _PN is None or not _PN.pn_available():
            return self._vext_json({"ok": False, "error":
                "Der Governor (pnd) ist nicht erreichbar — die TV-Lane läuft nur governt, "
                "kein ungovernter Ersatzstart."}, 503)
        r = _PN.submit(args, mem=mem, timeout_s=43200, tag="media.celltv." + lane,
                       latency="realtime", cpu_quota=cpuq, klass="media.cell")
        if not r.get("ok"):
            return self._vext_json({"ok": False, "error":
                "Der Governor (pnd) lässt die TV-Lane gerade nicht zu: %s"
                % (r.get("error") or "unbekannt")}, 503)
        jid = int(r["id"])
        try:
            json.dump({"lane": lane, "label": label, "job": jid, "args": args,
                       "started": time.time()}, open(sf + ".tmp", "w"))
            os.replace(sf + ".tmp", sf)
        except Exception:
            pass

        _state, _deadline = None, time.time() + 12.0
        while time.time() < _deadline:
            _state = (_PN.job(jid) or {}).get("state")
            if _state == "running" or _state in _PN.TERMINAL:
                break
            time.sleep(0.5)
        if _state == "running":
            time.sleep(3.0)
            _state = (_PN.job(jid) or {}).get("state")
        if _state != "running":
            _why = None
            if _state in ("queued", "staged", "blocked"):
                _eta = _PN.pn_req({"verb": "eta", "id": jid}, timeout=5) or {}
                _why = ((_eta.get("eta") or {}).get("wait_reason_de")
                        or "Kein Platz im Echtzeit-Band — die TV-Lane wurde nicht zugelassen.")
            _PN.cancel(jid)
            if not _why:
                _tail = _PN.log_tail(jid)
                _why = ("TV-Lane startete nicht (Job %d: %s)%s." %
                        (jid, _state, (": " + _tail[:160]) if _tail else ""))
            try:
                os.remove(sf)
            except Exception:
                pass
            _prov_log("celltv.start.fail", self._principal(),
                      json.dumps({"lane": lane, "job": jid, "error": _why}), {"wire": "api"})
            return self._vext_json({"ok": False, "error": _why, "job": jid}, 503)
        _prov_log("celltv.start", self._principal(),
                  json.dumps({"lane": lane, "job": jid}), {"wire": "api"})
        return self._vext_json({"ok": True, "lane": lane, "label": label, "job": jid})

    def _api_celltv_stop(self, raw):

        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "nur Owner/Admin"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        want = str(body.get("lane") or "").strip().lower()
        lanes = [want] if want in self._CELLTV_LANES else list(self._CELLTV_LANES)
        stopped, failed = [], []
        for lane in lanes:
            sf = self._celltv_state_file(lane)
            try:
                row = json.load(open(sf))
            except Exception:
                continue
            jid = row.get("job")
            if jid and _PN is not None:
                _PN.cancel(int(jid))
                _gone = False
                for _ in range(10):
                    _j = _PN.job(int(jid)) or {}
                    if _j.get("state") in _PN.TERMINAL or not _j:
                        _gone = True
                        break
                    time.sleep(0.3)
                (stopped if _gone else failed).append({"lane": lane, "job": jid})
                if not _gone:
                    continue
            try:
                os.remove(sf)
            except Exception:
                pass
        _prov_log("celltv.stop", self._principal(),
                  json.dumps({"stopped": stopped, "failed": failed}), {"wire": "api"})
        if failed:
            return self._vext_json({"ok": False, "stopped": stopped, "failed": failed,
                                    "error": "TV-Lane-Job ließ sich nicht beenden (%s)."
                                    % ", ".join(str(f["job"]) for f in failed)}, 502)
        return self._vext_json({"ok": True, "stopped": stopped})

    def _html_asset(self, name, missing="view not deployed"):

        p = os.path.join(os.path.dirname(os.path.realpath(__file__)), name)
        try:
            return self.send_html(open(p, "rb").read())
        except Exception:
            return self.send_html(missing, 404)

    def _api_displays(self):

        if _DISPLAY_REG is None:
            return self._vext_json({"ok": True, "displays": []})
        return self._vext_json({"ok": True, "displays": _DISPLAY_REG.list()})

    def _api_display_show(self, raw):

        if _DISPLAY_REG is None or _vext is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = str(body.get("display") or "local")
        did = _DISPLAY_REG.resolve(did) or did
        ref = body.get("ref") or {}
        if not isinstance(ref, dict):
            return self._vext_json({"ok": False, "error": "ref must be an object"}, 400)
        uid = self._principal()
        if ref.get("kind") in ("file", "object", "path"):
            _txt, err = _vext.resolve_ref(_vext_ctx(), uid, ref)
            if err:
                return self._vext_json({"ok": False, "error": err}, 400)
        res, err = _DISPLAY_REG.show(uid, did, ref, kiosk_post=_kiosk_post)
        _prov_log("display.show", uid, json.dumps({"display": did, "kind": ref.get("kind")}), {"wire": "api"})
        if err:
            return self._vext_json({"ok": False, "error": err}, 400)
        out = {"ok": True, "result": res}
        if isinstance(res, dict) and res.get("shown") is False:
            out["shown"] = False
            out["warning"] = res.get("warning") or "nicht angezeigt — Ziel (Fernseher) scheint aus/nicht erreichbar"
        return self._vext_json(out)

    def _api_display_restore(self, raw):

        if _DISPLAY_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        did = str(body.get("display") or "local")
        res, err = _DISPLAY_REG.restore_idle(self._principal(), did, kiosk_post=_kiosk_post)
        if err:
            return self._vext_json({"ok": False, "error": err}, 400)
        return self._vext_json({"ok": True, "result": res})

    def _api_display_label(self, raw):

        if _DISPLAY_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = _DISPLAY_REG.resolve(str(body.get("display") or "")) or str(body.get("display") or "")
        rec = _DISPLAY_REG.set_label(did, str(body.get("label") or ""))
        if rec is None:
            return self._vext_json({"ok": False, "error": "unknown display"}, 404)
        _prov_log("display.label", self._principal(), json.dumps({"id": did, "label": rec.get("label")}), {"wire": "api"})
        return self._vext_json({"ok": True, "display": rec})

    def _api_display_register(self, raw):

        if _DISPLAY_REG is None:
            return self._vext_json({"ok": False, "error": "unavailable"}, 503)
        if not self._is_admin():
            return self._vext_json({"ok": False, "error": "admin only"}, 403)
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._vext_json({"ok": False, "error": "bad json"}, 400)
        did = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("id") or ""))[:40]
        endpoint = str(body.get("endpoint") or "")
        if not did or not endpoint.startswith(("http://", "https://")):
            return self._vext_json({"ok": False, "error": "id + http(s) endpoint required"}, 400)
        disp = _DISPLAY_REG.register(did, str(body.get("name") or did)[:60],
                                     str(body.get("kind") or "kiosk"), "http", endpoint)
        _prov_log("display.register", self._principal(), json.dumps({"id": did, "endpoint": endpoint}), {"wire": "api"})
        return self._vext_json({"ok": True, "display": disp})

    def _serve_artifact(self, jid, name):
        jid = os.path.basename(jid); name = os.path.basename(urllib.parse.unquote(name))

        if job_get(jid, self._principal(), self._is_admin()) is None:
            return self.send_html("not found", 404)
        base = os.path.join(JOBS_DIR, jid)
        full = os.path.join(base, "artifacts", name)
        if not os.path.realpath(full).startswith(os.path.realpath(base)) or not os.path.isfile(full):
            return self.send_html("not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _serve_attach(self, rel):
        name = os.path.basename(urllib.parse.unquote(rel))

        if not self._is_admin() and _attach_owner(name) != self._principal():
            return self.send_html("not found", 404)
        full = os.path.join(ATTACH_DIR, name)
        if not os.path.realpath(full).startswith(os.path.realpath(ATTACH_DIR)) or not os.path.isfile(full):
            return self.send_html("not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _serve_novnc(self, rel):

        root = "/usr/share/novnc"
        safe = os.path.normpath(rel).lstrip("./")
        full = os.path.join(root, safe)
        if not os.path.realpath(full).startswith(os.path.realpath(root)) or not os.path.isfile(full):
            return self.send_html("not found", 404)
        ctype = ("text/html; charset=utf-8" if full.endswith(".html")
                 else "text/css" if full.endswith(".css")
                 else "application/javascript" if (full.endswith(".js") or full.endswith(".mjs"))
                 else "application/json" if full.endswith(".json")
                 else "image/svg+xml" if full.endswith(".svg")
                 else "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _vnc3_page(self):

        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        tok = cookie["pp_session"].value if "pp_session" in cookie else ""
        return self.send_html(VNC3_HTML.replace("%TOKEN%", json.dumps(tok)))

    def _serve_static(self, rel):
        safe = os.path.normpath(rel).lstrip("./")
        full = os.path.join(STATIC_DIR, safe)
        if not (os.path.realpath(full).startswith(os.path.realpath(STATIC_DIR)) and os.path.isfile(full)):

            vend_root = os.path.join(WEBAPP_DIR, "static")
            vend = os.path.join(vend_root, safe)
            if os.path.realpath(vend).startswith(os.path.realpath(vend_root)) and os.path.isfile(vend):
                full = vend
            else:
                return self.send_html("not found", 404)

        ctype = ("application/wasm" if full.endswith(".wasm")
                 else "text/css" if full.endswith(".css")
                 else "application/javascript" if (full.endswith(".js") or full.endswith(".mjs"))
                 else "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _serve_webapp(self, name):

        safe = os.path.normpath(name or "app.html").lstrip("./")
        if not safe or safe.endswith("/"):
            safe = "app.html"
        full = os.path.join(WEBAPP_DIR, safe)
        if not os.path.realpath(full).startswith(os.path.realpath(WEBAPP_DIR)) or not os.path.isfile(full):
            return self.send_html("not found", 404)

        _lang = "de"
        _i18n = None
        try:
            import portal_i18n as _i18n
            _lang = _i18n.ui_lang(getattr(self, "cfg", None), self.headers.get("Cookie", ""))
        except Exception:
            _i18n = None
            _lang = "de"
        if full.endswith(".html"):
            _html = self._inject_wstoken(open(full, encoding="utf-8").read())
            if _i18n is not None:
                _html = _i18n.inject_switcher(_html, _lang)
            return self.send_html(_html)
        ctype = ("text/css" if full.endswith(".css")
                 else "text/javascript" if full.endswith(".js")
                 else "application/manifest+json" if full.endswith(".webmanifest")
                 else "image/svg+xml" if full.endswith(".svg")
                 else "image/png" if full.endswith(".png")
                 else "image/x-icon" if full.endswith(".ico")
                 else "image/jpeg" if full.endswith((".jpg", ".jpeg"))
                 else "application/json" if full.endswith(".json")
                 else "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()

        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
