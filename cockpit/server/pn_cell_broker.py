#!/usr/bin/env python3

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pn_cell_basis import NET_BROKER, SONOS_SRC, _adapter_reap
from pn_cell_fern import RemoteVmm
from pn_cell_gastquellen import _DNS_STUB_SRC, _sonos_rooms_b64

def _kill_cell_brokers(run_dir):

    try:
        me = os.getpid()
        prefix = run_dir.rstrip("/") + "/"
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == me:
                continue
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "--unix-mux" in cmd and prefix in cmd:
                try: os.kill(int(pid), 9)
                except OSError: pass
        _adapter_reap(run_dir)
    except Exception:
        pass

def _reap_dead_cell_brokers(run_dir, meta):

    try:
        pid = (meta or {}).get("vmm_pid")
        if not pid:
            return
        try:
            if open("/proc/%d/comm" % int(pid)).read().strip() == "pn-vmm":
                return
        except (OSError, ValueError):
            pass
        _kill_cell_brokers(run_dir)
    except Exception:
        pass

_INTERACTIVE_CG = os.environ.get("PN_INTERACTIVE_SESS_CG", "/sys/fs/cgroup/pn.slice/interactive/sessions")

def _pnd_rpc(req, timeout=4.0):

    try:

        import sys as _s
        for _base in (os.environ.get("PNLIB_HOME"),
                      os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                   "engine"),
                      os.path.expanduser("~/portioneer")):
            if _base and os.path.isdir(os.path.join(_base, "pnlib")) and _base not in _s.path:
                _s.path.insert(0, _base)
        from pnlib import ipc as _ipc
        return _ipc.send_request(req, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": "pnd unreachable: %s" % e}

def _cell_broker_pids(run_dir):

    out = []
    try:
        me = os.getpid()
        prefix = run_dir.rstrip("/") + "/"
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == me:
                continue
            try:
                cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "--unix-mux" in cmd and prefix in cmd:
                out.append(int(pid))
    except Exception:
        pass
    return out

class CellBrokerNetzMixin:

    def _pn_register(self, mem_mb):

        try:

            pid = 0 if isinstance(self.proc, RemoteVmm) else (int(self.proc.pid) if self.proc is not None else 0)
        except Exception:
            pid = 0

        nid = self._remote_node()
        r = _pnd_rpc({"verb": "session-attach", "cell": self.cell,
                      "cell_principal": str(self.principal), "session": str(self.session),
                      "kind": "voice" if "voice" in str(self.session) else "session",
                      "mem_mb": int(mem_mb), "pid": pid,
                      **({"node": nid} if nid else {})})
        jid = r.get("id") if isinstance(r, dict) and r.get("ok") else None
        if jid:
            try:
                tmp = self.pnjob_file + ".tmp"
                with open(tmp, "w") as f:
                    f.write(str(int(jid)))
                os.replace(tmp, self.pnjob_file)
            except OSError:
                pass
        else:
            try:
                import sys as _sys
                _sys.stderr.write("[pn-session] ATTACH FAILED for %s (%s) — Zelle laeuft, ist aber "
                                  "fuer die Queue unsichtbar\n"
                                  % (self.cell, (r or {}).get("error")))
            except Exception:
                pass
        self._move_to_interactive_slice()
        return jid

    def _pn_unregister(self, state="done", reason=None):

        jid = None
        try:
            jid = int(open(self.pnjob_file).read().strip())
        except (OSError, ValueError):
            return
        _pnd_rpc({"verb": "session-detach", "job_id": jid, "state": state,
                  "reason": reason or "cell teardown (portal)"})
        try:
            os.unlink(self.pnjob_file)
        except OSError:
            pass

    def _move_to_interactive_slice(self):

        pids = [getattr(p, "pid", None)
                for p in (self.proc, self.broker, self.portal_broker, self.net_broker, self.act_broker)
                if p is not None and not isinstance(p, RemoteVmm)]
        pids = sorted(set([p for p in pids if p] + _cell_broker_pids(self.run_dir)))

        _me = os.getuid()
        _own = []
        for _p in pids:
            try:
                if os.stat("/proc/%d" % _p).st_uid == _me:
                    _own.append(_p)
            except OSError:
                pass
        pids = _own
        if not pids:
            return
        try:
            os.makedirs(_INTERACTIVE_CG, exist_ok=True)
        except OSError:
            pass
        moved = []
        procs_f = os.path.join(_INTERACTIVE_CG, "cgroup.procs")
        for pid in pids:
            try:
                with open(procs_f, "w") as f:
                    f.write(str(pid))
                moved.append(pid)
            except (OSError, ValueError):
                pass
        left = [p for p in pids if p not in moved]
        if left:
            try:
                subprocess.run(["sudo", "-n", "/usr/local/bin/pn-cgmove", "--sessions"]
                               + [str(p) for p in left],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except Exception:
                pass
            for pid in left[:]:
                try:
                    if "/pn.slice/interactive/" in open("/proc/%d/cgroup" % pid).read():
                        moved.append(pid); left.remove(pid)
                except OSError:
                    left.remove(pid)
        try:
            import sys as _sys
            if moved:
                _sys.stderr.write("[pn-session] %s -> pn.slice/interactive/sessions (pids %s)\n"
                                  % (self.cell, ",".join(map(str, moved))))
            if left:
                _sys.stderr.write("[pn-session] PLACEMENT FAILED for %s (pids %s bleiben in der "
                                  "control-slice — pn-cgmove/sudoers fehlt?)\n"
                                  % (self.cell, ",".join(map(str, left))))
        except Exception:
            pass

    def _net_broker_cmd(self, pol, nenv):

        base = ["/usr/bin/python3", NET_BROKER, "--unix-mux", self.net_sock]
        try:
            _de = os.path.join(os.path.expanduser("~/.local/share/brainbox-portal"),
                               "direct-egress", str(self.session))
            if os.path.exists(_de):
                self._log("direct-egress (kein VPN-Bind): Netz-Broker im Host-netns (plain NAT)")
                self.vpn_netns_active = ""
                return base
        except Exception:
            pass
        vpn_ns = str((pol or {}).get("vpn_netns") or "").strip()
        if not vpn_ns:

            sfx = "-" + str(self.session)

            try:
                from pn_cell_lifecycle import braucht_ssh_bahn as _will_bahn
                will_bahn = bool(_will_bahn(pol, False))
            except Exception as _e:
                self._log("VPN: braucht_ssh_bahn nicht auswertbar (%s) -> Konto-Tunnel "
                          "wird NICHT uebernommen" % _e)
                will_bahn = False
            uebergangen = ""

            try:
                import zlib as _zl
                _ouid = 1000 + (_zl.crc32(str(self.principal or "owner").encode()) % 200)

                acct_sfxs = ["-%d-acct" % _ouid, "-%d-default" % _ouid]
            except Exception:
                acct_sfxs = []
            for d in ("/run/netns", "/var/run/netns"):
                try:
                    names = sorted(os.listdir(d))
                except OSError:
                    names = []
                for n in names:
                    if n.startswith("pnv-") and n.endswith(sfx):
                        vpn_ns = n
                        break
                if not vpn_ns and acct_sfxs:
                    for n in names:
                        if n.startswith("pnv-") and any(n.endswith(a) for a in acct_sfxs):
                            if will_bahn:
                                vpn_ns = n
                            else:
                                uebergangen = n
                            break
                if vpn_ns:
                    break
            if vpn_ns:
                self._log("session-VPN entdeckt: Netz-Broker zieht in netns %s (fail-closed an cscotun*)" % vpn_ns)
            elif uebergangen:
                self._log("Konto-Tunnel %s NICHT uebernommen: diese Zelle will die SSH-Bahn "
                          "nicht (kein hpc_submit, kein SSH-Geheimnis, kein vpn_netns) "
                          "-> normales Netz" % uebergangen)
        self.vpn_netns_active = vpn_ns
        if not vpn_ns:
            return base
        require_tun = str((pol or {}).get("require_tun") or "cscotun").strip()
        nenv["PN_REQUIRE_TUN"] = require_tun
        askpass = os.environ.get("PN_NETNS_ASKPASS", "/tmp/.pnvpn-portal-askpass.sh")
        nenv["SUDO_ASKPASS"] = askpass
        try:
            boxuser = os.environ.get("USER") or __import__("pwd").getpwuid(os.getuid()).pw_name
        except Exception:
            boxuser = os.environ.get("USER") or "root"
        if not os.path.exists("/run/netns/" + vpn_ns) and not os.path.exists("/var/run/netns/" + vpn_ns):

            self._log("VPN-Dauerjob: netns %s fehlt -> Netz-Broker startet OHNE Tunnel (fail-closed, kein Egress)" % vpn_ns)

        durchreichen = ["PN_POLICY_FILE=%s" % self.policy_file,
                        "PN_REQUIRE_TUN=%s" % require_tun,
                        "PN_NET_BROKER_LOG=%s" % nenv.get("PN_NET_BROKER_LOG",
                                                          "/tmp/pn-net-broker.log")]
        for k in ("PN_SESSION_CELL", "PN_PRINCIPAL"):
            v = str(nenv.get(k) or "").strip()
            if v:
                durchreichen.append("%s=%s" % (k, v))
        return ["sudo", "-A", "ip", "netns", "exec", vpn_ns,
                "sudo", "-u", boxuser, "env"] + durchreichen + base

    def netz_tor_offen(self):

        if not self.alive():
            return None
        ok, out = self._run(
            "/bin/python3 -c \"import socket;s=socket.socket();s.settimeout(2);"
            "print('OFFEN' if s.connect_ex(('127.0.0.1',8888))==0 else 'ZU')\" 2>/dev/null; echo __NT__",
            "__NT__", 15)
        kopf = out.split("__NT__")[0]
        if "OFFEN" in kopf:
            return True
        if "ZU" in kopf:
            return False
        return None

    def _netz_tor_starten(self, versuche=3):

        for versuch in range(1, max(1, int(versuche)) + 1):
            self._run("PN_PROXY_TRANSPORT=vsock:2:9200 PN_PROXY_PORT=8888 /bin/python3 /opt/pn/incell_mux_proxy.py "
                      ">/tmp/nproxy.out 2>&1 & busybox sleep 2; echo __PS__", "__PS__", 15)
            if self.netz_tor_offen():
                self.bahn_fehlt.pop("netz-tor", None)
                return True
        ok, out = self._run("busybox tail -3 /tmp/nproxy.out 2>/dev/null; echo __NL__", "__NL__", 12)
        grund = " ".join(out.split("__NL__")[0].split())[-200:] or "kein Grund im Protokoll"
        self.bahn_fehlt["netz-tor"] = grund
        try:
            sys.stderr.write("[zelle %s] NETZ-TOR ZU nach %d Versuchen: %s\n"
                             % (self.cell, versuche, grund))
            sys.stderr.flush()
        except Exception:
            pass
        return False

    def netz_tor_sicherstellen(self):

        if not self.alive() or self.net_broker is None:
            return None
        zustand = self.netz_tor_offen()
        if zustand is not False:
            return zustand
        return self._netz_tor_starten(versuche=2)

    def _setup_net(self):

        self._netz_tor_starten()

        self._run("export http_proxy=http://127.0.0.1:8888 https_proxy=http://127.0.0.1:8888 "
                  "HTTP_PROXY=http://127.0.0.1:8888 HTTPS_PROXY=http://127.0.0.1:8888 "
                  "ALL_PROXY=socks5h://127.0.0.1:8888 all_proxy=socks5h://127.0.0.1:8888 "
                  "no_proxy=127.0.0.1,localhost,::1 NO_PROXY=127.0.0.1,localhost,::1; echo __PS__", "__PS__", 10)

        try:
            with open(SONOS_SRC, "rb") as _sf:
                _snb64 = base64.b64encode(_sf.read()).decode()
            _rooms = _sonos_rooms_b64()
            self._run("busybox mkdir -p /usr/bin /etc/pn /opt/pn && "
                      "busybox ln -sf /bin/busybox /usr/bin/env; echo __PS__",
                      "__PS__", 10)
            if _rooms:
                self._stage_atomar("/etc/pn/sonos_rooms.json", _rooms, "__PS__", 10)
            self._stage_atomar("/opt/pn/sonos", _snb64, "__PS__", 15,
                               "chmod +x /opt/pn/sonos && "
                               "busybox ln -sf /opt/pn/sonos /bin/sonos")
        except OSError:
            pass
        _sb = base64.b64encode(_DNS_STUB_SRC.encode()).decode()
        self._stage_atomar(
            "/opt/pn/pn_dns_stub.py", _sb, "__PS__", 12,
            "(/bin/python3 /opt/pn/pn_dns_stub.py >/tmp/dnsstub.out 2>&1 &) ; "
            "printf 'nameserver 127.0.0.1\\noptions timeout:2 attempts:1\\n' "
            "> /etc/resolv.conf 2>/dev/null; busybox sleep 1")
