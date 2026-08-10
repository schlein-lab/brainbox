#!/usr/bin/env python3

import argparse, fnmatch, json, os, re, select, shlex, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pn_vpn_netns as V

def _uplink():

    m = re.search(r"\bdev\s+(\S+)", V.sh("ip -o -4 route show default").stdout)
    return m.group(1) if m else "eth0"

UPLINK = None

def _init_uplink():
    global UPLINK
    if UPLINK is None:
        V.UPLINK = UPLINK = _uplink()
    return UPLINK

REFDIR = V.REFDIR
ID_RX = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
ADDR_RX = re.compile(r"^[0-9a-fA-F:.]+(/\d{1,3})?$")
CONNECT_WAIT = 45

OVPN_FORBIDDEN = {"up", "down", "route-up", "ipchange", "plugin", "script-security",
                  "learn-address", "tls-verify", "auth-user-pass-verify", "management", "config"}

class ProfErr(Exception):

    pass

def _load_profile(path):

    if not path or not os.path.isabs(path):
        raise ProfErr("Profilpfad muss absolut sein")
    if not os.path.isfile(path):
        raise ProfErr("Profil nicht gefunden")
    try:
        prof = json.load(open(path))
    except Exception:
        raise ProfErr("Profil nicht lesbar (kein gueltiges JSON)")
    if not isinstance(prof, dict) or not ID_RX.match(str(prof.get("id") or "")):
        raise ProfErr("Profil-ID ungueltig (erwartet ^[a-z][a-z0-9-]{1,23}$)")
    if prof.get("type") not in ("wireguard", "openvpn", "openconnect"):
        raise ProfErr("Profil-Typ ungueltig (wireguard|openvpn|openconnect)")
    c = prof.get("config") or ""
    if c and (c != os.path.basename(c) or c.startswith(".")):
        raise ProfErr("Config-Verweis muss ein einfacher Dateiname im Profilordner sein")
    rt = prof.get("require_tun") or ""
    if rt and not re.match(r"^[A-Za-z0-9*?_.-]{1,15}$", rt):
        raise ProfErr("require_tun-Muster ungueltig")
    return prof

def _read_creds():

    try:
        if sys.stdin.isatty():
            return {}
        r, _, _ = select.select([sys.stdin], [], [], 10)
        if not r:
            return {}
        line = sys.stdin.readline().strip()
        d = json.loads(line) if line else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

class ProfInst(V.Inst):

    def __init__(self, uid, profdir, prof, session="default"):
        self.prof = prof
        self.profdir = profdir

        os.environ["PN_VPN_SHARED"] = "1" if prof.get("shared") else "0"
        os.environ["PN_VPN_REGISTRY"] = REFDIR + "/keine-registry"
        super().__init__(uid, prof["id"], prof.get("gateway", ""), prof.get("protocol") or "", session)
        self.wgif = "wgp%d" % self.idx

    def _pid_file(self):
        return "%s/%s.client.pid" % (REFDIR, self.ns)

    def _tun_file(self):
        return "%s/%s.tun" % (REFDIR, self.ns)

    def _require_tun(self):

        return self.prof.get("require_tun") or (self.wgif if self.prof.get("type") == "wireguard" else "tun*")

    def _conf_path(self):
        c = self.prof.get("config") or ""
        return os.path.join(self.profdir, c) if c else ""

    def _write_tun_marker(self):

        try:
            open(self._tun_file(), "w").write(self._require_tun() + "\n")
        except OSError:
            pass

    def _cg(self):

        return V.cgset("pnvprof-%d-%s" % (self.uid, self.nskey), mx=256 * 2**20)

    def _infra_ready(self):

        if self.ns not in V.sh("ip netns list").stdout:
            return False
        return self.vp in V.nss(self.ns, "ip -o link").stdout

    def up(self):

        os.makedirs(REFDIR, exist_ok=True)
        if self.shared:
            self._ref_add()
            if self._infra_ready():
                self._write_tun_marker()
                return {"ok": True}
        self._teardown(quiet=True)
        if V.sh("ip netns add %s" % self.ns).returncode != 0:
            return {"ok": False, "error": "netns-Anlage fehlgeschlagen"}
        if V.sh("ip link add %s type veth peer name %s" % (self.vh, self.vp)).returncode != 0:
            return {"ok": False, "error": "veth-Anlage fehlgeschlagen"}
        V.sh("ip link set %s netns %s" % (self.vp, self.ns))
        V.sh("ip addr add %s/24 dev %s" % (self.host_ip, self.vh))
        V.sh("ip link set %s up" % self.vh)
        V.nss(self.ns, "ip addr add %s/24 dev %s" % (self.ns_ip, self.vp))
        V.nss(self.ns, "ip link set %s up" % self.vp)
        V.nss(self.ns, "ip link set lo up")
        V.nss(self.ns, "ip route add default via %s" % self.host_ip)
        V.sh("sysctl -wq net.ipv4.ip_forward=1")
        up = _init_uplink()
        for rule in ("-t nat -A POSTROUTING -s %s -o %s -j MASQUERADE" % (self.subnet, up),
                     "-A FORWARD -i %s -o %s -j ACCEPT" % (self.vh, up),
                     "-A FORWARD -i %s -o %s -m state --state RELATED,ESTABLISHED -j ACCEPT" % (up, self.vh)):
            if V.sh("iptables %s" % rule.replace("-A ", "-C ", 1)).returncode != 0:
                V.sh("iptables %s" % rule)
        d = "/etc/netns/%s" % self.ns
        os.makedirs(d, exist_ok=True)
        open(d + "/resolv.conf", "w").write("nameserver 9.9.9.9\n")
        open(d + "/hosts", "w").write("127.0.0.1 localhost\n::1 localhost\n")
        self._write_tun_marker()
        return {"ok": True}

    def _connected(self):
        if self.ns not in V.sh("ip netns list").stdout:
            return False
        if self.prof.get("type") == "wireguard":
            return self._wg_connected()
        return self._tun_connected()

    def _wg_connected(self):

        out = V.nss(self.ns, "wg show %s latest-handshakes" % self.wgif).stdout
        now = time.time()
        for line in out.splitlines():
            f = line.split()
            if f and f[-1].isdigit() and int(f[-1]) > 0 and (now - int(f[-1])) < 180:
                return True
        return False

    def _tun_connected(self):

        pat = self._require_tun()
        devs = []
        for line in V.nss(self.ns, "ip -o link").stdout.splitlines():
            m = re.match(r"^\d+:\s+([^:@\s]+)", line)
            if m:
                devs.append(m.group(1))
        tuns = [d for d in devs if fnmatch.fnmatch(d, pat)]
        if not tuns:
            return False
        rt = V.nss(self.ns, "ip route").stdout
        return any(re.search(r"^(default|0\.0\.0\.0/1)\b.*\bdev %s\b" % re.escape(d), rt, re.M)
                   for d in tuns)

    @staticmethod
    def _wg_parse(text):

        out, addrs, dns, allowed, mtu, sect = [], [], [], [], None, ""
        strip_keys = {"address", "dns", "mtu", "table", "saveconfig",
                      "preup", "postup", "predown", "postdown"}
        for raw in text.replace("\r", "").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            m = re.match(r"^\[(.+)\]$", line)
            if m:
                sect = m.group(1).strip().lower()
                out.append(line)
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip().lower()
                vals = [x.strip() for x in v.split(",") if x.strip()]
                if sect == "interface" and key in strip_keys:
                    if key == "address":
                        addrs += vals
                    elif key == "dns":
                        dns += vals
                    elif key == "mtu":
                        try:
                            mtu = int(vals[0])
                        except (ValueError, IndexError):
                            pass
                    continue
                if sect == "peer" and key == "allowedips":
                    allowed += vals
            out.append(line)
        return "\n".join(out) + "\n", addrs, dns, allowed, mtu

    def _wg_up(self, conf_text):
        if not shutil.which("wg"):
            raise ProfErr("wireguard-tools nicht installiert")
        V.sh("modprobe wireguard 2>/dev/null")
        conf, addrs, dns, allowed, mtu = self._wg_parse(conf_text)
        addrs = [a for a in addrs if ADDR_RX.match(a)]
        allowed = [a for a in allowed if ADDR_RX.match(a)]
        if not addrs:
            raise ProfErr("WireGuard-Conf ohne gueltige Interface-Address")

        V.nss(self.ns, "ip link del %s 2>/dev/null" % self.wgif)
        V.sh("ip link del %s 2>/dev/null" % self.wgif)
        if V.sh("ip link add %s type wireguard" % self.wgif).returncode != 0:
            raise ProfErr("WireGuard-Interface konnte nicht angelegt werden (Kernelmodul fehlt?)")
        if V.sh("ip link set %s netns %s" % (self.wgif, self.ns)).returncode != 0:
            V.sh("ip link del %s 2>/dev/null" % self.wgif)
            raise ProfErr("WireGuard-Interface liess sich nicht in die netns verschieben")

        fd, tmp = tempfile.mkstemp(dir=REFDIR, prefix=self.ns + ".wg.")
        try:
            os.write(fd, conf.encode())
            os.close(fd)
            r = V.nss(self.ns, "wg setconf %s %s" % (self.wgif, tmp))
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if r.returncode != 0:
            raise ProfErr("wg setconf abgelehnt — Conf-Syntax pruefen")
        for a in addrs:
            V.nss(self.ns, "ip addr add %s dev %s" % (shlex.quote(a), self.wgif))
        if mtu:
            V.nss(self.ns, "ip link set %s mtu %d" % (self.wgif, mtu))
        V.nss(self.ns, "ip link set %s up" % self.wgif)

        if "0.0.0.0/0" in allowed:
            V.nss(self.ns, "ip route replace default dev %s" % self.wgif)
        else:
            for c in [c for c in allowed if "." in c]:
                V.nss(self.ns, "ip route replace %s dev %s" % (shlex.quote(c), self.wgif))
        if any(":" in a for a in addrs) and "::/0" in allowed:
            V.nss(self.ns, "ip -6 route replace default dev %s" % self.wgif)
        ns_ips = [x for x in dns if ADDR_RX.match(x) and "/" not in x]
        if ns_ips:
            open("/etc/netns/%s/resolv.conf" % self.ns, "w").write(
                "".join("nameserver %s\n" % x for x in ns_ips))

        tgt = "9.9.9.9" if "0.0.0.0/0" in allowed else (allowed[0].split("/")[0] if allowed else "")
        if tgt and "." in tgt:
            V.bg("ip netns exec %s ping -c 2 -W 3 %s" % (self.ns, shlex.quote(tgt)))

    @staticmethod
    def _ovpn_screen(text):

        inblock = False
        for raw in text.replace("\r", "").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if inblock:
                if line.startswith("</"):
                    inblock = False
                continue
            if line.startswith("<") and not line.startswith("</"):
                inblock = True
                continue
            parts = line.split()
            tok = parts[0].lower().lstrip("-")
            if tok in OVPN_FORBIDDEN:
                return tok

            if tok == "auth-user-pass" and len(parts) > 1:
                return "auth-user-pass mit Dateiargument"
        return None

    def _ovpn_up(self, creds):
        if not shutil.which("openvpn"):
            raise ProfErr("openvpn nicht installiert")
        conf_path = self._conf_path()
        if not conf_path or not os.path.isfile(conf_path):
            raise ProfErr("Profil ohne OpenVPN-Konfigurationsdatei")
        bad = self._ovpn_screen(open(conf_path, errors="replace").read())
        if bad:
            raise ProfErr("OpenVPN-Conf enthaelt verbotene Direktive: %s" % bad)
        cg = self._cg()
        authfile, extra = None, ""
        pw = str(creds.get("password") or "")
        if pw:
            if (self.prof.get("auth") or {}).get("otp") and creds.get("otp"):
                pw += str(creds["otp"])
            fd, authfile = tempfile.mkstemp(dir=REFDIR, prefix=self.ns + ".auth.")
            os.write(fd, ("%s\n%s\n" % (self.prof.get("user", ""), pw)).encode())
            os.close(fd)
            extra = " --auth-user-pass %s" % shlex.quote(authfile)
        try:

            r = V.sh(V.cgin(cg, "ip netns exec %s openvpn --cd %s --config %s --script-security 1"
                                " --auth-nocache --writepid %s --daemon%s"
                                % (self.ns, shlex.quote(self.profdir), shlex.quote(conf_path),
                                   self._pid_file(), extra)))

            time.sleep(1.5)
        finally:
            if authfile:
                try:
                    os.unlink(authfile)
                except OSError:
                    pass
        if r.returncode != 0:
            raise ProfErr("openvpn-Start fehlgeschlagen (rc %d)" % r.returncode)

    @staticmethod
    def _oc_hint(errout):

        e = (errout or "").lower()
        if "certif" in e:
            return "Gateway-Zertifikat nicht akzeptiert"
        if "auth" in e or "password" in e or "login" in e:
            return "Anmeldung abgelehnt (Kennung/Passwort/OTP pruefen)"
        if ("resolve" in e or "unreachable" in e or "timed out" in e
                or "refused" in e or "connect" in e):
            return "Gateway nicht erreichbar"
        return "openconnect-Anmeldung fehlgeschlagen"

    def _oc_up(self, creds):
        if not shutil.which("openconnect"):
            raise ProfErr("openconnect nicht installiert")
        gw = str(self.prof.get("gateway") or "").strip()
        if not gw or not re.match(r"^[A-Za-z0-9._:/\[\]-]+$", gw):
            raise ProfErr("Profil ohne gueltiges Gateway")
        pw = str(creds.get("password") or "")
        if not pw:
            raise ProfErr("Passwort erforderlich (kommt per stdin, nie argv)")
        proto = str(self.prof.get("protocol") or "anyconnect").strip()
        if not re.match(r"^[a-z0-9-]+$", proto):
            raise ProfErr("Ungueltiges Protokoll im Profil")
        user = str(self.prof.get("user") or "")
        cg = self._cg()

        V.sh("modprobe overlay 2>/dev/null")
        for sub in ("upper", "work"):
            os.makedirs("%s/%s" % (self.etcov, sub), exist_ok=True)
        open("%s/upper/resolv.conf" % self.etcov, "w").write("nameserver 9.9.9.9\n")
        open("%s/upper/hosts" % self.etcov, "w").write("127.0.0.1 localhost\n::1 localhost\n")
        inner = ("mount -t overlay overlay -o lowerdir=/etc,upperdir=%s/upper,workdir=%s/work /etc; "
                 "for f in /etc/resolv.conf /etc/hosts; do "
                 "umount $f 2>/dev/null || umount -l $f 2>/dev/null; done; "
                 "exec openconnect --protocol=%s %s--passwd-on-stdin --non-inter -b --pid-file=%s %s"
                 % (self.etcov, self.etcov, shlex.quote(proto),
                    ("--user=%s " % shlex.quote(user)) if user else "",
                    shlex.quote(self._pid_file()), shlex.quote(gw)))
        cmd = V.cgin(cg, "ip netns exec %s unshare -m sh -c %s" % (self.ns, shlex.quote(inner)))
        p = subprocess.Popen(cmd, shell=True, text=True, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             preexec_fn=os.setsid)
        data = pw + "\n"
        if (self.prof.get("auth") or {}).get("otp") and creds.get("otp"):
            data += str(creds["otp"]) + "\n"
        try:
            _, errout = p.communicate(data, timeout=75)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                pass
            raise ProfErr("Zeitueberschreitung beim Gateway-Login")
        if p.returncode != 0:
            raise ProfErr(self._oc_hint(errout))

    def connect(self, creds):
        if self.shared:
            self._ref_add()
        if self._connected():

            self._sync_vpn_dns()
            self._lan_split_tunnel()
            return {"ok": True, "ns": self.ns}
        if not self._infra_ready():
            r = self.up()
            if not r.get("ok"):
                return r
        else:
            self._client_kill()
        try:
            t = self.prof.get("type")
            if t == "wireguard":
                cp = self._conf_path()
                if not cp or not os.path.isfile(cp):
                    raise ProfErr("Profil ohne WireGuard-Konfigurationsdatei")
                self._wg_up(open(cp, errors="replace").read())
            elif t == "openvpn":
                self._ovpn_up(creds)
            else:
                self._oc_up(creds)
        except ProfErr as e:
            return {"ok": False, "error": str(e)}
        deadline = time.time() + CONNECT_WAIT
        while time.time() < deadline:
            if self._connected():
                self._sync_vpn_dns()
                self._lan_split_tunnel()
                return {"ok": True, "ns": self.ns}
            time.sleep(1.5)

        return {"ok": False, "error": "Tunnel nicht verbunden (Zeitueberschreitung nach %ds)" % CONNECT_WAIT}

    def status(self):
        up = self._infra_ready()
        conn = self._connected() if up else False
        if conn:
            self._sync_vpn_dns()
            self._lan_split_tunnel()
        return {"ok": True, "up": up, "connected": conn, "ns": self.ns}

    def test(self, creds, keep=False):

        t0 = time.time()
        r = self.connect(creds)
        if not r.get("ok"):
            if not keep:
                self.down(False)
            return {"ok": False, "error": r.get("error", "Verbindung fehlgeschlagen")}
        egress = ""
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
            o = V.nss(self.ns, "curl -sS --max-time 10 %s" % url)
            cand = (o.stdout or "").strip()
            if o.returncode == 0 and re.match(r"^[0-9a-fA-F:.]{3,45}$", cand):
                egress = cand
                break
        if not keep:
            self.down(False)
        if not egress:
            return {"ok": False, "error": "Egress-Pruefung fehlgeschlagen (keine oeffentliche IP abrufbar)"}
        return {"ok": True, "egress_ip": egress, "seconds": int(time.time() - t0)}

    def _client_kill(self):

        try:
            pid = int(open(self._pid_file()).read().strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid > 1:
            try:
                comm = open("/proc/%d/comm" % pid).read().strip()
            except OSError:
                comm = ""
            if comm in ("openvpn", "openconnect"):
                try:
                    os.kill(pid, 15)
                    time.sleep(1.0)
                    os.kill(pid, 9)
                except OSError:
                    pass
        try:
            os.unlink(self._pid_file())
        except OSError:
            pass

    def _teardown(self, quiet=False):

        self._client_kill()
        if self.ns in V.sh("ip netns list").stdout:
            V.ns_kill(self.ns)
            V.nss(self.ns, "ip link del %s 2>/dev/null" % self.wgif)
        V.sh("ip link del %s 2>/dev/null" % self.wgif)
        up = _init_uplink()
        V.sh("iptables -t nat -D POSTROUTING -s %s -o %s -j MASQUERADE 2>/dev/null" % (self.subnet, up))
        V.sh("iptables -D FORWARD -i %s -o %s -j ACCEPT 2>/dev/null" % (self.vh, up))
        V.sh("iptables -D FORWARD -i %s -o %s -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null"
             % (up, self.vh))
        V.sh("ip netns del %s 2>/dev/null" % self.ns)
        V.sh("ip link del %s 2>/dev/null" % self.vh)
        shutil.rmtree("/etc/netns/%s" % self.ns, ignore_errors=True)
        shutil.rmtree(self.etcov, ignore_errors=True)
        for f in (self._pid_file(), self._tun_file()):
            try:
                os.unlink(f)
            except OSError:
                pass
        try:
            os.rmdir("/sys/fs/cgroup/pnvprof-%d-%s" % (self.uid, self.nskey))
        except OSError:
            pass
        return {"down": True}

def main():
    ap = argparse.ArgumentParser(prog="pn_vpn_profiles")
    ap.add_argument("cmd", choices=["up", "connect", "status", "test", "down"])
    ap.add_argument("--uid", required=True, type=int)
    ap.add_argument("--session", default="default")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        _init_uplink()
        prof = _load_profile(a.profile)
        if os.geteuid() != 0:
            raise ProfErr("root noetig (Aufruf ueber die Portal-sudo-Rail)")
        os.makedirs(REFDIR, exist_ok=True)
        inst = ProfInst(a.uid, os.path.dirname(os.path.abspath(a.profile)), prof, a.session)
        creds = _read_creds() if a.cmd in ("connect", "test") else {}
        if a.cmd == "up":
            r = inst.up()
            out = {"ok": True} if r.get("ok") else r
        elif a.cmd == "connect":
            out = inst.connect(creds)
        elif a.cmd == "status":
            out = inst.status()
        elif a.cmd == "test":
            out = inst.test(creds, keep=a.keep)
        else:
            inst.down(a.force)
            out = {"ok": True}
    except ProfErr as e:
        out = {"ok": False, "error": str(e)}
    except Exception as e:

        out = {"ok": False, "error": "interner Fehler (%s)" % type(e).__name__}
    print(json.dumps(out))
    sys.exit(0 if out.get("ok") else 1)

if __name__ == "__main__":
    main()
