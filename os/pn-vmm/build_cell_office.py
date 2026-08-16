#!/usr/bin/env python3

import os, shutil, subprocess, glob, sys

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
SESS = "kernel/_ownersession"; OFFICE = "kernel/_office"

IMG = os.environ.get("PN_OFFICE_IMG", "kernel/base-office.img"); SIZE = "1280M"

MULTI = "/usr/lib/%s-linux-gnu" % os.uname().machine
BINS = ["/usr/bin/Xvfb", "/usr/bin/x11vnc", "/usr/bin/xkbcomp", "/usr/bin/setxkbmap",
        "/usr/bin/openbox", "/usr/bin/xterm", "/usr/bin/flatpak", "/usr/bin/bwrap",
        "/usr/bin/xdg-dbus-proxy", "/usr/bin/gpg", "/usr/bin/gpgconf", "/usr/bin/glxinfo",

        "/usr/bin/xwallpaper", "/usr/bin/tint2", "/usr/bin/xwd",

        "/usr/bin/jgmenu", "/usr/bin/jgmenu_run"]

SEED_LIBS = [f"{MULTI}/libGLX_mesa.so.0", f"{MULTI}/libEGL.so.1", f"{MULTI}/libEGL_mesa.so.0"]
SEED_LIBS += sorted(glob.glob(f"{MULTI}/libgallium-*.so"))

TREES = [
    ("/usr/share/X11/xkb", "usr/share/X11/xkb"),
    ("/usr/share/X11/locale", "usr/share/X11/locale"),
    ("/etc/fonts", "etc/fonts"),
    ("/usr/share/fonts/truetype/dejavu", "usr/share/fonts/truetype/dejavu"),
    ("/usr/share/fonts/X11/misc", "usr/share/fonts/X11/misc"),

    ("/etc/xdg/openbox", "etc/xdg/openbox"),
    ("/usr/share/themes/Clearlooks", "usr/share/themes/Clearlooks"),
    ("/usr/share/themes/Onyx", "usr/share/themes/Onyx"),
    ("/etc/X11/app-defaults", "etc/X11/app-defaults"),
    (f"{MULTI}/gdk-pixbuf-2.0", MULTI.lstrip("/") + "/gdk-pixbuf-2.0"),
    (f"{MULTI}/imlib2", MULTI.lstrip("/") + "/imlib2"),
    (f"{MULTI}/gio/modules", MULTI.lstrip("/") + "/gio/modules"),
    (f"{MULTI}/dri", MULTI.lstrip("/") + "/dri"),
    ("/usr/share/glvnd", "usr/share/glvnd"),
    ("/usr/share/flatpak", "usr/share/flatpak"),
    ("/usr/lib/jgmenu", "usr/lib/jgmenu"),
]
FILES = [
    ("/usr/share/glib-2.0/schemas/gschemas.compiled", "usr/share/glib-2.0/schemas/gschemas.compiled"),
    ("/etc/ssl/certs/ca-certificates.crt", "etc/ssl/certs/ca-certificates.crt"),
]

NOTO_DIR = "/usr/share/fonts/truetype/noto"
NOTO_BASICS = ["NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf",
               "NotoSerif-Regular.ttf", "NotoSerif-Bold.ttf",
               "NotoSansMono-Regular.ttf", "NotoSansMono-Bold.ttf", "NotoMono-Regular.ttf",
               "NotoSansSymbols-Regular.ttf", "NotoSansSymbols2-Regular.ttf", "NotoColorEmoji.ttf"]

RFB_ADAPTER = r'''#!/bin/python3
# rfb_vsock_adapter — splices x11vnc (127.0.0.1:5901) with the host GUI/RFB lane (AF_VSOCK CID2).
# In-cell connects ACTIVELY OUT (cell-rfb-lane pattern); reconnect loop like cell_gui_app.
# The vsock port is CONFIG (PN_RFB_VSOCK_PORT / /etc/pn/rfb-port), DEFAULT 9500: in a SESSION cell
# the 5900 lane already carries the portal channel (pn_cell_session reuses PN_VMM_VSOCK_RFB for the
# portal broker), so the office desktop rides its own 7th lane (host: PN_VMM_VSOCK_GUI). A base-x11
# style host that bridges 5900 just sets PN_RFB_VSOCK_PORT=5900 — no image rebuild.
import os, socket, threading, time, sys

def _port():
    p = os.environ.get("PN_RFB_VSOCK_PORT", "")
    if not p:
        try: p = open("/etc/pn/rfb-port").read().strip()
        except OSError: p = ""
    try: return int(p or 9500)
    except ValueError: return 9500

PORT = _port()

def log(m): print("[rfbadapt] %s" % m, flush=True)

def splice(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except OSError: pass
    for s in (a, b):
        try: s.shutdown(socket.SHUT_RDWR)
        except OSError: pass

while True:
    try:
        v = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        v.connect((2, PORT))
        log("vsock lane up (port %d)" % PORT)
        t = socket.create_connection(("127.0.0.1", 5901), timeout=15)
        log("x11vnc connected; splicing")
        th = threading.Thread(target=splice, args=(v, t), daemon=True); th.start()
        splice(t, v); th.join(timeout=3)
        log("lane closed")
    except OSError as e:
        log("retry in 2s (%r)" % (e,))
    for s in ("v", "t"):
        try: locals()[s].close()
        except Exception: pass
    time.sleep(2)
'''

GUI_UP = r'''#!/bin/bash
# gui-up.sh — office GUI phase ON (on-demand; counterpart gui-down.sh). Idempotent (pid-file based).
# Resolution + keyboard layout are CONFIG, not code: PN_GUI_RES / /etc/pn/gui-res (WxHxD) and
# PN_KBD_LAYOUT / /etc/pn/kbd-layout (xkb layout; shipped default de) — layout-aware appliance.
export DISPLAY=:7 HOME=/root FONTCONFIG_PATH=/etc/fonts
# Boxzeit: desktop_stage passes a POSIX TZ (no tzdata in the cell). Persist it so a manual or
# menu-triggered gui-up (no env) keeps the house clock instead of falling back to UTC.
[ -n "${TZ:-}" ] && echo "$TZ" > /etc/pn/tz 2>/dev/null
export TZ="${TZ:-$(cat /etc/pn/tz 2>/dev/null)}"
export XKB_CONFIG_ROOT=/usr/share/X11/xkb XKB_BINDIR=/usr/bin
export XDG_RUNTIME_DIR=/tmp/xdg XDG_CONFIG_DIRS=/etc/xdg
export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/work/flatpak/exports/share:/usr/share}"
# /work itself is mounted HOST-side over the seat BEFORE gui-up runs (contract) — never mount here,
# only make sure the flatpak user dir exists on it.
export FLATPAK_USER_DIR="${FLATPAK_USER_DIR:-/work/flatpak}"
mkdir -p "$FLATPAK_USER_DIR" 2>/dev/null
mkdir -p -m0700 /tmp/xdg
# loopback MUST be up, else the adapter cannot reach x11vnc on 127.0.0.1 (Network unreachable).
ip link set lo up 2>/dev/null || ifconfig lo up 2>/dev/null || busybox ip link set lo up 2>/dev/null || true
# minimal cell rootfs ships no devpts -> xterm dies "get_pty: not enough ptys" (same lesson as
# pn_repl_launch.sh, which mounts it for tmux). Idempotent.
busybox mkdir -p /dev/pts
busybox mount -t devpts -o mode=0620,ptmxmode=0666 devpts /dev/pts 2>/dev/null
[ -e /dev/pts/ptmx ] && busybox ln -sf /dev/pts/ptmx /dev/ptmx 2>/dev/null
mkdir -p -m1777 /tmp/.X11-unix
RES="${PN_GUI_RES:-$(cat /etc/pn/gui-res 2>/dev/null)}"; RES="${RES:-1280x800x24}"
KBD="${PN_KBD_LAYOUT:-$(cat /etc/pn/kbd-layout 2>/dev/null)}"; KBD="${KBD:-de}"
[ -f /tmp/xvfb.pid ] && kill -0 "$(cat /tmp/xvfb.pid)" 2>/dev/null || { Xvfb :7 -screen 0 "$RES" -ac -nolisten tcp -noreset >/tmp/xvfb.log 2>&1 & echo $! > /tmp/xvfb.pid; }
for i in $(seq 1 60); do [ -S /tmp/.X11-unix/X7 ] && break; sleep 0.2; done
setxkbmap -layout "$KBD" >/tmp/setxkbmap.log 2>&1 || true
[ -f /tmp/openbox.pid ] && kill -0 "$(cat /tmp/openbox.pid)" 2>/dev/null || { openbox >/tmp/openbox.log 2>&1 & echo $! > /tmp/openbox.pid; }
# Innenausbau — the desktop is never an empty black root:
#   wallpaper (xwallpaper sets the root pixmap and EXITS — no daemon, re-run is idempotent)
#   tint2 panel (taskbar + clock, config baked at /opt/pn/tint2rc)
#   the Claude terminal: attaches the SAME tmux REPL the board terminal uses (desk-term.sh) —
#   closing the GUI later only detaches this client, the REPL itself keeps running.
xwallpaper --zoom /opt/pn/wallpaper.png >/tmp/wallpaper.log 2>&1 || true
[ -f /tmp/tint2.pid ] && kill -0 "$(cat /tmp/tint2.pid)" 2>/dev/null || { tint2 -c /opt/pn/tint2rc >/tmp/tint2.log 2>&1 & echo $! > /tmp/tint2.pid; }
pidof xterm >/dev/null 2>&1 || { xterm -fa 'DejaVu Sans Mono' -fs 11 -bg '#0d0d16' -fg '#e7e7f2' -geometry 108x28+56+40 -T 'Brainbox' -e /opt/pn/desk-term.sh >/tmp/xterm.log 2>&1 & }
# -xkb -add_keysyms -repeat: without these x11vnc cannot map viewer keysyms to keycodes (celltv lesson).
[ -f /tmp/x11vnc.pid ] && kill -0 "$(cat /tmp/x11vnc.pid)" 2>/dev/null || { x11vnc -display :7 -rfbport 5901 -localhost -auth /dev/null -nopw -forever -shared -noxdamage -xkb -add_keysyms -repeat -quiet >/tmp/x11vnc.log 2>&1 & echo $! > /tmp/x11vnc.pid; }
for i in $(seq 1 40); do { echo > /dev/tcp/127.0.0.1/5901; } 2>/dev/null && break; sleep 0.2; done
[ -f /tmp/rfbadapt.pid ] && kill -0 "$(cat /tmp/rfbadapt.pid)" 2>/dev/null || { /bin/python3 /opt/pn/rfb_vsock_adapter.py >/tmp/rfbadapt.log 2>&1 & echo $! > /tmp/rfbadapt.pid; }
sleep 1
# host desktop_stage() greps /tmp/gui-up.log for GUI_UP_OK — emit to BOTH stdout and the log.
MSG="GUI_UP_OK display=:7 res=$RES kbd=$KBD xvfb=$(cat /tmp/xvfb.pid 2>/dev/null) openbox=$(cat /tmp/openbox.pid 2>/dev/null) x11vnc=$(cat /tmp/x11vnc.pid 2>/dev/null) tint2=$(cat /tmp/tint2.pid 2>/dev/null)"
echo "$MSG"; echo "$MSG" >> /tmp/gui-up.log
'''

GUI_DOWN = r'''#!/bin/bash
# gui-down.sh — office GUI phase OFF ("as soon as not needed, off again"). Apps first, then the
# stack. busybox-safe: pid files for the GUI stack, pidof (matches comm) for the apps.
# xterm first: killing it only DETACHES the tmux client — the REPL session inside keeps running
# (that is the geregelte GUI->Terminal handover: nothing in the session is lost).
for a in xterm tint2; do kill $(pidof "$a" 2>/dev/null) 2>/dev/null; done
rm -f /tmp/tint2.pid
sleep 0.3
for f in rfbadapt x11vnc openbox; do
  p=$(cat /tmp/$f.pid 2>/dev/null); [ -n "$p" ] && kill "$p" 2>/dev/null; rm -f /tmp/$f.pid
done
sleep 0.3
p=$(cat /tmp/xvfb.pid 2>/dev/null); [ -n "$p" ] && kill "$p" 2>/dev/null; rm -f /tmp/xvfb.pid
sleep 0.3
echo "GUI_DOWN_OK xvfb_left=$(pidof Xvfb 2>/dev/null) x11vnc_left=$(pidof x11vnc 2>/dev/null) openbox_left=$(pidof openbox 2>/dev/null)"
'''

DESK_TERM = r'''#!/bin/bash
# desk-term.sh — the desktop terminal IS the session REPL: it attaches the SAME tmux session
# ("repl") the board terminal uses. It never CREATES the session — pn_repl_launch.sh owns creation
# (incl. pn-gate exec governance); creating it here would spawn an ungoverned bare shell.
# Killing this xterm (gui-down) only detaches a tmux CLIENT — the REPL keeps running untouched.
export HOME=/root TERM=xterm-256color
i=0
until tmux has-session -t repl 2>/dev/null; do
  i=$((i+1)); [ $i -eq 1 ] && echo "warte auf die Claude-Sitzung (tmux 'repl') …"
  [ $i -gt 120 ] && { echo "keine REPL-Sitzung gefunden — Board-Terminal schon gestartet?"; exec bash; }
  sleep 1
done
exec tmux -u attach-session -t repl
'''

TINT2RC = '''# tint2rc — Brainbox office panel: schlank + dunkel, Launcher + Taskbar + Uhr.
# Farbwelt = Portal (webapp/app.css). Der Launcher links oeffnet das Anwendungsmenue (jgmenu).
# Background 1: panel
rounded = 0
border_width = 0
background_color = #141422 100
# Background 2: active task
rounded = 4
border_width = 1
border_color = #6b7cff 45
background_color = #26264a 100

panel_items = LTC
panel_size = 100% 30
panel_position = bottom center horizontal
panel_background_id = 1
panel_padding = 8 2 8
wm_menu = 1

# Launcher: sichtbarer Knopf links -> Anwendungsmenue (jgmenu: XDG-Apps + Claude-Terminal).
launcher_padding = 6 4 6
launcher_background_id = 0
launcher_icon_background_id = 0
launcher_icon_size = 22
launcher_icon_asb = 100 0 0
launcher_tooltip = 1
launcher_item_app = /opt/pn/anwendungen.desktop

taskbar_mode = single_desktop
taskbar_padding = 4 2 4
taskbar_background_id = 0

task_text = 1
task_icon = 0
task_maximum_size = 220 26
task_padding = 8 2
task_font = DejaVu Sans 10
task_font_color = #a9a9c4 100
task_active_font_color = #e7e7f2 100
task_background_id = 0
task_active_background_id = 2

time1_format = %H:%M
time1_font = DejaVu Sans Bold 10
clock_font_color = #a9a9c4 100
clock_padding = 10 0
clock_background_id = 0
'''

MENU_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<openbox_menu xmlns="http://openbox.org/3.4/menu">
<menu id="root-menu" label="Office">
  <item label="Claude-Terminal">
    <action name="Execute"><command>xterm -fa 'DejaVu Sans Mono' -fs 11 -bg '#0d0d16' -fg '#e7e7f2' -T 'Brainbox' -e /opt/pn/desk-term.sh</command></action>
  </item>
  <item label="Terminal (xterm)">
    <action name="Execute"><command>xterm -fa 'DejaVu Sans Mono' -fs 11</command></action>
  </item>
  <item label="Reconfigure"><action name="Reconfigure"/></item>
  <item label="Exit GUI"><action name="Execute"><command>/opt/pn/gui-down.sh</command></action></item>
</menu>
</openbox_menu>
'''

MENU_SH = r"""#!/bin/sh
export JGMENU_EXEC_DIR=/usr/lib/jgmenu
export PATH="/usr/lib/jgmenu:/usr/bin:/bin${PATH:+:$PATH}"
CT="xterm -fa 'DejaVu Sans Mono' -fs 11 -bg '#0d0d16' -fg '#e7e7f2' -T Brainbox -e /opt/pn/desk-term.sh"
{ printf 'Claude-Terminal,%s\n^sep(Anwendungen)\n' "$CT"; jgmenu_run apps 2>/dev/null; } \
  | jgmenu --simple --at-pointer --icon-size=0 --config-file=/etc/xdg/jgmenu/jgmenurc
"""

ANWENDUNGEN_DESKTOP = """[Desktop Entry]
Type=Application
Name=Anwendungen
Comment=Installierte Programme oeffnen
Exec=/opt/pn/menu.sh
Icon=/opt/pn/apps-icon.png
Terminal=false
Categories=System;
"""

JGMENURC = """# jgmenurc — Brainbox-Anwendungsmenue, Portal-Farbwelt.
stay_alive = 0
tint2_look = 0
icon_size = 0
menu_width = 230
menu_padding_top = 4
menu_padding_right = 4
menu_padding_bottom = 4
menu_padding_left = 4
menu_radius = 6
menu_border = 1
item_height = 26
item_padding_x = 8
item_radius = 4
font = DejaVu Sans 10
color_menu_bg = #141422 100
color_menu_border = #6b7cff 45
color_norm_bg = #141422 0
color_norm_fg = #e7e7f2 100
color_sel_bg = #26264a 100
color_sel_fg = #ffffff 100
color_sep_fg = #6b7cff 70
color_title_fg = #a9a9c4 100
"""

INIT_EXPORTS = ("export FLATPAK_USER_DIR=/work/flatpak\n"
                "export FLATPAK_SYSTEM_DIR=/work/flatpak\n"
                "export XDG_DATA_DIRS=/work/flatpak/exports/share:/usr/share\n")
INIT_WORKDIR = 'busybox mkdir -p /work/flatpak 2>/dev/null\n'

def ldd_deps(path):
    try:
        out = subprocess.run(["ldd", path], capture_output=True, text=True).stdout
    except Exception:
        return []
    deps = []
    for line in out.splitlines():
        if "=>" in line:
            p = line.split("=>", 1)[1].strip().split(" ")[0]
            if p.startswith("/") and os.path.exists(p):
                deps.append(p)
    return deps

def main():
    assert os.path.isdir(SESS), "need kernel/_ownersession -> run build_cell_owner_session.py first"
    if os.path.exists(OFFICE):
        shutil.rmtree(OFFICE)
    shutil.copytree(SESS, OFFICE, symlinks=True)

    os.makedirs(f"{OFFICE}/usr/bin", exist_ok=True)
    for b in BINS:
        if not os.path.exists(b):
            print("SKIP missing bin:", b); continue
        base = os.path.basename(b)
        shutil.copy(b, f"{OFFICE}/bin/{base}"); os.chmod(f"{OFFICE}/bin/{base}", 0o755)

        shutil.copy(b, f"{OFFICE}/usr/bin/{base}"); os.chmod(f"{OFFICE}/usr/bin/{base}", 0o755)

    for src, rel in TREES:
        if not os.path.isdir(src):
            print("SKIP missing tree:", src); continue
        dst = f"{OFFICE}/{rel}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    for src, rel in FILES:
        dst = f"{OFFICE}/{rel}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(os.path.realpath(src), dst)
    for name in NOTO_BASICS:
        p = os.path.join(NOTO_DIR, name)
        if os.path.exists(p):
            os.makedirs(f"{OFFICE}/usr/share/fonts/truetype/noto", exist_ok=True)
            shutil.copy(p, f"{OFFICE}/usr/share/fonts/truetype/noto/{name}")

    os.makedirs(f"{OFFICE}/usr/libexec", exist_ok=True)
    libexec = sorted(glob.glob("/usr/libexec/flatpak*") + glob.glob("/usr/libexec/revokefs-fuse"))
    for p in libexec:
        shutil.copy(p, f"{OFFICE}/usr/libexec/{os.path.basename(p)}")
        os.chmod(f"{OFFICE}/usr/libexec/{os.path.basename(p)}", 0o755)

    for g in glob.glob(f"{MULTI}/libgallium-*.so"):
        os.makedirs(f"{OFFICE}{MULTI}", exist_ok=True)
        shutil.copy(g, f"{OFFICE}{MULTI}/{os.path.basename(g)}")
        link = f"{OFFICE}/lib/{os.path.basename(g)}"
        if not os.path.lexists(link):
            os.symlink(f"{MULTI}/{os.path.basename(g)}", link)

    os.makedirs(f"{OFFICE}/etc/pn", exist_ok=True)
    open(f"{OFFICE}/etc/machine-id", "w").write("b" * 32 + "\n")
    if not os.path.exists(f"{OFFICE}/etc/hosts"):
        open(f"{OFFICE}/etc/hosts", "w").write("127.0.0.1 localhost\n")
    if not os.path.exists(f"{OFFICE}/etc/resolv.conf"):
        open(f"{OFFICE}/etc/resolv.conf", "w").write("nameserver 9.9.9.9\n")
    open(f"{OFFICE}/etc/pn/gui-res", "w").write("1280x800x24\n")
    open(f"{OFFICE}/etc/pn/kbd-layout", "w").write("de\n")
    open(f"{OFFICE}/etc/xdg/openbox/menu.xml", "w").write(MENU_XML)

    rcp = f"{OFFICE}/etc/xdg/openbox/rc.xml"
    if os.path.exists(rcp):
        _rc = open(rcp).read()
        _old = '<action name="ShowMenu"><menu>root-menu</menu></action>'
        _new = '<action name="Execute"><command>/opt/pn/menu.sh</command></action>'
        if _old in _rc:
            open(rcp, "w").write(_rc.replace(_old, _new, 1))
        else:
            print("WARN: openbox rc.xml Rechtsklick-Anker (root-menu) nicht gefunden — bleibt statisch")
    else:
        print("WARN: kein etc/xdg/openbox/rc.xml gestaged — Rechtsklick nicht umgestellt")

    os.makedirs(f"{OFFICE}/opt/pn", exist_ok=True)
    for name, body in (("rfb_vsock_adapter.py", RFB_ADAPTER), ("gui-up.sh", GUI_UP),
                       ("gui-down.sh", GUI_DOWN), ("desk-term.sh", DESK_TERM),
                       ("menu.sh", MENU_SH)):
        p = f"{OFFICE}/opt/pn/{name}"
        open(p, "w").write(body); os.chmod(p, 0o755)
    open(f"{OFFICE}/opt/pn/tint2rc", "w").write(TINT2RC)

    open(f"{OFFICE}/opt/pn/anwendungen.desktop", "w").write(ANWENDUNGEN_DESKTOP)
    os.makedirs(f"{OFFICE}/etc/xdg/jgmenu", exist_ok=True)
    open(f"{OFFICE}/etc/xdg/jgmenu/jgmenurc", "w").write(JGMENURC)
    r = subprocess.run([sys.executable, "gen_launcher_icon.py",
                        f"{OFFICE}/opt/pn/apps-icon.png", "48"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "gen_launcher_icon.py failed: %s" % ((r.stderr or r.stdout).strip()[:300])

    r = subprocess.run([sys.executable, "gen_wallpaper.py",
                        f"{OFFICE}/opt/pn/wallpaper.png", "1920x1200"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "gen_wallpaper.py failed: %s" % ((r.stderr or r.stdout).strip()[:300])

    ip = f"{OFFICE}/sbin/init"; t = open(ip).read()
    if "FLATPAK_USER_DIR" not in t:
        anchor = "export HOME=/root\n"
        assert anchor in t, "session init changed: HOME export anchor not found"
        t = t.replace(anchor, anchor + INIT_EXPORTS, 1)
        anchor = 'echo "PN_WORK_NONE"\n'
        assert anchor in t, "session init changed: /work mount anchor not found"
        t = t.replace(anchor, anchor + INIT_WORKDIR, 1)
        open(ip, "w").write(t)

    seen = set(os.path.basename(x) for x in glob.glob(f"{OFFICE}/lib/*.so*"))
    queue = ([f"{OFFICE}/bin/{os.path.basename(b)}" for b in BINS if os.path.exists(b)]
             + list(SEED_LIBS)
             + [f"{OFFICE}/usr/libexec/{os.path.basename(p)}" for p in libexec]
             + glob.glob(f"{OFFICE}{MULTI}/dri/*.so")
             + glob.glob(f"{OFFICE}{MULTI}/gdk-pixbuf-2.0/2.10.0/loaders/*.so")
             + glob.glob(f"{OFFICE}{MULTI}/imlib2/loaders/*.so")
             + glob.glob(f"{OFFICE}{MULTI}/gio/modules/*.so")
             + glob.glob(f"{OFFICE}/usr/lib/jgmenu/*")
             + glob.glob(f"{OFFICE}/lib/*.so*"))
    for s in SEED_LIBS:
        name = os.path.basename(s)
        if name not in seen and os.path.exists(s) and not os.path.exists(f"{OFFICE}/lib/{name}"):
            shutil.copy(s, f"{OFFICE}/lib/{name}"); seen.add(name)
    added = []
    while queue:
        t = queue.pop()
        if not os.path.isfile(t) or os.path.islink(t) and not os.path.exists(t):
            continue
        for dep in ldd_deps(t):
            name = os.path.basename(dep)
            if name in seen:
                continue
            seen.add(name)
            dst = f"{OFFICE}/lib/{name}"
            if not os.path.exists(dst):
                shutil.copy(dep, dst); added.append(name); queue.append(dst)
    print("closure added (%d):" % len(added), " ".join(sorted(added)[:24]), "…" if len(added) > 24 else "")

    base_abs = os.path.abspath(OFFICE)
    env = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": f"{base_abs}/lib:{base_abs}/lib64", "HOME": "/tmp"}
    print("PN_SMOKE_BEGIN")
    ok = True
    for argv, want in ((["bin/flatpak", "--version"], "Flatpak"),
                       (["bin/openbox", "--version"], "Openbox"),
                       (["bin/xterm", "-version"], "XTerm"),

                       (["bin/tint2", "-v"], "tint2"),
                       (["bin/jgmenu", "--version"], "jgmenu")):
        r = subprocess.run([os.path.join(base_abs, argv[0])] + argv[1:],
                           env=env, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        good = r.returncode == 0 and want in out
        ok = ok and good
        print("  [%s] %s -> %s" % ("PASS" if good else "FAIL", " ".join(argv), out.splitlines()[0] if out else "(no output)"))
    print("PN_SMOKE_%s" % ("PASS" if ok else "FAIL"))
    if not ok:
        print("=> not writing image; fix deps above and re-run."); sys.exit(2)

    sz = subprocess.run(["du", "-sh", OFFICE], capture_output=True, text=True).stdout.split()[0]
    print("office staging size:", sz)
    subprocess.run(["truncate", "-s", SIZE, IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", OFFICE, IMG], check=True)
    print("PN_OFFICE_IMAGE_BUILT", IMG, SIZE)

if __name__ == "__main__":
    main()
