#!/usr/bin/env python3

import os, shutil, subprocess, glob, stat

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
PYBASE = "kernel/_pybase"; X11 = "kernel/_x11base"; IMG = "kernel/base-x11.img"; SIZE = "1536M"

BINS = ["/usr/bin/bash", "/usr/bin/Xvfb", "/usr/bin/x11vnc", "/usr/bin/xkbcomp"]

SEED_LIBS = ["/usr/lib/x86_64-linux-gnu/libgtk-3.so.0", "/usr/lib/x86_64-linux-gnu/libgdk-3.so.0",
             "/usr/lib/x86_64-linux-gnu/libdbus-glib-1.so.2"]

TREES = [
    ("/usr/lib/firefox", "usr/lib/firefox"),
    ("/opt/cisco/secureclient", "opt/cisco/secureclient"),
    ("/opt/.cisco", "opt/.cisco"),

    ("/usr/share/X11/xkb", "usr/share/X11/xkb"),
    ("/usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0", "usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0"),
    ("/etc/fonts", "etc/fonts"),
    ("/usr/share/fonts/truetype/dejavu", "usr/share/fonts/truetype/dejavu"),
]
FILES = [
    ("/usr/share/glib-2.0/schemas/gschemas.compiled", "usr/share/glib-2.0/schemas/gschemas.compiled"),
    ("/etc/ssl/certs/ca-certificates.crt", "etc/ssl/certs/ca-certificates.crt"),
    ("incell_mux_proxy.py", "opt/pn/incell_mux_proxy.py"),
]

RFB_ADAPTER = r'''#!/bin/python3
# rfb_vsock_adapter — spleisst x11vnc (127.0.0.1:5901) mit der Host-RFB-Lane (AF_VSOCK CID2:5900).
# In-cell verbindet AKTIV RAUS (cell-rfb-lane-Muster); Reconnect-Schleife wie cell_gui_app.
import socket, threading, time, sys

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
        v.connect((2, 5900))
        log("vsock lane up")
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
# gui-up.sh — GUI-Phase AN (on-demand; Gegenstueck gui-down.sh). Idempotent (pidof-basiert, busybox).
export DISPLAY=:7 HOME=/root FONTCONFIG_PATH=/etc/fonts
export XKB_CONFIG_ROOT=/usr/share/X11/xkb XKB_BINDIR=/usr/bin
export XDG_RUNTIME_DIR=/tmp/xdg; mkdir -p -m0700 /tmp/xdg
# loopback MUSS hoch, sonst erreicht der Adapter x11vnc auf 127.0.0.1 nicht (Network unreachable).
ip link set lo up 2>/dev/null || ifconfig lo up 2>/dev/null || busybox ip link set lo up 2>/dev/null || true
mkdir -p -m1777 /tmp/.X11-unix
[ -f /tmp/xvfb.pid ] && kill -0 "$(cat /tmp/xvfb.pid)" 2>/dev/null || { Xvfb :7 -screen 0 1280x800x24 -ac -nolisten tcp -noreset >/tmp/xvfb.log 2>&1 & echo $! > /tmp/xvfb.pid; }
for i in $(seq 1 60); do [ -S /tmp/.X11-unix/X7 ] && break; sleep 0.2; done
[ -f /tmp/x11vnc.pid ] && kill -0 "$(cat /tmp/x11vnc.pid)" 2>/dev/null || { x11vnc -display :7 -rfbport 5901 -localhost -auth /dev/null -nopw -forever -shared -noxdamage -quiet >/tmp/x11vnc.log 2>&1 & echo $! > /tmp/x11vnc.pid; }
for i in $(seq 1 40); do { echo > /dev/tcp/127.0.0.1/5901; } 2>/dev/null && break; sleep 0.2; done
[ -f /tmp/rfbadapt.pid ] && kill -0 "$(cat /tmp/rfbadapt.pid)" 2>/dev/null || { /bin/python3 /opt/pn/rfb_vsock_adapter.py >/tmp/rfbadapt.log 2>&1 & echo $! > /tmp/rfbadapt.pid; }
sleep 1; echo "GUI_UP_OK display=:7 xvfb=$(cat /tmp/xvfb.pid 2>/dev/null) x11vnc=$(cat /tmp/x11vnc.pid 2>/dev/null)"
'''

GUI_DOWN = r'''#!/bin/bash
# gui-down.sh — GUI-Phase AUS ("sobald nicht mehr noetig wieder off"). Tunnel-Prozesse (vpnagentd/
# cscotun) bleiben. busybox-sicher: PID-Dateien fuer den GUI-Stack, pidof (matcht comm) fuer die Apps.
for a in firefox vpnui; do kill $(pidof "$a" 2>/dev/null) 2>/dev/null; done
sleep 0.3
for f in rfbadapt x11vnc; do
  p=$(cat /tmp/$f.pid 2>/dev/null); [ -n "$p" ] && kill "$p" 2>/dev/null; rm -f /tmp/$f.pid
done
sleep 0.3
p=$(cat /tmp/xvfb.pid 2>/dev/null); [ -n "$p" ] && kill "$p" 2>/dev/null; rm -f /tmp/xvfb.pid
sleep 0.3
echo "GUI_DOWN_OK xvfb_left=$(pidof Xvfb 2>/dev/null) x11vnc_left=$(pidof x11vnc 2>/dev/null)"
'''

GW_BRIDGE = r'''#!/bin/python3
# gw_bridge.py — lauscht auf lo-Alias-IP:443 und tunnelt jede TCP-Verbindung als HTTP-CONNECT durch
# den governten in-cell Proxy (127.0.0.1:8888) zum ECHTEN Ziel (gleiche IP draussen). So sieht der
# Cisco-Client ein "normales" Gateway, obwohl die Zelle nur die vsock-NET-Lane hat. TCP-only:
# DTLS/UDP landet auf lo, scheitert sofort -> Cisco faellt planmaessig auf TLS-over-TCP zurueck.
import socket, sys, threading

PROXY = ("127.0.0.1", 8888)

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

def handle(c, target):
    try:
        p = socket.create_connection(PROXY, timeout=20)
        p.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" % (target, target)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = p.recv(4096)
            if not d: raise OSError("proxy closed early")
            buf += d
        if b" 200" not in buf.split(b"\r\n", 1)[0]:
            raise OSError("proxy refused: %r" % buf[:100])
        print("[gwbridge] tunnel up -> %s:443" % target, flush=True)
        th = threading.Thread(target=splice, args=(c, p), daemon=True); th.start()
        splice(p, c)
    except OSError as e:
        print("[gwbridge] %s: %r" % (target, e), flush=True)
        try: c.close()
        except OSError: pass

def serve(ip):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((ip, 443)); s.listen(16)
    print("[gwbridge] listen %s:443" % ip, flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c, ip), daemon=True).start()

for _ip in sys.argv[1:]:
    threading.Thread(target=serve, args=(_ip,), daemon=True).start()
threading.Event().wait()
'''

VPN_UP = r'''#!/bin/bash
# vpn-up.sh — VPN-Phase AN (idempotent): lo-Aliase der Gateway-IPs, /etc/hosts-Pins, mux-Proxy
# (governte NET-Lane), gw_bridge (lo:443 -> CONNECT), vpnagentd (root). Die GUI ist davon GETRENNT
# (gui-up/gui-down) — der Tunnel lebt auch ohne Bildschirm weiter.
export HOME=/root
GW_IPS="@VPN_GW_IPS@"
# /dev/shm (POSIX shm) MUSS existieren: vpnagentd legt seinen Inter-Module-State
# (CInterModuleStateProducer, Cisco-Semaphore) dort ab; ohne /dev/shm bricht der Agent mit
# IMSPRODUCER_ERROR_WRITER_INIT_FAILURE ab. Ausserdem watcht der Agent /var/run/utmp.
busybox mkdir -p /dev/shm /var/run /var/log /var/tmp
busybox mount -t tmpfs tmpfs /dev/shm 2>/dev/null
busybox touch /var/run/utmp
ip link set lo up 2>/dev/null || busybox ip link set lo up 2>/dev/null || true
for a in $GW_IPS; do busybox ip addr add "$a/32" dev lo 2>/dev/null; done
grep -q "@VPN_HOSTS_MARK@" /etc/hosts 2>/dev/null || cat >> /etc/hosts <<'HOSTS'
@VPN_HOSTS_BLOCK@
HOSTS
[ -s /etc/resolv.conf ] || echo "nameserver 9.9.9.9" > /etc/resolv.conf
mkdir -p /var/run /var/log /var/tmp /root/.cisco/certificates /root/.config
[ -f /tmp/mux.pid ] && kill -0 "$(cat /tmp/mux.pid)" 2>/dev/null || { PN_PROXY_TRANSPORT=vsock:2:9200 PN_PROXY_PORT=8888 /bin/python3 /opt/pn/incell_mux_proxy.py >/tmp/mux.log 2>&1 & echo $! > /tmp/mux.pid; }
for i in $(seq 1 20); do { echo > /dev/tcp/127.0.0.1/8888; } 2>/dev/null && break; sleep 0.2; done
[ -f /tmp/gwbridge.pid ] && kill -0 "$(cat /tmp/gwbridge.pid)" 2>/dev/null || { /bin/python3 /opt/pn/gw_bridge.py $GW_IPS >/tmp/gwbridge.log 2>&1 & echo $! > /tmp/gwbridge.pid; }
if ! pidof vpnagentd >/dev/null 2>&1; then
  /opt/cisco/secureclient/bin/vpnagentd >/tmp/vpnagentd.log 2>&1 &
fi
for i in $(seq 1 40); do { echo > /dev/tcp/127.0.0.1/29754; } 2>/dev/null && { echo "VPN_UP_OK agent=$(pidof vpnagentd)"; exit 0; }; sleep 0.5; done
echo "VPN_UP_FAIL"; tail -8 /tmp/vpnagentd.log 2>/dev/null
'''

SAML_CATCH = r'''#!/bin/bash
# saml-catch.sh — default-http(s)-Handler der Zelle: SAML-URL festhalten und dann AM LEBEN BLEIBEN.
# (acextwebhelper schliesst den :29786-Callback ~2s nachdem der "Browser"-Handler stirbt —
#  dieselbe Lektion wie saml-open auf dem Host.)
echo "$1" >> /tmp/sso_url.txt
exec sleep 300
'''

SAML_DESKTOP = '''[Desktop Entry]
Type=Application
Name=SAML Catch
Exec=/opt/pn/saml-catch.sh %u
MimeType=x-scheme-handler/http;x-scheme-handler/https;
NoDisplay=true
Terminal=false
'''

MIMEAPPS = '''[Default Applications]
x-scheme-handler/http=saml-catch.desktop
x-scheme-handler/https=saml-catch.desktop
'''

FF_SAML = r'''#!/bin/bash
# ff_saml.sh — Firefox auf :7 mit Proxy-Profil (governte NET-Lane) fuer die SAML/2FA-Seite.
# localhost wird NICHT proxied (allow_hijacking_localhost=false) -> der :29786-Callback von
# acextwebhelper bleibt zell-lokal erreichbar. Sandboxen aus: 4.14-Zellkernel ohne userns.
export DISPLAY=:7 HOME=/root XDG_RUNTIME_DIR=/tmp/xdg FONTCONFIG_PATH=/etc/fonts
export MOZ_DISABLE_CONTENT_SANDBOX=1 MOZ_DISABLE_GMP_SANDBOX=1 MOZ_DISABLE_RDD_SANDBOX=1
export MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1 MOZ_FAKE_NO_SANDBOX=1
mkdir -p /tmp/ffprof /tmp/xdg
cat > /tmp/ffprof/user.js <<'PREFS'
user_pref("network.proxy.type", 1);
user_pref("network.proxy.http", "127.0.0.1");
user_pref("network.proxy.http_port", 8888);
user_pref("network.proxy.ssl", "127.0.0.1");
user_pref("network.proxy.ssl_port", 8888);
user_pref("network.proxy.allow_hijacking_localhost", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("browser.sessionstore.resume_from_crash", false);
PREFS
exec /usr/lib/firefox/firefox --no-remote --profile /tmp/ffprof "$@" >/tmp/firefox.log 2>&1
'''

IPTABLES_STUB = r'''#!/bin/bash
# iptables-Stub: vpnagentd verlangt ein AUFFINDBARES iptables-Binary (CUnixFwUtil::locateIptables)
# und verweigert sonst den Start. In der isolierten Zelle sind Firewall-Regeln gegenstandslos —
# die einzige Netz-Lane ist der governte vsock-Proxy. Also: Aufrufe loggen, Erfolg melden.
echo "$(busybox date +%s) $0 $*" >> /tmp/iptables-calls.log 2>/dev/null
exit 0
'''

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
    assert os.path.isdir(PYBASE), "need kernel/_pybase -> run build_cell_base_python.py first"
    if os.path.exists(X11):
        shutil.rmtree(X11)
    shutil.copytree(PYBASE, X11, symlinks=True)

    os.makedirs(f"{X11}/usr/bin", exist_ok=True)
    for b in BINS:
        base = os.path.basename(b)
        shutil.copy(b, f"{X11}/bin/{base}"); os.chmod(f"{X11}/bin/{base}", 0o755)

        shutil.copy(b, f"{X11}/usr/bin/{base}"); os.chmod(f"{X11}/usr/bin/{base}", 0o755)

    for src, rel in TREES:
        dst = f"{X11}/{rel}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    for src, rel in FILES:
        dst = f"{X11}/{rel}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(os.path.realpath(src), dst)

    link = f"{X11}/bin/firefox"
    if os.path.lexists(link):
        os.remove(link)
    os.symlink("/usr/lib/firefox/firefox", link)

    os.makedirs(f"{X11}/etc", exist_ok=True)
    if not os.path.exists(f"{X11}/etc/passwd"):
        open(f"{X11}/etc/passwd", "w").write("root:x:0:0:root:/root:/bin/bash\n")
        open(f"{X11}/etc/group", "w").write("root:x:0:\n")
    open(f"{X11}/etc/machine-id", "w").write("b" * 32 + "\n")

    open(f"{X11}/etc/hosts", "w").write("127.0.0.1 localhost\n")
    open(f"{X11}/etc/resolv.conf", "w").write("nameserver 9.9.9.9\n")
    os.makedirs(f"{X11}/root/.config", exist_ok=True)
    os.makedirs(f"{X11}/root/.cisco/certificates", exist_ok=True)

    os.makedirs(f"{X11}/opt/pn", exist_ok=True)

    raw_pins = os.environ.get("PN_CELL_VPN_HOSTS", "")
    if not raw_pins:
        try:
            for ln in open("/etc/brainbox/site.conf"):
                if ln.startswith("PN_CELL_VPN_HOSTS="):
                    raw_pins = ln.split("=", 1)[1].strip()
        except OSError:
            pass
    pins = [p.split("=", 1) for p in raw_pins.split(",") if "=" in p]
    vpn_up = (VPN_UP
              .replace("@VPN_GW_IPS@", " ".join(ip for ip, _ in pins))
              .replace("@VPN_HOSTS_MARK@", pins[0][1] if pins else "pn-no-vpn-pins")
              .replace("@VPN_HOSTS_BLOCK@", "\n".join(f"{ip} {fqdn}" for ip, fqdn in pins)))
    for name, body in (("rfb_vsock_adapter.py", RFB_ADAPTER), ("gui-up.sh", GUI_UP),
                       ("gui-down.sh", GUI_DOWN), ("gw_bridge.py", GW_BRIDGE),
                       ("vpn-up.sh", vpn_up), ("saml-catch.sh", SAML_CATCH),
                       ("ff_saml.sh", FF_SAML)):
        p = f"{X11}/opt/pn/{name}"
        open(p, "w").write(body); os.chmod(p, 0o755)

    for rel in ("sbin/iptables", "usr/sbin/iptables", "sbin/ip6tables", "usr/sbin/ip6tables",
                "usr/sbin/iptables-save", "usr/sbin/iptables-restore",
                "usr/sbin/ip6tables-save", "usr/sbin/ip6tables-restore"):
        p = f"{X11}/{rel}"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(IPTABLES_STUB); os.chmod(p, 0o755)

    os.makedirs(f"{X11}/usr/share/applications", exist_ok=True)
    open(f"{X11}/usr/share/applications/saml-catch.desktop", "w").write(SAML_DESKTOP)
    open(f"{X11}/root/.config/mimeapps.list", "w").write(MIMEAPPS)
    open(f"{X11}/usr/share/applications/mimeapps.list", "w").write(MIMEAPPS)

    seen = set(os.path.basename(x) for x in glob.glob(f"{X11}/lib/*.so*"))
    queue = ([f"{X11}/bin/{os.path.basename(b)}" for b in BINS]
             + list(SEED_LIBS)
             + [f"{X11}/usr/lib/firefox/firefox", f"{X11}/usr/lib/firefox/libxul.so"]
             + glob.glob(f"{X11}/opt/cisco/secureclient/bin/*")
             + glob.glob(f"{X11}/opt/cisco/secureclient/bin/plugins/*.so*")
             + glob.glob(f"{X11}/opt/cisco/secureclient/lib/*.so*")
             + glob.glob(f"{X11}/usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/2.10.0/loaders/*.so")
             + glob.glob(f"{X11}/lib/*.so*"))
    for s in SEED_LIBS:
        name = os.path.basename(s)
        if name not in seen and os.path.exists(s):
            shutil.copy(s, f"{X11}/lib/{name}"); seen.add(name)
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
            dst = f"{X11}/lib/{name}"
            if not os.path.exists(dst):
                shutil.copy(dep, dst); added.append(name); queue.append(dst)
    print("closure added (%d):" % len(added), " ".join(sorted(added)[:24]), "…" if len(added) > 24 else "")

    sz = subprocess.run(["du", "-sh", X11], capture_output=True, text=True).stdout.split()[0]
    print("x11 staging size:", sz)
    subprocess.run(["truncate", "-s", SIZE, IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", X11, IMG], check=True)
    print("PN_X11_IMAGE_BUILT", IMG, SIZE)

if __name__ == "__main__":
    main()
