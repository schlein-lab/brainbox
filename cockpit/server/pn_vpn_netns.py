#!/usr/bin/env python3

import argparse, base64, fcntl, io, json, os, pwd, re, shlex, shutil, subprocess, sys, time

try:
    _OWNER = os.environ.get("SUDO_USER") or pwd.getpwuid(1000).pw_name
    _OWNER_HOME = pwd.getpwnam(_OWNER).pw_dir
except KeyError:
    _OWNER = os.environ.get("SUDO_USER") or "root"
    _OWNER_HOME = os.path.expanduser("~" + _OWNER)

def _site_get(key, default=""):

    v = os.environ.get(key)
    if v:
        return v
    for p in ("/etc/brainbox/site.conf", "/run/brainbox/site.env"):
        try:
            for line in open(p):
                line = line.strip()
                if line.startswith(key + "="):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
        except Exception:
            pass
    return default

HPC_VPN_ID = ((os.environ.get("PN_HPC_VPN_ID") or os.environ.get("HPC_VPN_ID") or "").strip()
              or _site_get("HPC_VPN_ID", "hpc"))
HPC_OPERATOR_NETNS = _site_get("HPC_NETNS", "hpc")
HPC_SSH_TARGET = (os.environ.get("PN_HPC_HOST") or os.environ.get("PN_HPC_HOST")
                  or _site_get("HPC_SSH_TARGET", "hpc-front1"))

UPLINK = "ens3"
CISCO = "/opt/cisco/secureclient/bin"
CB_PORT = 29786
DATA_DIR = os.environ.get("PN_PORTAL_DATA") or (_OWNER_HOME + "/.local/share/brainbox-portal")
VMCELLS = DATA_DIR + "/vmcells.json"
VMCELL_DIR = DATA_DIR + "/vmcells"
W, H = 1280, 900
REFDIR = "/run/pn-vpn"

LOGIN_2FA_WINDOW = 900

URL_FRISCH_S = float(os.environ.get("PN_VPN_URL_FRISCH_S", "120"))

def _vpn_registry_path():

    return os.environ.get("PN_VPN_REGISTRY") or (_OWNER_HOME + "/.config/pn-vpn/registry.json")

def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def _vpn_is_shared(vpn):

    if _truthy(os.environ.get("PN_VPN_SHARED", "")):
        return True
    try:
        reg = json.load(open(_vpn_registry_path()))
        for e in (reg if isinstance(reg, list) else []):
            if e.get("id") == vpn:
                return bool(e.get("shared"))
    except Exception:
        pass
    return False

VNC_BRIDGE = '''\
import socket, sys, threading, os
sock, port = sys.argv[1], int(sys.argv[2])
try: os.unlink(sock)
except OSError: pass
s = socket.socket(socket.AF_UNIX); s.bind(sock)
try: os.chmod(sock, 0o666)
except OSError: pass
s.listen(16)
def splice(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except OSError: pass
    for x in (a, b):
        try: x.shutdown(socket.SHUT_RDWR)
        except OSError: pass
while True:
    c, _ = s.accept()
    try:
        t = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError:
        c.close(); continue
    threading.Thread(target=splice, args=(c, t), daemon=True).start()
    threading.Thread(target=splice, args=(t, c), daemon=True).start()
'''

def sh(c, **k):
    return subprocess.run(c, shell=True, text=True, capture_output=True, **k)

def bg(c, log=None):
    return subprocess.Popen(c, shell=True, preexec_fn=os.setsid,
                            stdout=open(log, "w") if log else subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)

def nss(ns, c, **k):
    return sh("ip netns exec %s %s" % (ns, c), **k)

def ns_pids(ns):
    return [p for p in sh("ip netns pids %s" % ns).stdout.split() if p.isdigit()]

def ns_kill(ns, comms=None):

    for p in ns_pids(ns):
        try:
            comm = open("/proc/%s/comm" % p).read().strip()
        except OSError:
            continue
        if comms is None or comm in comms:
            sh("kill -9 %s" % p)

def kill_stray_acext(keep_ns):

    for p in sh("pgrep -x acextwebhelper").stdout.split():
        ns = sh("ip netns identify %s" % p).stdout.strip()

        if ns == keep_ns or ns in (HPC_OPERATOR_NETNS, "hpc") or ns.startswith("pnv-"):
            continue
        sh("kill -9 %s" % p)

def cgset(name, high=None, mx=None):

    cg = "/sys/fs/cgroup/%s" % name
    try:
        os.makedirs(cg, exist_ok=True)
        if high is not None:
            open(cg + "/memory.high", "w").write(str(high))
        if mx is not None:
            open(cg + "/memory.max", "w").write(str(mx))
    except OSError:
        pass
    return cg

def cgin(cg, c):

    return "echo $$ > %s/cgroup.procs 2>/dev/null; %s" % (cg, c)

class Inst:
    def __init__(self, uid, vpn, gateway, group, session="default"):
        import zlib
        self.uid = int(uid); self.vpn = vpn; self.gateway = gateway; self.group = group
        self.session = re.sub(r"[^a-zA-Z0-9]", "", str(session))[:16] or "default"
        tag = re.sub(r"[^a-z0-9]", "", vpn.lower())[:8]; self.tag = tag

        self.shared = _vpn_is_shared(vpn)
        self.nskey = "acct" if self.shared else self.session

        key = "%d-%s-%s" % (self.uid, tag, self.nskey)
        self.idx = 100 + (zlib.crc32(key.encode()) % 120)
        self.dnum = 70 + (zlib.crc32(key.encode()) % 60)
        self.ns = "pnv-%s-%d-%s" % (tag, self.uid, self.nskey)
        self.vh = "pvh%d" % self.idx; self.vp = "pvp%d" % self.idx
        self.host_ip = "10.201.%d.1" % self.idx; self.ns_ip = "10.201.%d.2" % self.idx
        self.subnet = "10.201.%d.0/24" % self.idx
        self.disp = ":%d" % self.dnum
        self.vncport = 5900 + self.dnum
        self.home = "/tmp/pnvpn-%d-%s" % (self.uid, self.nskey)
        self.etcov = "/run/pnvpn-etc/%s" % self.ns
        self.ssofile = "%s/sso.txt" % self.home
        self.cell = "vpn-%d-%s" % (self.uid, self.nskey)
        self.sock = "%s/%s.sock" % (VMCELL_DIR, self.cell)

    def _procs_mit(self, nadel):

        raus = []
        try:
            for n in os.listdir("/proc"):
                if not n.isdigit():
                    continue
                try:
                    with open("/proc/%s/cmdline" % n, "rb") as f:
                        cl = f.read().decode("utf-8", "replace")
                except OSError:
                    continue
                if nadel in cl:
                    raus.append(int(n))
        except OSError:
            pass
        return raus

    _KEIN_ANMELDELINK = ("/saml/sp/logout",)

    def _gefangene_url(self):

        try:
            zeilen = [z.strip() for z in io.open(self.ssofile, encoding="utf-8") if z.strip()]
        except (OSError, IOError):
            return None
        for z in reversed(zeilen):
            if z.startswith("http") and not any(t in z for t in self._KEIN_ANMELDELINK):
                return z
        return None

    def nachstarten(self, timeout=90):

        prof = "%s/ffprofile" % self.home
        if not os.path.isdir(self.home):
            return {"stufe": 2, "grund": "kein Anmelde-HOME — es lief keine Anmeldung",
                    "ergebnis": self.login(timeout)}

        browser = [p for p in self._procs_mit(prof) if "firefox" in
                   io.open("/proc/%d/cmdline" % p, "rb").read().decode("utf-8", "replace")]
        if browser:
            return {"stufe": 0, "grund": "Anmelde-Browser laeuft bereits", "pids": browser,
                    "url": self._gefangene_url(), "stream_cell": self.cell}

        vpnui = self._procs_mit("%s/vpnui.real" % CISCO)
        xvfb = self._procs_mit("Xvfb %s " % self.disp) or self._procs_mit("Xvfb %s\x00" % self.disp)
        url = self._gefangene_url()

        if vpnui and xvfb and url:
            inner = ("exec ip netns exec %s env %s %s/saml-catch.sh %s"
                     % (self.ns, self._cenv(), self.home, url))
            bg(cgin(cgset("pnvpn-browser-%d-%s" % (self.uid, self.session)), inner),
               "/tmp/pnvpn-nachstart-%d.log" % self.uid)
            return {"stufe": 1, "grund": "vpnui wartet weiter — nur der Browser wurde neu geoeffnet "
                                         "(ctx-Token bleibt gueltig)",
                    "url": url, "stream_cell": self.cell}

        fehlt = []
        if not vpnui:
            fehlt.append("vpnui ist beendet (mit ihm der ctx-Token)")
        if not xvfb:
            fehlt.append("der Anmelde-Bildschirm laeuft nicht mehr")
        if not url:
            fehlt.append("es wurde nie eine SAML-URL gefangen")
        return {"stufe": 2, "grund": "; ".join(fehlt) + " -> vollstaendige Neuanmeldung",
                "ergebnis": self.login(timeout)}

    def _ref_file(self):
        return "%s/%s.refs.json" % (REFDIR, self.ns)

    def _ref_rw(self, fn):

        try:
            os.makedirs(REFDIR, exist_ok=True)
        except OSError:
            pass
        f = open(self._ref_file(), "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                cur = set(json.load(f) or [])
            except Exception:
                cur = set()
            cur = fn(set(cur))
            f.seek(0); f.truncate(); json.dump(sorted(cur), f); f.flush()
            return cur
        finally:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
            f.close()

    def _ref_add(self):
        if not self.shared:
            return 0
        return len(self._ref_rw(lambda s: s | {self.session}))

    def _ref_remove(self):
        if not self.shared:
            return 0
        return len(self._ref_rw(lambda s: s - {self.session}))

    def _ref_count(self):
        try:
            return len(set(json.load(open(self._ref_file())) or []))
        except Exception:
            return 0

    def _ref_clear(self):
        try:
            os.unlink(self._ref_file())
        except OSError:
            pass

    def _infra_ready(self):

        return self._agent_healthy()

    def attach(self):

        if not self.shared:
            return {"attached": False, "shared": False, "ns": self.ns}
        n = self._ref_add()
        ex = self.ns in sh("ip netns list").stdout
        return {"attached": True, "shared": True, "ns": self.ns, "ns_exists": ex,
                "connected": (self._connected() if ex else False), "refs": n}

    def up(self):

        if self.shared:
            self._ref_add()
            if self._infra_ready():
                agent = "29754" in nss(self.ns, "ss -tln").stdout
                return {"ns": self.ns, "agent_29754": agent, "gateway_http": "adopted",
                        "adopted": True, "refs": self._ref_count()}
        self._teardown(quiet=True)
        sh("ip netns add %s" % self.ns)
        sh("ip link add %s type veth peer name %s" % (self.vh, self.vp))
        sh("ip link set %s netns %s" % (self.vp, self.ns))
        sh("ip addr add %s/24 dev %s" % (self.host_ip, self.vh)); sh("ip link set %s up" % self.vh)
        nss(self.ns, "ip addr add %s/24 dev %s" % (self.ns_ip, self.vp))
        nss(self.ns, "ip link set %s up" % self.vp); nss(self.ns, "ip link set lo up")

        for _ri in range(12):
            nss(self.ns, "ip route replace default via %s" % self.host_ip)
            if ("default via %s" % self.host_ip) in nss(self.ns, "ip route show default").stdout:
                break
            time.sleep(0.3)
        sh("sysctl -wq net.ipv4.ip_forward=1")
        for rule in ("-t nat -A POSTROUTING -s %s -o %s -j MASQUERADE" % (self.subnet, UPLINK),
                     "-A FORWARD -i %s -o %s -j ACCEPT" % (self.vh, UPLINK),
                     "-A FORWARD -i %s -o %s -m state --state RELATED,ESTABLISHED -j ACCEPT" % (UPLINK, self.vh)):
            if sh("iptables %s" % rule.replace("-A ", "-C ", 1)).returncode != 0:
                sh("iptables %s" % rule)
        d = "/etc/netns/%s" % self.ns; os.makedirs(d, exist_ok=True)
        open(d + "/resolv.conf", "w").write("nameserver 9.9.9.9\n")
        open(d + "/hosts", "w").write("127.0.0.1 localhost\n::1 localhost\n")
        sh("%s/load_tun.sh" % CISCO)
        ns_kill(self.ns, ("vpnagentd",)); time.sleep(0.5)
        _cgi = cgset("pnvpn-infra-%d-%s" % (self.uid, self.session), mx=256 * 2**20)

        sh("modprobe overlay 2>/dev/null")
        for _sub in ("upper", "work"):
            os.makedirs("%s/%s" % (self.etcov, _sub), exist_ok=True)

        open("%s/upper/resolv.conf" % self.etcov, "w").write("nameserver 9.9.9.9\n")
        open("%s/upper/hosts" % self.etcov, "w").write("127.0.0.1 localhost\n::1 localhost\n")
        bg(cgin(_cgi, "ip netns exec %s unshare -m sh -c "
                      "'mount -t overlay overlay -o lowerdir=/etc,upperdir=%s/upper,workdir=%s/work /etc; "
                      "for f in /etc/resolv.conf /etc/hosts; do "
                      "umount $f 2>/dev/null || umount -l $f 2>/dev/null; done; "
                      "exec %s/vpnagentd'" % (self.ns, self.etcov, self.etcov, CISCO)))
        for _ in range(30):
            if "29754" in nss(self.ns, "ss -tln").stdout:
                break
            time.sleep(0.3)
        agent = "29754" in nss(self.ns, "ss -tln").stdout
        net = nss(self.ns, "timeout 8 curl -sS -o /dev/null -w '%%{http_code}' https://%s/ 2>&1" % self.gateway).stdout.strip()
        return {"ns": self.ns, "agent_29754": agent, "gateway_http": net}

    def _prep_home(self):

        for p in (self.home + "/.config", self.home + "/.local/share/applications",
                  self.home + "/run", self.home + "/ffprofile"):
            os.makedirs(p, exist_ok=True)

        open(self.home + "/ffprofile/user.js", "w").write(
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.aboutwelcome.enabled", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
            'user_pref("toolkit.telemetry.enabled", false);\n'
            'user_pref("browser.sessionstore.resume_from_crash", false);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'

            'user_pref("accessibility.force_disabled", 1);\n'
            'user_pref("layers.acceleration.disabled", true);\n'
            'user_pref("gfx.webrender.software", true);\n'
            'user_pref("media.gpu-process-decoder", false);\n'
            'user_pref("dom.ipc.processCount", 1);\n'

            'user_pref("browser.cache.disk.enable", false);\n'
            'user_pref("browser.cache.memory.capacity", 16384);\n'
            'user_pref("browser.sessionhistory.max_total_viewers", 0);\n')
        catch = self.home + "/saml-catch.sh"

        open(catch, "w").write(
            "#!/bin/bash\n"
            "echo \"$1\" >> %s\n"
            "export MOZ_DISABLE_CONTENT_SANDBOX=1 MOZ_DISABLE_GMP_SANDBOX=1 MOZ_DISABLE_RDD_SANDBOX=1 "
            "MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1 MOZ_FAKE_NO_SANDBOX=1\n"
            "export MOZ_DISABLE_A11Y=1 GTK_A11Y=none NO_AT_BRIDGE=1\n"

            "export XDG_RUNTIME_DIR=\"$HOME/run\"\n"
            "mkdir -p \"$XDG_RUNTIME_DIR\" 2>/dev/null; chmod 700 \"$XDG_RUNTIME_DIR\" 2>/dev/null\n"

            "echo 500 > /proc/$$/oom_score_adj 2>/dev/null\n"
            "FF=/usr/lib/firefox/firefox; PROF=%s/ffprofile\n"

            "if pgrep -f \"[f]irefox.*$PROF\" >/dev/null 2>&1; then exit 0; fi\n"
            "if command -v dbus-run-session >/dev/null 2>&1; then\n"
            "  exec dbus-run-session -- \"$FF\" --profile \"$PROF\" \"$1\"\n"
            "else\n"
            "  exec \"$FF\" --profile \"$PROF\" \"$1\"\n"
            "fi\n"
            % (self.ssofile, self.home))
        os.chmod(catch, 0o755)
        open(self.home + "/.local/share/applications/pnvpn-catch.desktop", "w").write(
            "[Desktop Entry]\nType=Application\nName=PNVPN SAML\nExec=%s %%u\n"
            "MimeType=x-scheme-handler/http;x-scheme-handler/https;\nNoDisplay=true\nTerminal=false\n" % catch)
        mm = "[Default Applications]\nx-scheme-handler/http=pnvpn-catch.desktop\nx-scheme-handler/https=pnvpn-catch.desktop\n"
        open(self.home + "/.config/mimeapps.list", "w").write(mm)
        open(self.home + "/.local/share/applications/mimeapps.list", "w").write(mm)
        sh("update-desktop-database %s/.local/share/applications 2>/dev/null" % self.home)

    def _cenv(self):

        return ("HOME=%s XDG_CONFIG_HOME=%s/.config XDG_DATA_HOME=%s/.local/share "
                "XDG_DATA_DIRS=%s/.local/share:/usr/share XDG_RUNTIME_DIR=%s/run "
                "DISPLAY=%s GDK_BACKEND=x11 NO_AT_BRIDGE=1"
                % (self.home, self.home, self.home, self.home, self.home, self.disp))

    def _main_win(self):
        out = sh("DISPLAY=%s bash -lc 'xdotool search --name \"Cisco Secure Client\" 2>/dev/null'" % self.disp).stdout.split()
        return out[0] if out else ""

    def _register_cell(self):
        os.makedirs(VMCELL_DIR, exist_ok=True)
        try:
            reg = json.load(open(VMCELLS))
        except Exception:
            reg = {}
        reg[self.cell] = {"sock": self.sock, "name": "VPN-Login %s" % self.vpn, "w": W, "h": H, "pid": os.getpid()}
        tmp = VMCELLS + ".tmp"
        json.dump(reg, open(tmp, "w"))
        os.replace(tmp, VMCELLS)
        sh("chown %s:%s %s %s 2>/dev/null" % (_OWNER, _OWNER, VMCELLS, VMCELL_DIR))

    def _unregister_cell(self):
        try:
            reg = json.load(open(VMCELLS))
            if self.cell in reg:
                del reg[self.cell]
                tmp = VMCELLS + ".tmp"; json.dump(reg, open(tmp, "w")); os.replace(tmp, VMCELLS)
                sh("chown %s:%s %s 2>/dev/null" % (_OWNER, _OWNER, VMCELLS))
        except Exception:
            pass

    def _uplink_ok(self):

        main = nss(self.ns, "ip route show").stdout
        return (self.subnet in main) and (("default via %s" % self.host_ip) in main)

    def _uplink_repair(self):

        if self._connected():
            return True
        for _ in range(3):
            nss(self.ns, "ip link set %s up" % self.vp)
            nss(self.ns, "ip route replace %s dev %s proto kernel scope link src %s"
                         % (self.subnet, self.vp, self.ns_ip))
            nss(self.ns, "ip route replace default via %s" % self.host_ip)
            if self._uplink_ok():
                return True
            time.sleep(0.3)
        return False

    def _agent_listening(self):

        return (self.ns in sh("ip netns list").stdout) and ("29754" in nss(self.ns, "ss -tln").stdout)

    def _agent_alive(self):

        for p in ns_pids(self.ns):
            try:
                if open("/proc/%s/comm" % p).read().strip() == "vpnagentd":
                    return True
            except OSError:
                continue
        return False

    def _agent_healthy(self):

        if not self._agent_listening():
            return False
        if self._connected():
            return True

        if not self._uplink_ok() and not self._uplink_repair():
            return False
        if not self._agent_alive():
            return False
        r = nss(self.ns, "timeout 6 %s/vpn.real state </dev/null 2>/dev/null" % CISCO)
        return r.returncode != 124 and ("state:" in r.stdout.lower())

    def _vpnui_alive(self):

        for p in ns_pids(self.ns):
            try:
                if open("/proc/%s/comm" % p).read().strip() in ("vpnui.real", "vpnui"):
                    return True
            except OSError:
                continue
        return False

    ASA_FEHLERSEITE = "/+CSCOE+/message.html"
    HEILVERSUCHE = int(os.environ.get("PN_VPN_HEILVERSUCHE", "2"))

    def _ff_letzte_url(self):

        quelle = "%s/ffprofile/places.sqlite" % self.home
        if not os.path.exists(quelle):
            return None
        ziel = "%s/.places-probe.sqlite" % self.home
        try:
            import sqlite3
            shutil.copy2(quelle, ziel)
            for _zus in ("-wal", "-shm"):
                if os.path.exists(quelle + _zus):
                    shutil.copy2(quelle + _zus, ziel + _zus)
            db = sqlite3.connect(ziel)
            try:
                r = db.execute("select p.url from moz_historyvisits v join moz_places p "
                               "on p.id=v.place_id order by v.visit_date desc limit 1").fetchone()
            finally:
                db.close()
            return r[0] if r else None
        except Exception:
            return None
        finally:
            for _p in (ziel, ziel + "-wal", ziel + "-shm"):
                try:
                    os.unlink(_p)
                except OSError:
                    pass

    def _ff_brauchbar(self):

        if not self._ff_alive():
            return False
        u = self._ff_letzte_url()
        if u and self.ASA_FEHLERSEITE in u:
            return False
        return True

    def _wache_fehlerseite(self, timeout, versuch):

        import threading

        def lauf():
            ende = time.time() + LOGIN_2FA_WINDOW
            while time.time() < ende:
                time.sleep(5)
                try:
                    if self._connected():
                        return
                    if not self._ff_alive():
                        return
                    u = self._ff_letzte_url()
                    if not (u and self.ASA_FEHLERSEITE in u):
                        continue
                    if versuch >= self.HEILVERSUCHE:
                        sys.stderr.write("[pnvpn] ASA verwarf den Kontext erneut (Versuch %d) — "
                                         "keine weitere Selbstheilung\n" % versuch)
                        return
                    sys.stderr.write("[pnvpn] ASA-Fehlerseite erkannt -> frischer Anmelde-Link "
                                     "(Selbstheilung %d)\n" % (versuch + 1))
                    self.login(timeout=timeout, force=True, _versuch=versuch + 1)
                    return
                except Exception:
                    return

        threading.Thread(target=lauf, daemon=True).start()

    def _ff_alive(self):

        needle = ("%s/ffprofile" % self.home).encode()
        for p in ns_pids(self.ns):
            try:
                if needle in open("/proc/%s/cmdline" % p, "rb").read():
                    return True
            except OSError:
                continue
        return False

    def login(self, timeout=90, force=False, _versuch=0):

        if self.shared:
            self._ref_add()
            if self._connected():
                return {"stream_cell": None, "connected": True, "adopted": True,
                        "ns": self.ns, "refs": self._ref_count()}

        if not force and self._connected():
            return {"stream_cell": None, "connected": True, "ns": self.ns,
                    "note": "Der Tunnel steht bereits — es wurde nichts angefasst. "
                            "Fuer eine echte Neuanmeldung: force."}

        _agent_up = self._agent_healthy()
        if not _agent_up:
            r = self.up()
            if not r.get("agent_29754"):
                return {"error": "vpnagentd kam nicht hoch", "detail": r}

        os.makedirs(self.home, exist_ok=True)
        self._lk = open(os.path.join(self.home, ".login.flock"), "a+")
        try:
            fcntl.flock(self._lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:

            for _ in range(20):
                time.sleep(1)
                if self._connected():
                    return {"stream_cell": self.cell, "url": None, "connected": True, "in_progress": False}
                if os.path.exists(self.ssofile):
                    u = self._gefangene_url()
                    if u:
                        return {"stream_cell": self.cell, "url": u, "connected": False,
                                "in_progress": True, "note": "Es lief bereits ein Login-Versuch — dessen Link."}
            return {"stream_cell": self.cell, "url": None, "connected": False, "in_progress": True,
                    "busy": True, "note": "Der Anmelde-Versuch laeuft noch — das Anmelde-Fenster ist offen."}

        lock = os.path.join(self.home, ".login.inflight")
        try:
            if os.path.exists(lock):
                age = time.time() - os.path.getmtime(lock)
                u = None
                if os.path.exists(self.ssofile):
                    u = self._gefangene_url()
                conn = self._connected()

                if conn:
                    return {"stream_cell": self.cell, "url": u, "connected": True, "in_progress": False}

                u_alt = None
                if u:
                    try:
                        u_alt = time.time() - os.path.getmtime(self.ssofile)
                    except OSError:
                        u_alt = None

                lebendig = self._ff_brauchbar() or (u and u_alt is not None and u_alt < URL_FRISCH_S)
                if not force and self._vpnui_alive() and lebendig and age < LOGIN_2FA_WINDOW:
                    return {"stream_cell": self.cell, "url": u, "connected": False, "in_progress": True,
                            "note": "Ein Anmelde-Versuch laeuft bereits (2FA offen) — dessen Fenster/Link gilt."}

                if not force and self._vpnui_alive() and age < 45:
                    return {"stream_cell": self.cell, "url": u, "connected": conn, "in_progress": True}
        except Exception:
            pass
        self._prep_home()
        open(lock, "w").write(str(time.time()))
        try:
            os.unlink(self.ssofile)
        except OSError:
            pass

        sh("pkill -f 'Xvfb %s'" % self.disp)
        ns_kill(self.ns, ("vpnui.real", "vpnui"))
        sh("pkill -9 -f '%s/ffprofile'" % self.home)

        kill_stray_acext(self.ns)

        sh("pkill -f 'pnvpn-vncbridge.py %s'" % self.sock)

        sh(r"pkill -f 'pnvpn-vncbridge\.py .* %d$'" % self.vncport); time.sleep(1)
        os.makedirs(VMCELL_DIR, exist_ok=True)
        open("/tmp/pnvpn-vncbridge.py", "w").write(VNC_BRIDGE)

        cgi = cgset("pnvpn-infra-%d-%s" % (self.uid, self.session), mx=256 * 2**20)
        cgb = cgset("pnvpn-browser-%d-%s" % (self.uid, self.session), high=1024 * 2**20, mx=1536 * 2**20)
        bg(cgin(cgi, "Xvfb %s -screen 0 %dx%dx24 -ac -nolisten tcp" % (self.disp, W, H)),
           "/tmp/pnvpn-xvfb-%d.log" % self.uid)
        time.sleep(1.5)
        bg(cgin(cgi, "DISPLAY=%s openbox" % self.disp), "/tmp/pnvpn-openbox-%d.log" % self.uid); time.sleep(1)

        bg(cgin(cgi, "x11vnc -display %s -rfbport %d -localhost -nopw -forever -shared -noxdamage -quiet -loop2000"
           % (self.disp, self.vncport)), "/tmp/pnvpn-x11vnc-%d.log" % self.uid); time.sleep(1)

        bg(cgin(cgi, "python3 /tmp/pnvpn-vncbridge.py %s %d" % (self.sock, self.vncport)))
        time.sleep(0.5)
        self._register_cell()

        if not os.path.exists("/var/run/utmp"):
            open("/var/run/utmp", "wb").close()
        sh("sessreg -a -l :0 -u /var/run/utmp %s" % _OWNER)

        bg(cgin(cgb, "ip netns exec %s env %s %s/vpnui.real" % (self.ns, self._cenv(), CISCO)),
           "/tmp/pnvpn-vpnui-%d.log" % self.uid)
        win = ""
        for _ in range(30):
            time.sleep(1); win = self._main_win()
            if win:
                break
        if not win:
            return {"error": "vpnui-Fenster kam nicht", "stream_cell": self.cell}
        time.sleep(2)
        g = dict(re.findall(r"(\w+)=(\-?\d+)", sh("DISPLAY=%s xdotool getwindowgeometry --shell %s" % (self.disp, win)).stdout))
        bx = int(g.get("X", 0)) + int(g.get("WIDTH", 451)) // 2
        by = int(g.get("Y", 0)) + int(g.get("HEIGHT", 535)) - 91

        sh("DISPLAY=%s xdotool mousemove %d %d click 1" % (self.disp, bx, by))
        url = None
        for i in range(timeout // 2):
            time.sleep(2)
            if os.path.exists(self.ssofile):
                u = self._gefangene_url()
                if u:
                    url = u; break

        self._wache_fehlerseite(timeout, _versuch)
        return {"stream_cell": self.cell, "url": url, "connected": self._connected(),
                "note": "Login-Seite im Portal-Stream (Screen-Tab / Zelle %s) — dort b*-Kennung + 2FA. "
                        "Danach steht der Tunnel automatisch." % self.cell}

    def _connected(self):
        return "cscotun" in nss(self.ns, "ip -br addr").stdout if self.ns in sh("ip netns list").stdout else False

    def _sync_vpn_dns(self):

        src = "%s/upper/resolv.conf" % self.etcov
        dst = "/etc/netns/%s/resolv.conf" % self.ns
        try:
            if os.path.exists(src) and os.path.isdir(os.path.dirname(dst)):
                txt = open(src).read()
                if "nameserver" in txt and open(dst).read() != txt:
                    open(dst, "w").write(txt)
        except OSError:
            pass

    def _host_lan_cidr(self):

        try:
            for line in sh("ip -o -4 route show scope link dev %s" % UPLINK).stdout.splitlines():
                parts = line.split()
                cidr = parts[0] if parts else ""
                if "/" in cidr and not cidr.startswith(("169.254.", "0.", "127.")):
                    return cidr
        except Exception:
            pass
        return None

    def _lan_split_tunnel(self):

        try:
            lan = self._host_lan_cidr()
            if not lan:
                return
            nss(self.ns, "ip route replace %s via %s dev %s table 199" % (lan, self.host_ip, self.vp))
            if "lookup 199" not in nss(self.ns, "ip rule").stdout:
                nss(self.ns, "ip rule add to %s lookup 199 priority 100" % lan)
            for chain, spec in (("OUTPUT", "-d %s -o %s" % (lan, self.vp)),
                                ("INPUT", "-s %s -i %s" % (lan, self.vp))):
                if nss(self.ns, "iptables -C %s %s -j ACCEPT" % (chain, spec)).returncode != 0:
                    nss(self.ns, "iptables -I %s 1 %s -j ACCEPT" % (chain, spec))
        except Exception:
            pass

    def status(self):
        ex = self.ns in sh("ip netns list").stdout

        if not ex and self.nskey not in ("acct", "default"):
            acct = Inst(self.uid, self.vpn, self.gateway, self.group, session="default")
            if acct.ns in sh("ip netns list").stdout:
                out = acct.status()
                out["queried_ns"] = self.ns
                out["via_account"] = True
                return out
        url = None
        url_verfallen = False
        if os.path.exists(self.ssofile):
            url = self._gefangene_url()

        if url and not self._ff_brauchbar() and not self._connected():
            try:
                if (time.time() - os.path.getmtime(self.ssofile)) >= URL_FRISCH_S:
                    url, url_verfallen = None, True
            except OSError:
                pass
        conn = self._connected()

        if conn:
            self._sync_vpn_dns()
            self._lan_split_tunnel()
            self._gui_down()
        return {"ns": self.ns, "ns_exists": ex, "connected": conn, "url": url,

                "url_verfallen": url_verfallen,
                "stream_cell": self.cell if (ex and not conn) else None}

    def _gui_down(self):
        sh("pkill -f 'Xvfb %s'" % self.disp); ns_kill(self.ns, ("vpnui.real", "vpnui"))
        sh("pkill -f 'vnc-bridge-%s'" % self.cell); sh("pkill -f 'x11vnc -display %s'" % self.disp)
        sh("pkill -9 -f '%s/ffprofile'" % self.home)
        self._unregister_cell()
        for f in (self.sock, os.path.join(self.home, ".login.inflight")):
            try:
                os.unlink(f)
            except OSError:
                pass

    def _teardown(self, quiet=False):
        self._gui_down()
        ns_kill(self.ns)
        sh("iptables -t nat -D POSTROUTING -s %s -o %s -j MASQUERADE 2>/dev/null" % (self.subnet, UPLINK))
        sh("iptables -D FORWARD -i %s -o %s -j ACCEPT 2>/dev/null" % (self.vh, UPLINK))
        sh("iptables -D FORWARD -i %s -o %s -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null" % (UPLINK, self.vh))
        sh("ip netns del %s 2>/dev/null" % self.ns); sh("ip link del %s 2>/dev/null" % self.vh)
        shutil.rmtree("/etc/netns/%s" % self.ns, ignore_errors=True)
        shutil.rmtree(self.etcov, ignore_errors=True)
        try:
            os.unlink(self.ssofile)
        except OSError:
            pass
        return {"down": True}

    def down(self, force=False):

        if not self.shared:
            return self._teardown()
        remaining = self._ref_remove()
        if remaining > 0 and not force:
            return {"down": False, "detached": True, "ns": self.ns, "refs": remaining}
        r = self._teardown()
        self._ref_clear()
        r["refs"] = 0; r["detached"] = True
        return r

SSH_KEY_PASS_SECRET = "hpc_key_pass"
SSH_ASKPASS = "/tmp/.pnvpn-hkey-ap.sh"

def find_live_netns(uid, vpn):

    tag = re.sub(r"[^a-z0-9]", "", vpn.lower())[:8]
    pfx = "pnv-%s-%d-" % (tag, int(uid))
    cands = [l.split()[0] for l in sh("ip netns list").stdout.split("\n")
             if l.split() and l.split()[0].startswith(pfx)]
    live = [ns for ns in cands if "cscotun" in nss(ns, "ip -br addr").stdout]
    if not live:
        return None

    live.sort(key=lambda n: (0 if n.endswith("-acct") else (1 if n.endswith("-default") else 2), n))
    return live[0]

def hssh(uid, vpn, target, cmd_b64, session=None, timeout=90):

    if session:
        ns = Inst(uid, vpn, "", "2fa", session).ns
        if not (ns in sh("ip netns list").stdout and "cscotun" in nss(ns, "ip -br addr").stdout):
            ns = find_live_netns(uid, vpn)
    else:
        ns = find_live_netns(uid, vpn)
    if not ns:
        return {"error": "kein aktiver VPN-Tunnel (cscotun) fuer diese Kennung", "connected": False}
    open(SSH_ASKPASS, "w").write("#!/bin/bash\n%s/.local/bin/phantom secret get %s\n" % (_OWNER_HOME, SSH_KEY_PASS_SECRET))
    os.chmod(SSH_ASKPASS, 0o755)
    raw = base64.b64decode(cmd_b64).decode() if cmd_b64 else "hostname"

    remote = "bash -lc " + shlex.quote(raw)
    args = ["ip", "netns", "exec", ns, "runuser", "-u", _OWNER, "--",
            "env", "HOME=%s" % _OWNER_HOME, "SSH_ASKPASS=%s" % SSH_ASKPASS,
            "SSH_ASKPASS_REQUIRE=force", "DISPLAY=", "BATCH=",
            "setsid", "-w", "ssh", "-F", "%s/.ssh/config" % _OWNER_HOME,
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
            "-o", "ServerAliveInterval=15", target or HPC_SSH_TARGET, remote]
    try:
        pr = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "Zeitueberschreitung zum Cluster", "connected": True, "ns": ns}
    out = (pr.stdout or "").strip()
    if pr.returncode != 0 and not out:
        out = (pr.stderr or "").strip()[-1500:]
    return {"rc": pr.returncode, "out": out[-8000:], "ns": ns, "target": target or HPC_SSH_TARGET, "connected": True}

def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("cmd", choices=["up", "login", "nachstarten", "status", "attach", "down",
                                    "hssh", "hstat"])
    ap.add_argument("--uid", default="1000")
    ap.add_argument("--vpn", default=HPC_VPN_ID)
    ap.add_argument("--session", default="default")
    ap.add_argument("--force", action="store_true")

    ap.add_argument("--gateway", default=os.environ.get("PN_VPN_GATEWAY", "")); ap.add_argument("--group", default="2fa")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--target", default=HPC_SSH_TARGET)
    ap.add_argument("--rcmd", default="")
    a = ap.parse_args()
    if os.geteuid() != 0:
        print(json.dumps({"error": "root noetig"})); sys.exit(2)
    if a.cmd == "hstat":
        ns = find_live_netns(int(a.uid), a.vpn)
        print(json.dumps({"connected": bool(ns), "ns": ns})); return
    if a.cmd == "hssh":
        out = hssh(int(a.uid), a.vpn, a.target, a.rcmd,
                   session=(a.session if a.session != "default" else None), timeout=a.timeout)
        print(json.dumps(out)); return
    if a.cmd in ("up", "login") and not a.gateway:
        print(json.dumps({"error": "kein Gateway: --gateway oder PN_VPN_GATEWAY setzen (kommt normal aus der VPN-Registry)"})); sys.exit(2)
    inst = Inst(a.uid, a.vpn, a.gateway, a.group, a.session)
    out = {"up": inst.up, "login": lambda: inst.login(a.timeout, a.force), "status": inst.status,
           "nachstarten": lambda: inst.nachstarten(a.timeout),
           "attach": inst.attach, "down": lambda: inst.down(a.force)}[a.cmd]()
    print(json.dumps(out))

if __name__ == "__main__":
    main()
