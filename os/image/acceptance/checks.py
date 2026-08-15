

import json
import re
import socket
import struct
import time

from . import GREEN, RED, AMBER, SKIP
from . import discover, wsclient

STORM_THRESHOLD = 3

def check_portal(rep, portal):
    g = "portal"
    r = portal.get("/")
    if r.status == 0:
        rep.red("portal reachable", r.text[:90], g)
        return None
    if r.status not in (200, 302, 303, 401, 403):
        rep.red("portal reachable", "HTTP %d" % r.status, g)
        return None
    rep.green("portal reachable", "HTTP %d" % r.status, g)
    return r

def check_real_ui(rep, portal, surf):

    g = "portal"
    root = surf.docs.get("/", "")
    if not root:
        rep.red("serves real UI", "no HTML body at /", g)
        return
    have_title = "<title" in root.lower()
    scripts = len(re.findall(r"<script\b", root, re.I))
    if not have_title or scripts == 0 or len(root) < 2000:
        rep.red("serves real UI",
                "looks like a stub: %d bytes, %d <script>, title=%s"
                % (len(root), scripts, have_title), g)
    else:
        rep.green("serves real UI",
                  "%d bytes, %d scripts" % (len(root), scripts), g)

    bad = {a: s for a, s in surf.assets.items() if s not in (200, 204, 301, 302, 304)}
    if bad:
        rep.red("UI assets resolve",
                "%d dead: %s" % (len(bad), ", ".join("%s=%s" % (k, v) for k, v in list(bad.items())[:4])),
                g, {"dead_assets": bad})
    else:
        rep.green("UI assets resolve", "%d assets, all served" % len(surf.assets), g)

LOGIN_FALLBACKS = ("owner", "admin", "brainbox", "tester")

def check_login(rep, portal, user, password):
    g = "auth"
    tried = []
    ok, r = portal.login(user, password)
    tried.append(user)
    if not ok:
        for cand in LOGIN_FALLBACKS:
            if cand == user:
                continue
            portal.cookiejar.clear()
            ok, r = portal.login(cand, password)
            tried.append(cand)
            if ok:

                rep.amber("login principal name (harness fallback)",
                          "configured name '%s' did not match; harness retried its own credential "
                          "under fallback names and '%s' was the correct principal (expected when the "
                          "creds file carries only the owner PIN, not a product defect)"
                          % (user, cand), g)
                user = cand
                break
    if not ok:
        rep.red("login via /api/login",
                "HTTP %d for principals %s" % (r.status, "/".join(tried)), g)
        return False
    rep.green("login via /api/login",
              "HTTP %d as '%s', session cookie set" % (r.status, user), g)
    ok2, status = portal.logged_in()
    if not ok2:
        rep.red("authenticated session usable", "/api/status did not confirm", g)
        return False
    rep.green("authenticated session usable",
              "principal=%s role=%s" % (status.get("principal"), status.get("role")), g)
    return True

def check_auth_enforced(rep, target):

    from .portal import Portal
    g = "auth"
    anon = Portal(target)
    r = anon.get("/api/admin/users")

    if r.status in (301, 302, 303, 307, 308):
        loc = r.headers.get("location", "")
        rep.green("admin API rejects anonymous",
                  "HTTP %d -> %s (bounced to login)" % (r.status, loc[:40] or "?"), g)
    elif r.status in (401, 403) or (r.json() or {}).get("ok") is False:
        rep.green("admin API rejects anonymous", "HTTP %d" % r.status, g)
    elif r.status == 404:
        rep.amber("admin API rejects anonymous", "endpoint absent (HTTP 404)", g)
    elif r.status == 200 and (r.json() or {}).get("users") is not None:
        rep.red("admin API rejects anonymous",
                "HTTP 200 with a user list -- admin data served without login", g)
    else:
        rep.red("admin API rejects anonymous",
                "HTTP %d -- admin surface answered an anonymous caller" % r.status, g)

def check_discovered_surface(rep, portal, surf, post_probe=False):
    g = "discovered surface"
    frac, mapped, total = surf.mapped_fraction()
    auto = len([c for c in surf.controls if c.mapped_by == "auto"])
    ann = len([c for c in surf.controls if c.mapped_by == "annotation"])
    client_only = len([c for c in surf.controls
                       if (c.handler or "").lstrip("#") in discover.CLIENT_ONLY])
    rep.green("controls discovered from UI",
              "%d controls, %d endpoints, %d ws routes (auto %d, annotated %d, "
              "client-only %d, unmapped %d)"
              % (total, len(surf.api), len(surf.ws), auto, ann, client_only,
                 total - mapped - client_only),
              g, {"controls": total, "auto": auto, "annotated": ann,
                  "client_only": client_only,
                  "endpoints": sorted(surf.api), "ws": sorted(surf.ws),
                  "methods": {k: sorted(v) for k, v in surf.methods.items()}})

    if not surf.api:
        rep.red("endpoints extracted", "parsed no /api/ endpoints -- extractor blind", g)
        return {}

    results = discover.probe_endpoints(portal, surf, post_probe=post_probe)
    missing = {k: v for k, v in results.items() if v["verdict"] == "missing"}
    broken = {k: v for k, v in results.items() if v["verdict"] == "broken"}
    unprobed = [k for k, v in results.items() if v["verdict"] == "unprobed"]
    unverified = [k for k, v in results.items() if v["verdict"] == "unverified"]
    base = [k for k, v in results.items() if v["verdict"] == "base"]
    degraded = {k: v for k, v in results.items() if v["verdict"] == "degraded"}
    okn = len([v for v in results.values() if v["verdict"] == "ok"])

    if degraded:
        rep.amber("degraded endpoints report honestly",
                  "%d unavailable with a stated reason: %s"
                  % (len(degraded),
                     "; ".join("%s %s" % (k, v["note"]) for k, v in list(degraded.items())[:2])),
                  g, {"degraded": degraded})

    if broken:
        rep.red("no endpoint 500s / tracebacks",
                "%d broken: %s" % (len(broken),
                                   "; ".join("%s %s" % (k, v["note"]) for k, v in list(broken.items())[:3])),
                g, {"broken": broken})
    else:
        rep.green("no endpoint 500s / tracebacks", "%d endpoints clean" % okn, g)

    if missing:
        rep.red("every UI endpoint has a handler",
                "%d called by the UI with GET but 404: %s"
                % (len(missing), ", ".join(sorted(missing)[:5])),
                g, {"missing": missing})
    else:
        rep.green("every UI endpoint has a handler",
                  "%d verified live, %d inconclusive without --post-probe"
                  % (okn, len(unverified)), g)

    if unprobed or unverified or base:
        rep.amber("endpoints not conclusively probed",
                  "%d destructive, %d POST-only (GET inconclusive), %d base paths"
                  % (len(unprobed), len(unverified), len(base)),
                  g, {"destructive": sorted(unprobed), "post_only": sorted(unverified),
                      "base": sorted(base)})

    dead_controls = []
    for c in surf.controls:
        for ep in c.endpoints:
            v = results.get(ep, {}).get("verdict")
            if v in ("missing", "broken"):
                dead_controls.append("%s -> %s (%s)" % (c.label or c.handler, ep, v))
    if dead_controls:
        rep.red("no control leads nowhere",
                "%d dead: %s" % (len(dead_controls), "; ".join(dead_controls[:3])),
                g, {"dead_controls": dead_controls})
    else:
        rep.green("no control leads nowhere", "%d mapped controls all live" % mapped, g)
    return results

def check_ws_routes(rep, portal, surf, seconds, sid=None):
    g = "websockets"
    routes = sorted(surf.ws) or ["/ws/term", "/ws/feed"]
    origin = portal.target
    live = []
    for route in routes:
        url = portal.ws_url(route)
        hs = wsclient.handshake(url, cookie=portal.cookie_header, origin=origin)
        if hs.ok:
            hs.close()
            rep.green("WS %s upgrades" % route, "101 Switching Protocols", g)
            live.append(route)
        else:

            status = hs.status
            if status in (401, 403):
                rep.red("WS %s upgrades" % route,
                        "pre-handshake %s -- route rejects before upgrade (browser shows nothing)"
                        % hs.describe(), g, {"prehandshake": status})
            elif status == 404:
                rep.red("WS %s upgrades" % route,
                        "pre-handshake %s -- route absent (browser shows nothing)" % hs.describe(),
                        g, {"prehandshake": status})
            else:
                rep.red("WS %s upgrades" % route, hs.describe(), g, {"prehandshake": status})

    made_sid = None
    need_session = any(r.rstrip("/").endswith("term") for r in live)
    if need_session and not sid:
        r = portal.post("/api/sessions/new",
                        {"title": "acc-ws-%d" % int(time.time()), "kind": "cockpit"},
                        ctype="json", timeout=60)
        j = r.json() or {}
        if r.status >= 400 or not j.get("ok"):
            rep.red("WS /ws/term needs a live session",
                    "could not create one to attach to: HTTP %d %s" % (r.status, r.text[:70]), g)
        else:
            sid = made_sid = j.get("id") or (j.get("session") or {}).get("id")
            if not sid:
                rep.red("WS /ws/term needs a live session",
                        "create returned ok but no session id", g)

    try:

        for route in live:
            is_term = route.rstrip("/").endswith("term")
            if is_term and not sid:

                rep.red("WS %s stable %ds" % (route, seconds),
                        "no session to attach to -- survival not measured", g)
                continue
            url = portal.ws_url(route)
            if is_term and sid:
                url += ("&" if "?" in url else "?") + "sid=" + sid
            res = wsclient.count_opens(url, seconds, cookie=portal.cookie_header, origin=origin)
            opens = res["opens"]
            if res["stayed"] and opens <= 1:
                rep.green("WS %s stable %ds" % (route, seconds),
                          "1 open, held to the end", g, res)
            elif res.get("stopped_terminal"):

                rep.red("WS %s stable %ds" % (route, seconds),
                        "refused, not retried (correct): %s" % res["last_reason"], g, res)
            elif opens > STORM_THRESHOLD:
                rep.red("WS %s stable %ds" % (route, seconds),
                        "RECONNECT STORM: %d opens in %ds (%s)"
                        % (opens, seconds, res["last_reason"]), g, res)
            else:
                rep.amber("WS %s stable %ds" % (route, seconds),
                          "%d opens, last: %s" % (opens, res["last_reason"]), g, res)
    finally:

        if made_sid:
            try:
                portal.post("/api/session/kill", {"sid": made_sid}, ctype="json", timeout=60)
            except Exception:
                pass
            deadline = time.time() + 12
            while time.time() < deadline:
                time.sleep(1.5)
                try:
                    listed = (portal.get("/api/sessions").json() or {}).get("sessions") or []
                except Exception:
                    break
                if not any((s.get("id") == made_sid) for s in listed):
                    break

HOSTSHELL_CLOSE_CODE = 4004

def _ws_close_frame(hs, timeout=8.0):

    sock = hs.sock
    buf = getattr(hs, "_leftover", b"")
    sock.settimeout(1.0)
    deadline = time.time() + timeout
    seen_text = 0
    while time.time() < deadline:
        try:
            fr, buf = wsclient.read_frame(sock, buf)
        except socket.timeout:
            continue
        except Exception as e:
            return None, "", "read error: %s" % e
        if fr is None:
            return None, "", "peer closed (EOF) without a CLOSE frame -- %d data frame(s)" % seen_text
        if fr.opcode == wsclient.OP_CLOSE:
            if len(fr.payload) < 2:
                return None, "", "CLOSE frame carried no status code"
            code = struct.unpack(">H", fr.payload[:2])[0]
            reason = fr.payload[2:].decode("utf-8", "replace")
            return code, reason, "%d in-band frame(s) before CLOSE" % seen_text
        seen_text += 1
    return None, "", "no CLOSE frame within %.0fs" % timeout

def check_hostshell_retired(rep, portal, guest=None):
    g = "websockets"
    before = ""
    if guest and guest.enabled:
        before = guest.run("tmux ls 2>/dev/null || true") or ""

    url = portal.ws_url("/ws/term") + "?target=shell"
    hs = wsclient.handshake(url, cookie=portal.cookie_header, origin=portal.target)
    if not hs.ok:

        rep.red("host shell refused honestly (/ws/term?target=shell)",
                "refused PRE-handshake (%s) -- a browser sees only 1006 and no reason"
                % hs.describe(), g, {"prehandshake": hs.status})
        return
    code, reason, note = _ws_close_frame(hs)
    hs.close()
    if code == HOSTSHELL_CLOSE_CODE and reason.strip():
        rep.green("host shell refused honestly (/ws/term?target=shell)",
                  "handshake completed, CLOSE %d: %s" % (code, reason), g,
                  {"code": code, "reason": reason, "note": note})
    elif code is None:
        rep.red("host shell refused honestly (/ws/term?target=shell)",
                "upgraded but no usable CLOSE frame: %s" % note, g)
    elif code != HOSTSHELL_CLOSE_CODE:
        rep.red("host shell refused honestly (/ws/term?target=shell)",
                "closed with %d (expected %d): %s" % (code, HOSTSHELL_CLOSE_CODE, reason), g)
    else:
        rep.red("host shell refused honestly (/ws/term?target=shell)",
                "CLOSE %d carried an EMPTY reason -- the user is told nothing" % code, g)

    if guest and guest.enabled:
        after = guest.run("tmux ls 2>/dev/null || true") or ""
        new = [ln for ln in after.splitlines()
               if ln.strip() and ln not in before.splitlines()]
        spawned = [ln for ln in new if "-shell" in ln.split(":", 1)[0]]
        if spawned:
            rep.red("host shell attempt spawns no tmux",
                    "attempt created %s" % "; ".join(spawned), g, {"new": new})
        else:
            rep.green("host shell attempt spawns no tmux",
                      "no new *-shell session (%d other new session(s))" % len(new), g)

        stale = [ln for ln in after.splitlines()
                 if ln.strip() and "-shell" in ln.split(":", 1)[0]]
        if stale:
            rep.amber("no leftover host shell sessions",
                      "pre-existing: %s" % "; ".join(stale), g, {"stale": stale})
        else:
            rep.green("no leftover host shell sessions", "none", g)
    else:
        rep.skip("host shell attempt spawns no tmux", "no in-guest access", g)

SEAT_BIN_SEARCH = (
    "command -v phantom >/dev/null 2>&1 && echo YES; "
    "find ~/brainarbeit -maxdepth 5 -type f -perm -u+x "
    "\\( -name phantom -o -name rfbd -o -name Xvnc \\) 2>/dev/null | head -1 | grep -q . && echo YES; "
    "pgrep -x 'rfbd|x11vnc|Xvfb' >/dev/null 2>&1 && echo YES"
)

CAP_EVIDENCE = {
    "cockpit": ("terminal/cockpit", "command -v tmux >/dev/null && echo YES"),
    "screen": ("Screen/seat", SEAT_BIN_SEARCH),
    "voice_tts_server": ("voice TTS", "echo MAYBE"),
    "voice_stt_server": ("voice STT", "echo MAYBE"),
    "rooms": ("rooms", "echo MAYBE"),
    "fusion": ("fusion", "echo MAYBE"),
    "email": ("email", "echo MAYBE"),
    "email_send": ("email send", "echo MAYBE"),
}

CELL_EVIDENCE = """
[ -e /dev/kvm ] && echo KVM
find ~/brainarbeit -maxdepth 6 -type f -perm -u+x -name 'pn-vmm' 2>/dev/null | head -1 | grep -q . && echo PNVMM
find ~/brainarbeit -maxdepth 6 -type f \\( -name 'vmlinux*' -o -name 'bzImage' \\) 2>/dev/null | head -1 | grep -q . && echo KERNEL
find ~/brainarbeit -maxdepth 6 -type f -name '*.img' 2>/dev/null | head -1 | grep -q . && echo ROOTFS
command -v tmux >/dev/null && echo TMUX
grep -q '^CELLS_ENABLED=1' /etc/brainbox/caps.env 2>/dev/null && echo CELLS_ENABLED
"""

def check_capability_honesty(rep, portal, guest):
    g = "capability honesty"
    _, status = portal.logged_in()
    caps = (status or {}).get("caps") or {}
    if not caps:
        rep.amber("capabilities reported", "/api/status exposed no caps map", g)
        return caps
    on = [k for k, v in caps.items() if v]
    off = [k for k, v in caps.items() if not v]
    rep.green("capabilities reported",
              "%d on (%s), %d off (%s)" % (len(on), ",".join(on) or "-", len(off), ",".join(off) or "-"),
              g, {"caps": caps})

    if not (guest and guest.enabled):
        rep.skip("capabilities backed by binaries", "no in-guest access (--ssh-host)", g)
        return caps

    for cap, val in sorted(caps.items()):
        human, expr = CAP_EVIDENCE.get(cap, (cap, "echo MAYBE"))
        out = guest.run(expr) or ""
        if "MAYBE" in out:
            continue
        backed = "YES" in out
        if val and not backed:

            rep.red("cap '%s' backed by a binary" % cap,
                    "reported AVAILABLE but backing binary/feature missing", g)
        elif val and backed:
            rep.green("cap '%s' backed by a binary" % cap, "available and backed", g)
        elif not val and not backed:
            rep.amber("cap '%s' honestly disabled" % cap,
                      "%s unavailable and reported false (honest, feature is dead for the user)" % human, g)
        else:
            rep.amber("cap '%s' honestly disabled" % cap,
                      "backing present but reported false -- feature needlessly off", g)
    return caps

def check_cell_substrate(rep, guest, nested=False):
    g = "capability honesty"
    if not (guest and guest.enabled):
        rep.skip("cell substrate present", "no in-guest access", g)
        return
    out = guest.run(CELL_EVIDENCE) or ""
    want = ["KVM", "PNVMM", "KERNEL", "ROOTFS", "TMUX"]
    missing = [w for w in want if w not in out]

    if nested and "KVM" in missing:
        missing = [m for m in missing if m != "KVM"]
        rep.amber("cell substrate: /dev/kvm",
                  "absent because this run is nested in QEMU -- not an image defect; "
                  "cells cannot be exercised in this mode", g)

    enabled = "CELLS_ENABLED" in out
    if missing:

        rep.red("cell substrate present",
                "%smissing: %s" % ("CELLS_ENABLED=1 but " if enabled else "", ",".join(missing)),
                g, {"probe": out})
    else:
        rep.green("cell substrate present", "kvm+pn-vmm+kernel+rootfs+tmux all present", g)

def check_session_lifecycle(rep, portal, caps, hold_seconds):
    g = "sessions"
    title = "acceptance-%d" % int(time.time())
    sid = None
    r = portal.post("/api/sessions/new", {"title": title, "kind": "cockpit"}, ctype="json", timeout=60)
    j = r.json() or {}
    if r.status >= 400 or not j.get("ok"):
        rep.red("session create", "HTTP %d %s" % (r.status, r.text[:70]), g)
        return None
    sid = j.get("id") or (j.get("session") or {}).get("id")
    if not sid:
        rep.red("session create", "ok but no session id returned", g)
        return None
    rep.green("session create", "sid=%s" % sid, g)

    try:

        board = (portal.get("/api/session/board").json() or {}).get("sessions") or []
        mine = [s for s in board if s.get("sid") == sid]
        if mine:
            st = mine[0]
            rep.green("session on board", "state=%s runtime=%s" % (st.get("state"), st.get("runtime")), g)
        else:
            rep.red("session on board", "created session absent from /api/session/board", g)

        if caps.get("cockpit") is False:
            rep.red("session kind is actually usable",
                    "created kind=cockpit while caps.cockpit=false -- user gets a session "
                    "that can never attach a terminal", g)
        else:
            rep.green("session kind is actually usable", "caps.cockpit=true", g)

        url = portal.ws_url("/ws/term") + "?sid=" + sid
        hs = wsclient.handshake(url, cookie=portal.cookie_header, origin=portal.target)
        if not hs.ok:
            rep.red("terminal attaches (/ws/term)",
                    "pre-handshake %s" % hs.describe(), g, {"prehandshake": hs.status})
        else:
            rep.green("terminal attaches (/ws/term)", "101 upgraded", g)
            stayed, frames, why = wsclient.hold(hs, hold_seconds)
            hs.close()
            if stayed:
                rep.green("terminal survives %ds" % hold_seconds,
                          "held, %d frames" % frames, g)
            else:
                rep.red("terminal survives %ds" % hold_seconds, why, g)
    finally:

        t_aus = time.time()
        r = portal.post("/api/session/power", {"sid": sid, "on": False}, ctype="json", timeout=240)
        d_aus = time.time() - t_aus
        j = r.json() or {}
        if r.status < 400 and j.get("ok"):
            if d_aus > 30:
                rep.amber("Sitzung laesst sich ausschalten",
                          "ok, aber %.0f s ohne Rueckmeldung (Cockpit-Knopf wartet blind)" % d_aus, g)
            else:
                rep.green("Sitzung laesst sich ausschalten",
                          "power off ok nach %.1f s (wieder einschaltbar)" % d_aus, g)
        else:
            rep.red("Sitzung laesst sich ausschalten",
                    "nach %.0f s: HTTP %d %s" % (d_aus, r.status, r.text[:60]), g)

        rk = portal.post("/api/session/kill", {"sid": sid}, ctype="json", timeout=60)
        jk = rk.json() or {}
        if rk.status == 403 and not jk.get("ok"):
            rep.green("Loeschen ist ohne zweiten Faktor gesperrt",
                      "HTTP 403 %s" % (jk.get("error") or "")[:60], g)
        elif rk.status < 400 and jk.get("ok"):
            rep.red("Loeschen ist ohne zweiten Faktor gesperrt",
                    "die Sitzung wurde OHNE 2FA geloescht", g)
        else:
            rep.amber("Loeschen ist ohne zweiten Faktor gesperrt",
                     "unerwartete Antwort HTTP %d %s" % (rk.status, rk.text[:60]), g)
    return sid

def _surface(rep, portal, group, name, path, honest_when_disabled=True):
    r = portal.get(path, timeout=20)
    if r.status == 0:

        rep.red(name, "%s unreachable: %s" % (path, r.text[:70]), group)
        return None
    if r.has_traceback:
        rep.red(name, "traceback from %s" % path, group)
        return None
    if r.status == 404:
        rep.red(name, "%s -> 404 (surface absent)" % path, group)
        return None
    if r.status >= 500:
        rep.red(name, "%s -> HTTP %d" % (path, r.status), group)
        return None
    j = r.json()
    if isinstance(j, dict) and j.get("ok") is False:
        msg = j.get("error") or j.get("reason") or j.get("msg") or ""
        if honest_when_disabled and msg:
            rep.amber(name, "unavailable, honest reason: %s" % str(msg)[:70], group)
        else:
            rep.red(name, "%s -> ok:false with no reason given" % path, group)
        return j
    rep.green(name, "%s -> HTTP %d" % (path, r.status), group)
    return j

SEAT_PROCS = "pgrep -ax 'Xvfb|x11vnc|rfbd|phantom|Xvnc' 2>/dev/null | head -5"

def check_screen(rep, portal, caps, guest=None):
    g = "screen/seat"
    j = _surface(rep, portal, g, "screen surface", "/api/screen/apps")
    if caps.get("screen"):
        r = portal.post("/api/screen/start", {}, ctype="json", timeout=45)
        jj = r.json() or {}
        if r.status == 0:
            rep.red("screen starts", "unreachable: %s" % r.text[:60], g)
        elif r.has_traceback or r.status >= 500:
            rep.red("screen starts", "HTTP %d / traceback" % r.status, g)
        elif jj.get("ok"):

            if guest and guest.enabled:
                time.sleep(3)
                procs = (guest.run(SEAT_PROCS) or "").strip()
                if procs:
                    rep.green("screen starts", "seat started, process live: %s"
                              % procs.splitlines()[0][:50], g)
                else:
                    rep.red("screen starts",
                            "/api/screen/start returned ok:true but NO seat process "
                            "(Xvfb/x11vnc/rfbd/phantom) is running", g)
            else:
                rep.amber("screen starts", "ok:true (unverified -- no in-guest access)", g)
            portal.post("/api/screen/stop", {}, ctype="json", timeout=30)
        else:
            rep.red("screen starts", "ok:false -- %s" % str(jj.get("error") or r.text)[:70], g)
    else:

        rep.amber("screen starts", "caps.screen=false -- Screen must be visibly disabled in the UI", g)

def check_claude_signin(rep, portal):
    g = "claude sign-in"
    r = portal.get("/api/admin/llm/oauth/status", timeout=20)
    if r.status == 0:
        rep.red("claude sign-in status", "unreachable: %s" % r.text[:60], g)
    elif r.status == 404:
        rep.red("claude sign-in status", "/api/admin/llm/oauth/status -> 404 (sign-in path absent)", g)
    elif r.has_traceback or r.status >= 500:
        rep.red("claude sign-in status", "HTTP %d / traceback" % r.status, g)
    else:
        j = r.json() or {}
        rep.green("claude sign-in status", "HTTP %d %s" % (r.status, json.dumps(j)[:60]), g)

    r2 = portal.get("/api/admin/llm/pool", timeout=20)
    if r2.status == 404:
        rep.red("LLM pool surface", "/api/admin/llm/pool -> 404", g)
    elif r2.has_traceback or r2.status >= 500:
        rep.red("LLM pool surface", "HTTP %d" % r2.status, g)
    else:
        j = r2.json() or {}
        brains = j.get("brains") or j.get("pool") or j.get("entries") or []
        n = len(brains) if isinstance(brains, list) else 0
        if n:
            rep.green("LLM pool surface", "%d brain(s) configured" % n, g)
        else:
            rep.amber("LLM pool surface", "no brains configured (sign-in not completed)", g)

def check_queue(rep, portal):
    g = "queue/jobs"
    _surface(rep, portal, g, "jobs surface", "/api/jobs")
    _surface(rep, portal, g, "queue surface", "/api/queue")

def check_admin(rep, portal):
    g = "admin"
    _surface(rep, portal, g, "admin overview", "/api/admin/overview")
    _surface(rep, portal, g, "admin users", "/api/admin/users")
    _surface(rep, portal, g, "admin RAM ledger", "/api/admin/ram")
    _surface(rep, portal, g, "admin statistics", "/api/admin/stats")

def check_vault(rep, portal):
    g = "vault/tresor"
    _surface(rep, portal, g, "vault blob surface", "/api/vault/blob")
    _surface(rep, portal, g, "keys surface", "/api/keys")

PNCTL = """
(pnctl list 2>/dev/null || /usr/local/bin/pnctl list 2>/dev/null) | sed 's/[[:space:]]\\+/ /g'
"""

RESPAWN = r"""
tail -400 /var/log/pn/pn-init.log 2>/dev/null |
  grep -oE '\[pn-init\] [A-Za-z0-9_-]+ exited' | awk '{print $2}' | sort | uniq -c | sort -rn | head -8
"""

def check_services(rep, guest, expect_down=("avahi", "banner")):
    g = "service health"
    if not (guest and guest.enabled):
        rep.skip("pn-init services up", "no in-guest access", g)
        rep.skip("no service respawn loop", "no in-guest access", g)
        return
    out = guest.run(PNCTL)
    if not out:
        rep.red("pn-init services up", "pnctl list produced nothing", g)
        return
    down = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].upper() == "DOWN":
            down.append(parts[0])

    lying = []
    if down:
        names = " ".join("'%s'" % d for d in down)
        probe = "for s in %s; do pgrep -x \"$s\" >/dev/null 2>&1 && echo \"ALIVE:$s\"; done" % names
        alive = guest.run(probe) or ""
        lying = [d for d in down if ("ALIVE:" + d) in alive]

    really_down = [d for d in down if d not in lying]
    critical = [d for d in really_down if d not in expect_down]

    if lying:
        rep.red("pn-init service state is truthful",
                "pnctl reports DOWN but the process IS running: %s" % ", ".join(lying),
                g, {"misreported": lying})
    else:
        rep.green("pn-init service state is truthful", "no misreported services", g)

    if critical:
        rep.red("pn-init services up", "DOWN: %s" % ", ".join(critical), g, {"down": really_down})
    elif really_down:
        rep.amber("pn-init services up", "DOWN (tolerated): %s" % ", ".join(really_down),
                  g, {"down": really_down})
    else:
        rep.green("pn-init services up", "all services RUNNING", g)

    uptimes = {}
    for line in out.splitlines():
        m = re.match(r"(\S+)\s+RUNNING\s+\d+\s+\(up\s+([^)]+)\)", line.strip())
        if m:
            uptimes[m.group(1)] = _parse_uptime(m.group(2))

    resp = guest.run(RESPAWN) or ""
    loops = []
    for line in resp.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            n = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        up = uptimes.get(name)

        if n >= 5 and (up is None or up < 180):
            loops.append("%s x%d (uptime %s)" % (name, n, "DOWN" if up is None else "%ds" % up))
    if loops:
        rep.red("no service respawn loop",
                "restarting in a loop: %s" % ", ".join(loops), g, {"loops": loops})
    else:
        rep.green("no service respawn loop",
                  "no service both restarting often and short-lived", g)

def _parse_uptime(s):

    total = 0
    for val, unit in re.findall(r"(\d+)([dhms])", s):
        total += int(val) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total

PORTAL_LOG = "$HOME/.local/share/brainbox-portal/portal.console.log"

def portal_log_marker(guest):
    if not (guest and guest.enabled):
        return None
    out = guest.run("wc -l < %s 2>/dev/null || echo 0" % PORTAL_LOG)
    try:
        return int((out or "0").strip().split()[0])
    except Exception:
        return 0

DISCONNECT_EXC = re.compile(
    r"^(ConnectionResetError|BrokenPipeError|ssl\.SSLEOFError|ssl\.SSLError|"
    r"TimeoutError|socket\.timeout|ConnectionAbortedError)\b"
)

def check_still_up(rep, portal):

    g = "service health"
    for attempt in range(3):
        r = portal.get("/api/status", timeout=10)
        if r.status not in (0,):
            rep.green("portal still serving at end of run", "HTTP %d" % r.status, g)
            return
        time.sleep(3)
    rep.red("portal still serving at end of run",
            "portal unreachable after the suite -- it died or is respawning", g)

def check_portal_log(rep, guest, marker):

    g = "service health"
    if not (guest and guest.enabled) or marker is None:
        rep.skip("no portal traceback during run", "no in-guest access", g)
        return
    out = guest.run(
        "tail -n +%d %s 2>/dev/null | grep -E '^[A-Za-z_.]+(Error|Exception)' | head -20"
        % (marker + 1, PORTAL_LOG)
    )
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    handler_bugs = [l for l in lines if not DISCONNECT_EXC.match(l)]
    disconnects = [l for l in lines if DISCONNECT_EXC.match(l)]

    if handler_bugs:
        rep.red("no portal traceback during run",
                "%d handler exception(s): %s" % (len(handler_bugs), handler_bugs[0][:70]),
                g, {"handler": handler_bugs, "disconnects": disconnects})
    else:
        rep.green("no portal traceback during run",
                  "no handler exception since run start", g)

    if disconnects:

        rep.amber("portal logs client disconnects quietly",
                  "%d full traceback(s) from peer hangups (log noise that masks real errors)"
                  % len(disconnects), g, {"disconnects": disconnects[:6]})
