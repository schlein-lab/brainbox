#!/usr/bin/env python3

import os, sys, time

sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))
import pn_cell_session as cs

PRIN, SID, CID = "deskproof", "t1", 223
SHOT_GZ = "/tmp/desk-shot.xwd.gz"
SHOT_PNG = "/tmp/desk-shot.png"

ok = True

def ck(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, extra), flush=True)

policy = {"desktop": True, "net_general": "deny", "net_internal": "deny", "net_hosts": []}
cell = cs.CellSession(PRIN, SID, CID, policy=policy)

print("== gate 1: boot (office profile, no net) ==", flush=True)
t0 = time.time()
booted = cell.boot()
ck("office cell boots", booted, "(%.1fs)" % (time.time() - t0))
if not booted:
    print("boot_denied:", cell._boot_denied)
    sys.exit(1)

print("== gate 1b: start the REPL (same lane the board terminal uses) ==", flush=True)
t0 = time.time()
started = cell.start_terminal()
ck("start_terminal (tmux 'repl' + claude)", bool(started),
   "(%.1fs) %s" % (time.time() - t0, getattr(cell, "_term_denied", "") or ""))

print("== gate 2: desktop_stage ==", flush=True)
t0 = time.time()
reason = cell.desktop_stage()
ck("desktop_stage", reason is None, "(%.1fs) %s" % (time.time() - t0, reason or ""))

print("== gate 3: Innenausbau alive in-cell ==", flush=True)
time.sleep(6)
okc, out = cell._run(
    "echo TINT2=$(pidof tint2) XTERM=$(pidof xterm); "
    "busybox cat /tmp/wallpaper.log 2>/dev/null | busybox head -2; "
    "tmux has-session -t repl 2>/dev/null && echo REPL_SESSION=yes || echo REPL_SESSION=no; "
    "tmux list-clients -t repl 2>/dev/null | busybox wc -l; echo __G3__", "__G3__", 20)
o = out or ""
ck("tint2 running", okc and "TINT2=" in o and o.split("TINT2=", 1)[1].strip().split()[0].isdigit(), o.strip()[:120])
ck("xterm running", "XTERM=" in o and o.split("XTERM=", 1)[1].strip().split()[0].isdigit())
ck("wallpaper set (no error)", "error" not in o.lower() and "cannot" not in o.lower())
ck("tmux repl session exists", "REPL_SESSION=yes" in o)
lines = [l.strip() for l in o.splitlines() if l.strip().isdigit()]
ck("desk-term attached as tmux client", lines and int(lines[-1]) >= 1, "clients=%s" % (lines[-1] if lines else "?"))

print("== gate 3b: Anwendungsmenue (jgmenu) + Panel-Launcher im Image ==", flush=True)
okc, out = cell._run(
    "jgmenu --version 2>&1 | busybox head -1; "
    "echo LAUNCHER_DESKTOP=$([ -f /opt/pn/anwendungen.desktop ] && echo yes || echo no); "
    "echo MENU_SH=$([ -x /opt/pn/menu.sh ] && echo yes || echo no); "
    "echo JGMENURC=$([ -f /etc/xdg/jgmenu/jgmenurc ] && echo yes || echo no); "
    "echo APPSICON=$([ -s /opt/pn/apps-icon.png ] && echo yes || echo no); "
    "busybox grep -q launcher_item_app /opt/pn/tint2rc && echo PANEL_LAUNCHER=yes || echo PANEL_LAUNCHER=no; "
    "echo __G3B__", "__G3B__", 20)
o2 = out or ""
_jv = ""
for _l in o2.splitlines():
    if "jgmenu" in _l:
        _jv = _l.strip(); break
ck("jgmenu laeuft in der Zelle", okc and "jgmenu" in o2, _jv)
ck("Panel-Launcher .desktop vorhanden", "LAUNCHER_DESKTOP=yes" in o2)
ck("menu.sh ausfuehrbar", "MENU_SH=yes" in o2)
ck("jgmenurc vorhanden", "JGMENURC=yes" in o2)
ck("Launcher-Icon vorhanden", "APPSICON=yes" in o2)
ck("tint2-Panel hat Launcher-Button", "PANEL_LAUNCHER=yes" in o2)

print("== gate 4: screenshot evidence (xwd -> gzip -> host png) ==", flush=True)
okc, out = cell._run("DISPLAY=:7 xwd -root -silent 2>/tmp/xwd.err | busybox gzip -9 | base64 -w0; "
                     "echo; echo __G4__", "__G4__", 60)
b64 = ""
if okc and out:
    cand = [l.strip() for l in out.splitlines() if len(l.strip()) > 1000]
    b64 = cand[-1] if cand else ""
ck("screenshot pulled", bool(b64), "(%d b64 chars)" % len(b64))
if b64:
    import base64 as _b64, gzip as _gz, struct
    raw = _gz.decompress(_b64.b64decode(b64))
    hdr = struct.unpack(">25I", raw[:100])
    hsz, w, h, bpl, ncol = hdr[0], hdr[4], hdr[5], hdr[12], hdr[19]
    off = hsz + ncol * 12
    from PIL import Image

    img = Image.frombytes("RGB", (w, h), raw[off:off + bpl * h], "raw", "BGRX", bpl, 1)
    img.save(SHOT_PNG)
    ck("png written", os.path.getsize(SHOT_PNG) > 1000, "%s (%dx%d)" % (SHOT_PNG, w, h))

print("== gate 5: geregelter shutdown — GUI down, REPL survives ==", flush=True)
okc, out = cell._run("/opt/pn/gui-down.sh; busybox sleep 1; "
                     "echo XVFB=$(pidof Xvfb) TINT2=$(pidof tint2) XTERM=$(pidof xterm); "
                     "tmux has-session -t repl 2>/dev/null && echo REPL_AFTER=yes || echo REPL_AFTER=no; "
                     "echo __G5__", "__G5__", 30)
o = out or ""
import re as _re
m = _re.search(r"XVFB=(\S*) TINT2=(\S*) XTERM=(\S*)", o)
ck("X stack gone", bool(m) and not any(g.isdigit() for g in m.groups()), o.strip()[:140])
ck("tmux repl SURVIVES gui-down", "REPL_AFTER=yes" in o)

print("== gate 6: unfold again (gui-up idempotent cycle) ==", flush=True)
okc, out = cell._run("/opt/pn/gui-up.sh 2>&1 | busybox tail -1; echo __G6__", "__G6__", 60)
ck("second gui-up", okc and "GUI_UP_OK" in (out or ""), (out or "").strip()[:140])

print("== teardown + erase (disposable proof cell) ==", flush=True)
try:
    cell._teardown(reboot=False)
    cell._erase_state()
    ck("teardown+erase", True)
except Exception as e:
    ck("teardown+erase", False, repr(e))

print("RESULT:", "ALL GREEN" if ok else "RED")
sys.exit(0 if ok else 1)
