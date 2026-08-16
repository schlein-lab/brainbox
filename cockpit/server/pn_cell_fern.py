#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pn_cell_basis import (
    ADOPT_WAIT_S,
    BIN,
    BROKER,
    INITRD,
    KERNEL,
    OFFICE_VCPUS,
    PORTAL_BROKER,
    _ADMIT,
    _AdoptedProc,
    _file_sha256,
    _read_proc_stat,
    _sock_lebt)

_NODE_CELLS_TTL_S = float(os.environ.get("PN_NODE_CELLS_TTL_S", "3") or 0)
_NODE_CELLS_TIMEOUT_S = float(os.environ.get("PN_NODE_CELLS_TIMEOUT_S", "4") or 4)
_NODE_CELLS_NOLIST_BACKOFF_S = 600.0
_node_status_lock = threading.Lock()
_node_status = {}
_node_status_thread = [False]

def _node_status_loop():

    while True:
        try:
            _node_status_refresh()
        except Exception:
            pass
        time.sleep(max(1.0, _NODE_CELLS_TTL_S))

def _node_status_thread_start():
    if _node_status_thread[0] or _NODE_CELLS_TTL_S <= 0:
        return
    with _node_status_lock:
        if _node_status_thread[0]:
            return
        _node_status_thread[0] = True
    threading.Thread(target=_node_status_loop, name="node-cells-status", daemon=True).start()

def _node_status_entry(endpoint, token=None):
    with _node_status_lock:
        st = _node_status.get(endpoint)
        if st is None:
            st = {"ts": 0.0, "cells": None, "err": None, "nolist_until": 0.0,
                  "token": token, "busy": None}
            _node_status[endpoint] = st
        if token is not None:
            st["token"] = token
    _node_status_thread_start()
    return st

def _node_cells_fetch(endpoint, token):

    import urllib.request
    import urllib.error
    req = urllib.request.Request(endpoint.rstrip("/") + "/cells",
                                 headers={"X-Node-Token": token or ""})
    try:
        with urllib.request.urlopen(req, timeout=_NODE_CELLS_TIMEOUT_S) as r:
            obj = json.loads(r.read().decode("utf-8"))
        rows = obj.get("cells") if isinstance(obj, dict) else None
        if not isinstance(rows, list):
            return None, "unerwartete Antwort"
        return {str(c.get("cell_id")): c for c in rows
                if isinstance(c, dict) and c.get("cell_id")}, None
    except urllib.error.HTTPError as e:
        if int(getattr(e, "code", 0) or 0) == 404:
            return None, "nolist"
        return None, "HTTP %s" % getattr(e, "code", "?")
    except Exception as e:
        return None, e.__class__.__name__

def _node_status_refresh():

    now = time.time()
    jobs = []
    with _node_status_lock:
        for ep, st in _node_status.items():
            if st.get("busy") is not None:

                if now - st.get("busy_since", now) < 60.0:
                    continue
                st["busy"] = None
            if st.get("nolist_until", 0) > now:
                continue
            if (now - st.get("ts", 0)) < _NODE_CELLS_TTL_S:
                continue
            ev = threading.Event()
            st["busy"] = ev
            st["busy_since"] = now
            jobs.append((ep, st.get("token"), ev))
    if not jobs:
        return

    def _one(ep, tok, ev):
        cells, err = _node_cells_fetch(ep, tok)
        with _node_status_lock:
            st = _node_status.get(ep)
            if st is not None:
                st["ts"] = time.time()
                if err == "nolist":
                    st["nolist_until"] = time.time() + _NODE_CELLS_NOLIST_BACKOFF_S
                    st["err"] = None
                elif err is not None:
                    st["err"] = err
                else:
                    st["cells"] = cells
                    st["err"] = None
                if st.get("busy") is ev:
                    st["busy"] = None
        ev.set()

    if len(jobs) == 1:
        _one(*jobs[0])
        return
    ths = [threading.Thread(target=_one, args=j, daemon=True, name="node-cells-bulk") for j in jobs]
    for t in ths:
        t.start()
    for t in ths:
        t.join(_NODE_CELLS_TIMEOUT_S + 1.0)

def _node_cells_get(endpoint, token, cell_id):

    now = time.time()
    st = _node_status_entry(endpoint, token)
    with _node_status_lock:
        if st.get("nolist_until", 0) > now:
            return "nolist", None
        ts = st.get("ts", 0)
        busy = st.get("busy")
    if (now - ts) > 3 * _NODE_CELLS_TTL_S:
        if busy is None:
            _node_status_refresh()
        else:
            busy.wait(_NODE_CELLS_TIMEOUT_S + 1.0)
    with _node_status_lock:
        if st.get("nolist_until", 0) > now:
            return "nolist", None
        cells = st.get("cells")
        if isinstance(cells, dict) and not st.get("err") \
                and (time.time() - st.get("ts", 0)) <= 3 * _NODE_CELLS_TTL_S:
            e = cells.get(str(cell_id))
            if e is None:
                return "gone", None
            return ("running" if e.get("state") == "running" else "exited"), e
        return "error", None

def _node_cells_snapshot(endpoint, token):

    now = time.time()
    st = _node_status_entry(endpoint, token)
    with _node_status_lock:
        if st.get("nolist_until", 0) > now:
            return None
        ts = st.get("ts", 0)
        busy = st.get("busy")
    if (now - ts) > _NODE_CELLS_TTL_S:
        if busy is None:
            _node_status_refresh()
        else:
            busy.wait(_NODE_CELLS_TIMEOUT_S + 1.0)
    with _node_status_lock:
        if st.get("nolist_until", 0) > now:
            return None
        cells = st.get("cells")
        if isinstance(cells, dict) and not st.get("err") \
                and (time.time() - st.get("ts", 0)) <= 3 * _NODE_CELLS_TTL_S:
            return dict(cells)
        return None

def fern_zellen_uebersicht():

    wj = os.path.expanduser("~/.local/share/brainbox-portal/workers.json")
    if not os.path.exists(wj):
        return {}, True
    try:
        import portal_placement as _pp
        knoten = [n for n in (_pp._worker_nodes() or []) if n.get("can_cells")]
    except Exception:
        return {}, False
    zellen = {}
    alle_frisch = True
    for n in knoten:
        nid = n.get("id")
        try:
            ep, tok = _pp.node_endpoint(nid), _pp.node_token(nid)
        except Exception:
            ep = tok = None
        if not ep or not tok:
            alle_frisch = False
            continue
        cells = _node_cells_snapshot(ep, tok)
        if cells is None:
            alle_frisch = False
            continue
        for cid_, e in cells.items():
            alt = zellen.get(cid_)
            if alt is not None and ((alt.get("eintrag") or {}).get("state") == "running"
                                    or not isinstance(e, dict) or e.get("state") != "running"):
                continue
            zellen[cid_] = {"node": nid, "endpoint": ep, "token": tok,
                            "eintrag": e if isinstance(e, dict) else {}}
    return zellen, alle_frisch

def node_status_info():

    out = {}
    now = time.time()
    with _node_status_lock:
        for ep, st in _node_status.items():
            cells = st.get("cells")
            out[ep] = {"ok": bool(isinstance(cells, dict) and not st.get("err")),
                       "err": st.get("err"),
                       "age_s": round(now - (st.get("ts") or 0), 1) if st.get("ts") else None,
                       "cells_n": (len(cells) if isinstance(cells, dict) else None),
                       "nolist": bool(st.get("nolist_until", 0) > now)}
    return out

class RemoteVmm:

    def __init__(self, endpoint, token, cell_id, pid, cid):
        self.endpoint = str(endpoint).rstrip("/")
        self._token = token
        self.cell_id = cell_id
        self.pid = int(pid) if pid else -1
        self.cid = cid
        self.returncode = None
        self._last_tail = ""
        _node_status_entry(self.endpoint, token)

    def _get(self):
        import urllib.request
        import urllib.error
        req = urllib.request.Request(self.endpoint + "/cells/" + self.cell_id,
                                     headers={"X-Node-Token": self._token})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"state": "gone"}

    def _status_cached(self):

        now = time.time()
        c = getattr(self, "_st_cache", None)
        if c is not None and (now - c[0]) < _NODE_CELLS_TTL_S:
            return c[1]
        try:
            d = self._get()
        except Exception:
            return None
        if _NODE_CELLS_TTL_S > 0:
            self._st_cache = (now, d)
        return d

    def poll(self):

        if self.returncode is not None:
            return self.returncode
        if _NODE_CELLS_TTL_S <= 0:
            zustand = "nolist"
        else:
            zustand, _e = _node_cells_get(self.endpoint, self._token, self.cell_id)
        if zustand == "running":
            return None
        if zustand == "error":
            return None
        if zustand == "nolist":
            d = self._status_cached()
            if d is None:
                return None
        else:
            try:
                d = self._get()
            except Exception:
                return None
        if d.get("state") in ("exited", "gone"):
            rc = d.get("rc")
            self.returncode = rc if isinstance(rc, int) else -1
            self._last_tail = d.get("vmm_err_tail") or self._last_tail
            self._last_out = d.get("vmm_out_tail") or getattr(self, "_last_out", "")
            return self.returncode
        self._last_tail = d.get("vmm_err_tail") or self._last_tail
        self._last_out = d.get("vmm_out_tail") or getattr(self, "_last_out", "")
        return None

    def err_tail(self):
        try:
            d = self._get()
            self._last_tail = d.get("vmm_err_tail") or self._last_tail
        except Exception:
            pass
        return self._last_tail

    def out_tail(self):

        try:
            d = self._get()
            self._last_out = d.get("vmm_out_tail") or getattr(self, "_last_out", "")
        except Exception:
            pass
        return getattr(self, "_last_out", "")

    def wait(self, timeout=None):
        t0 = time.time()
        while self.poll() is None:
            if timeout is not None and time.time() - t0 >= timeout:
                raise subprocess.TimeoutExpired("pn-vmm(remote)", timeout)
            time.sleep(0.3)
        return self.returncode

    def _stop(self):
        import urllib.request
        req = urllib.request.Request(self.endpoint + "/cells/" + self.cell_id + "/stop",
                                     data=b"{}", method="POST",
                                     headers={"X-Node-Token": self._token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
        except Exception:
            pass
        if self.returncode is None:
            self.returncode = -1

    def terminate(self):
        self._stop()

    def kill(self):
        self._stop()

    def send_signal(self, sig):
        self._stop()

class CellFernMixin:

    @staticmethod
    def _load_or_make_adopt_token(d):

        tf = os.path.join(d, "adopt.token")
        try:
            if os.path.exists(tf):
                t = open(tf).read().strip()
                if t:
                    return t
        except OSError:
            pass
        t = os.urandom(32).hex()
        try:
            fd = os.open(tf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try: os.write(fd, t.encode())
            finally: os.close(fd)
        except OSError:
            pass
        return t

    def _reclaim_own_vmm(self):

        try:
            if not os.path.exists(self.meta_file):
                return
            pid = json.load(open(self.meta_file)).get("vmm_pid")
            if not pid:
                return
            try:
                comm = open("/proc/%d/comm" % int(pid)).read().strip()
            except (OSError, ValueError):
                return
            if comm != "pn-vmm":
                return
            os.kill(int(pid), 9)
            time.sleep(0.2)
        except Exception:
            pass

    def _adopt_connect(self, sockpath):

        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(ADOPT_WAIT_S)
            c.connect(sockpath)
            c.sendall((self.adopt_token + "\n").encode())
            ack = b""; t0 = time.time()
            while b"PNADOPTOK" not in ack and time.time() - t0 < ADOPT_WAIT_S:
                try: d = c.recv(64)
                except socket.timeout: break
                if not d: break
                ack += d
            if b"PNADOPTOK" not in ack:
                try: c.close()
                except OSError: pass
                return None
            c.settimeout(None)
            return c
        except OSError:
            return None

    @staticmethod
    def _peer_pid_starttime(sock):

        try:
            import struct
            creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, _uid, _gid = struct.unpack("3i", creds)
            if pid <= 0:
                return (None, None)
            st = _read_proc_stat(pid)
            if st is None or st[0] == "Z":
                return (None, None)
            return (pid, st[1])
        except Exception:
            return (None, None)

    def adopt_in_place(self, mem_mb):

        conn = self._adopt_connect(self.seat_adopt_sock)
        if conn is None:
            return False
        pid, starttime = self._peer_pid_starttime(conn)
        if pid is None:
            try: conn.close()
            except OSError: pass
            return False
        self.conn = conn
        self.term_conn = self._adopt_connect(self.term_adopt_sock)
        self.term_srv = None
        self.term_on = bool(self.term_conn)
        self.proc = _AdoptedProc(pid, starttime)
        self.booted = self.last = time.time()
        if _ADMIT is not None:
            try:
                _ADMIT.reserve(self._admit_id, "session", int(mem_mb), pid,
                               owner=self.principal, session=self.session, label=self.cell)
            except Exception:
                pass

        self._pn_register(int(mem_mb))
        return True

    def fern_broker_sicherstellen(self):

        if self._remote_node() is None:
            return []
        neu = []
        with self._lock:
            pol = self.policy or {}

            self._write_policy_file(pol)
            if not _sock_lebt(self.llm_sock):
                try:
                    os.unlink(self.llm_sock)
                except OSError:
                    pass
                benv = dict(os.environ)
                _b = pol.get("llm_budget") or {}
                _mode = _b.get("enabled", "auto")
                if _mode == "auto":
                    _on = (pol.get("llm_source") or "subscription") == "api_key"
                else:
                    _on = bool(_mode)
                if _on:
                    benv["PN_LLM_MAX_RPM"] = str(_b.get("rpm", 60))
                    benv["PN_LLM_MAX_REQ"] = str(_b.get("max_req", 0))
                    benv["PN_LLM_MAX_TOKENS"] = str(_b.get("max_tokens", 0))
                _dis = pol.get("disallowed_tools") or []
                _strip = [w for t, w in (("WebSearch", "web_search"), ("WebFetch", "web_fetch"))
                          if t in _dis]
                if _strip:
                    benv["PN_STRIP_SERVER_TOOLS"] = ",".join(_strip)
                benv["PN_POLICY_FILE"] = self.policy_file
                benv["PN_PRINCIPAL"] = str(self.principal)
                benv["PN_SESSION_CELL"] = self.cell
                benv["PN_SESSION_JOB_FILE"] = self.pnjob_file
                try:
                    self.broker = subprocess.Popen(
                        ["/usr/bin/python3", BROKER, "--unix-mux", self.llm_sock],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=benv, start_new_session=True)
                    t0 = time.time()
                    while not os.path.exists(self.llm_sock) and time.time() - t0 < 10:
                        time.sleep(0.1)
                    neu.append("llm")
                except OSError as e:
                    self._readopt_grund = "LLM-Broker-Neuanlage fehlgeschlagen: %s" % e
            if not _sock_lebt(self.net_sock):
                try:
                    os.unlink(self.net_sock)
                except OSError:
                    pass
                nenv = dict(os.environ)
                nenv["PN_POLICY_FILE"] = self.policy_file
                nenv["PN_PRINCIPAL"] = str(self.principal)
                nenv["PN_SESSION_CELL"] = self.cell
                nenv.setdefault("PN_LLMD_SOCK", os.path.join(
                    os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
                    "pn-llmd.sock"))
                try:
                    self.net_broker = subprocess.Popen(
                        self._net_broker_cmd(pol, nenv),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=nenv, start_new_session=True)
                    t0 = time.time()
                    while not os.path.exists(self.net_sock) and time.time() - t0 < 10:
                        time.sleep(0.1)
                    neu.append("net")
                except OSError:
                    pass
            portal_moeglich = bool(self.portal_token and self.portal_url) \
                and pol.get("portal_enabled", True)
            if portal_moeglich and not _sock_lebt(self.portal_sock):
                try:
                    os.unlink(self.portal_sock)
                except OSError:
                    pass
                penv = dict(os.environ)
                penv["PN_PORTAL_URL"] = self.portal_url
                penv["PN_PORTAL_TOKEN"] = self.portal_token
                penv["PN_SESSION_SID"] = str(self.session)
                penv["PN_ALLOWED_VERBS"] = ",".join(pol.get("portal_verbs", ["*"]) or [])
                penv["PN_ALLOW_STATE"] = "1" if pol.get("portal_state", "allow") == "allow" else "0"
                penv["PN_ALLOWED_DISPLAYS"] = ",".join(pol.get("displays", []) or [])
                penv["PN_ALLOWED_DEVICES"] = ",".join(pol.get("devices", []) or [])
                penv["PN_DEVICE_CONNECT"] = pol.get("device_connect", "deny")
                penv["PN_FS_READ"] = json.dumps(pol.get("fs_read", []) or [])
                penv["PN_FS_WRITE"] = json.dumps(pol.get("fs_write", []) or [])
                penv["PN_PRINCIPAL"] = str(self.principal)
                penv["PN_SESSION_CELL"] = self.cell
                penv["PN_COMPUTE_ENABLED"] = "1" if pol.get("compute_enabled") else "0"
                penv["PN_COMPUTE_MEM_MAX_MIB"] = str(int(pol.get("compute_mem_max_mib") or 0))
                penv["PN_COMPUTE_CPU_MAX_PCT"] = str(int(pol.get("compute_cpu_max_pct") or 0))
                penv["PN_COMPUTE_TIMEOUT_MAX_S"] = str(int(pol.get("compute_timeout_max_s") or 0))
                penv["PN_COMPUTE_MAX_CONCURRENT"] = str(int(pol.get("compute_max_concurrent") or 0))
                penv["PN_POLICY_FILE"] = self.policy_file
                try:
                    self.portal_broker = subprocess.Popen(
                        ["/usr/bin/python3", PORTAL_BROKER, "--unix-mux", self.portal_sock],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, env=penv, start_new_session=True)
                    t0 = time.time()
                    while not os.path.exists(self.portal_sock) and time.time() - t0 < 10:
                        time.sleep(0.1)
                    neu.append("portal")
                except OSError:
                    pass
        if neu:
            try:
                import sys as _sys
                _sys.stderr.write("[cell-adopt] %s: tote Bahn-Broker neu aufgesetzt (%s)\n"
                                  % (self.cell, ",".join(neu)))
            except Exception:
                pass
        return neu

    def readopt_prepare(self):

        nid = self._remote_node()
        if not nid:
            return None
        endpoint, token = self._node_conn(nid)
        if not endpoint or not token:
            return None
        try:
            st, obj = self._http_json("GET", "%s/cells/%s" % (endpoint, self.cell), token, timeout=15)
        except Exception:
            return None
        if st != 200 or not isinstance(obj, dict) or obj.get("state") != "running":
            return None
        try:
            import pn_cell_remote
            term_reg = pn_cell_remote.get_terminator()
        except Exception:
            return None
        srv = term_srv = None
        try:
            for p in (self.seat_sock, self.term_sock):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.seat_sock); srv.listen(1); srv.settimeout(1.0)
            term_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            term_srv.bind(self.term_sock); term_srv.listen(1); term_srv.settimeout(1.0)
        except OSError:
            for s in (srv, term_srv):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass
            return None

        for lane, sock in (("seat", self.seat_sock), ("term", self.term_sock),
                           ("llm", self.llm_sock), ("net", self.net_sock),
                           ("portal", self.portal_sock)):
            if lane not in ("seat", "term") and not _sock_lebt(sock):
                continue
            try:
                term_reg.register(self.cell, lane, sock, nid, token)
            except Exception:
                pass
        if not _sock_lebt(self.llm_sock):

            for s in (srv, term_srv):
                try:
                    s.close()
                except OSError:
                    pass
            try:
                term_reg.unregister_cell(self.cell)
            except Exception:
                pass
            self._readopt_grund = ("der LLM-Broker dieser Zelle laeuft nicht mehr "
                                   "(Modell-Bahn tot) — sauberer Neustart noetig")
            return None
        return (srv, term_srv, endpoint, token, obj)

    def readopt_finish(self, prepared, mem_mb, deadline):

        srv, term_srv, endpoint, token, obj = prepared
        conn = None
        try:
            while time.time() < deadline:
                try:
                    conn, _ = srv.accept()
                    break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            try:
                srv.close()
            except OSError:
                pass
        if conn is None:
            try:
                term_srv.close()
            except OSError:
                pass
            return False
        self.conn = conn

        ok = False
        try:
            ok, _out = self._run("echo __READOPT_OK__", "__READOPT_OK__", 30)
        except Exception:
            ok = False
        if not ok:
            for s in (conn, term_srv):
                try:
                    s.close()
                except OSError:
                    pass
            self.conn = None
            return False

        try:
            term_srv.settimeout(max(35.0, deadline - time.time()))
            self.term_conn, _ = term_srv.accept()
            self.term_srv = term_srv
            self.term_on = bool(self.term_conn)
        except (socket.timeout, OSError):
            try:
                term_srv.close()
            except OSError:
                pass
            self.term_conn = self.term_srv = None
            self.term_on = False
        self.proc = RemoteVmm(endpoint, token, self.cell, obj.get("pid"), obj.get("cid"))
        self.booted = self.last = time.time()
        self._boot_denied = None

        try:
            self._pn_register(int(mem_mb))
        except Exception:
            pass
        try:
            self._persist_meta()
        except Exception:
            pass
        return True

    def _remote_node(self):

        nid = (self.policy or {}).get("node")
        try:
            import portal_placement as _pp
            local = _pp.LOCAL_ID
        except Exception:
            local = "local"
        if not nid or nid == local:
            return None
        return nid

    def _node_conn(self, nid):

        try:
            import portal_placement as _pp
            return _pp.node_endpoint(nid), _pp.node_token(nid)
        except Exception:
            return None, None

    def _box_lane_host(self):

        h = os.environ.get("PN_CELL_LANE_HOST")
        if h:
            return h
        try:
            import pn_certs
            ip = pn_certs.primary_ipv4()
            if ip:
                return ip
        except Exception:
            pass
        return "127.0.0.1"

    def _http_json(self, method, url, token, body=None, timeout=30):

        import urllib.request
        import urllib.error
        data = None
        headers = {"X-Node-Token": token}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8"))
            except Exception:
                return e.code, None
        except Exception as e:
            return 0, {"error": str(e)[:200]}

    def _stage_image_on_node(self, endpoint, token, path, role):

        iid = os.path.basename(path)
        sha = _file_sha256(path)
        if not sha:
            return False, iid, sha
        import urllib.parse
        qs = urllib.parse.urlencode({"id": iid, "sha256": sha})
        st, obj = self._http_json("GET", "%s/cells/stage?%s" % (endpoint, qs), token, timeout=20)
        if st == 200 and isinstance(obj, dict) and obj.get("present"):
            return True, iid, sha

        import urllib.request
        import urllib.error
        try:
            size = os.path.getsize(path)
        except OSError:
            return False, iid, sha
        qs2 = urllib.parse.urlencode({"id": iid, "sha256": sha, "role": role})
        url = "%s/cells/stage?%s" % (endpoint, qs2)
        with open(path, "rb") as f:
            req = urllib.request.Request(url, data=f, method="POST",
                                         headers={"X-Node-Token": token,
                                                  "Content-Type": "application/octet-stream",
                                                  "Content-Length": str(size)})
            try:
                with urllib.request.urlopen(req, timeout=1800) as r:
                    r.read()
                return True, iid, sha
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                self._boot_denied = "Staging von %s auf den Node fehlgeschlagen: %s" % (iid, e)
                return False, iid, sha

    def _node_image_present(self, endpoint, token, iid, sha):

        import urllib.parse
        qs = urllib.parse.urlencode({"id": iid, "sha256": sha})
        st, obj = self._http_json("GET", "%s/cells/stage?%s" % (endpoint, qs), token, timeout=20)
        return bool(st == 200 and isinstance(obj, dict) and obj.get("present"))

    def _load_arch_manifest(self, arch):

        base = os.path.expanduser("~/.local/share/brainbox-portal")
        p = os.path.join(base, "node-images-%s.json" % arch)
        try:
            with open(p) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def _boot_remote(self, want_mem, blks, ro_env, delta_mb, work_mb, desktop):

        nid = self._remote_node()
        endpoint, token = self._node_conn(nid)
        if not endpoint or not token:
            self._boot_denied = ("Remote-Node „%s“ ist unbekannt oder ohne Token — Zelle kann dort nicht "
                                 "gebootet werden." % nid)
            return None

        try:
            import pn_cell_remote
            term = pn_cell_remote.get_terminator()
            lane_port = term.port
        except Exception as e:
            self._boot_denied = "Box-Lane-Terminator nicht verfuegbar: %s" % e
            return None

        lane_socks = {"seat": self.seat_sock, "llm": self.llm_sock, "term": self.term_sock}
        lanes = ["seat", "llm", "term"]
        if self.net_broker is not None:
            lane_socks["net"] = self.net_sock; lanes.append("net")
        if self.portal_broker is not None:
            lane_socks["portal"] = self.portal_sock; lanes.append("portal")
        if desktop:
            lane_socks["gui"] = self.gui_sock; lanes.append("gui")
        for lane in lanes:
            term.register(self.cell, lane, lane_socks[lane], nid, token)

        def _arch_fam(a):
            a = str(a or "").lower()
            if a.startswith(("aarch64", "arm")):
                return "arm"
            if a in ("x86_64", "amd64", "x64", "i686", "i386"):
                return "x86"
            return a or "unknown"
        import platform
        box_machine = platform.machine()
        node_machine = None
        try:
            import portal_placement as _pp
            node_machine = (_pp.node_by_id(nid) or {}).get("arch")
        except Exception:
            node_machine = None
        cross_arch = bool(node_machine and _arch_fam(node_machine) != _arch_fam(box_machine))
        images = []
        base_path = blks[0]
        if not cross_arch:

            for path, role in ((BIN, "vmm"), (KERNEL, "kernel"), (INITRD, "initrd"), (base_path, "base")):
                ok, iid, sha = self._stage_image_on_node(endpoint, token, path, role)
                if not ok:
                    if not self._boot_denied:
                        self._boot_denied = "Image %s konnte nicht auf den Node gestaged werden." % iid
                    pn_cell_remote.get_terminator().unregister_cell(self.cell)
                    return None
                images.append({"role": role, "id": iid, "sha256": sha})

            n_rw = 1 + (1 if work_mb > 0 else 0)
            for path in blks[1 + n_rw:]:
                ok, iid, sha = self._stage_image_on_node(endpoint, token, path, "extra")
                if not ok:
                    pn_cell_remote.get_terminator().unregister_cell(self.cell)
                    return None
                images.append({"role": "extra", "id": iid, "sha256": sha})
        else:

            man = self._load_arch_manifest(node_machine)
            if not man:
                self._boot_denied = ("Cross-Arch-Boot auf „%s“ (Node-Arch %s, Box-Arch %s): es fehlt das "
                                     "Arch-Image-Manifest ~/.local/share/brainbox-portal/node-images-%s.json "
                                     "— der Node muss vorab seine eigenen Images in seinen CAS seeden."
                                     % (nid, node_machine, box_machine, node_machine))
                pn_cell_remote.get_terminator().unregister_cell(self.cell)
                return None
            for role in ("vmm", "kernel", "initrd", "base"):
                _mk = "office" if (role == "base" and desktop) else role
                ent = man.get(_mk) if isinstance(man.get(_mk), dict) else None
                iid = (ent or {}).get("id")
                sha = (ent or {}).get("sha256")
                if not iid or not sha:
                    self._boot_denied = ("Arch-Image-Manifest node-images-%s.json unvollstaendig: Rolle "
                                         "„%s“ fehlt (id/sha256)." % (node_machine, _mk))
                    pn_cell_remote.get_terminator().unregister_cell(self.cell)
                    return None

                if not self._node_image_present(endpoint, token, iid, sha):
                    self._boot_denied = ("Cross-Arch-Image %s@%s fehlt im CAS von Node „%s“ — der Node ist "
                                         "nicht (korrekt) geseedet; die Box hat die %s-Datei nicht und kann "
                                         "sie nicht nachliefern." % (iid, sha[:12], nid, node_machine))
                    pn_cell_remote.get_terminator().unregister_cell(self.cell)
                    return None
                images.append({"role": role, "id": iid, "sha256": sha})

            n_rw = 1 + (1 if work_mb > 0 else 0)
            extra_paths = blks[1 + n_rw:]
            if extra_paths:
                try:
                    import sys as _sys
                    _sys.stderr.write("[pn-session] %s: cross-arch (%s) — ueberspringe %d extra-Image(s) "
                                      "%s (box-only, nicht node-arch)\n"
                                      % (self.cell, node_machine, len(extra_paths),
                                         ",".join(os.path.basename(p) for p in extra_paths)))
                except Exception:
                    pass

        try:
            import pn_certs
            ca_pem = open(pn_certs.ca_cert_path()).read()
            ca_fp = pn_certs.ca_fingerprint_sha256()
        except Exception as e:
            self._boot_denied = "Box-CA nicht lesbar (Lane-Pinning unmoeglich): %s" % e
            pn_cell_remote.get_terminator().unregister_cell(self.cell)
            return None
        body = {
            "cell_id": self.cell,
            "mem_mb": int(want_mem),
            "vcpus": int((self.policy or {}).get("vcpus") or (OFFICE_VCPUS if desktop else 1)),
            "images": images,
            "kits": (list((self.policy or {}).get("kits") or []) if cross_arch else []),
            "delta_mb": int(delta_mb),
            "work_mb": int(work_mb),
            "adopt_token": self.adopt_token,
            "box_lane": {"host": self._box_lane_host(), "port": int(lane_port),
                         "ca_sha256": ca_fp, "ca_pem": ca_pem},
            "lanes": lanes,
            "env_extra": {},
        }
        st, obj = self._http_json("POST", "%s/cells" % endpoint, token, body=body, timeout=120)
        if st != 200 or not isinstance(obj, dict) or not obj.get("ok"):
            reason = (obj or {}).get("error") if isinstance(obj, dict) else None
            miss = (obj or {}).get("missing") if isinstance(obj, dict) else None
            self._boot_denied = ("Node „%s“ verweigerte /cells (HTTP %s): %s%s"
                                 % (nid, st, reason or "unbekannt",
                                    (" fehlend: %s" % miss) if miss else ""))
            pn_cell_remote.get_terminator().unregister_cell(self.cell)
            return None
        try:
            import sys as _sys
            _sys.stderr.write("[pn-session] %s: REMOTE boot on %s cid=%s pid=%s lanes=%s\n"
                              % (self.cell, nid, obj.get("cid"), obj.get("pid"), ",".join(lanes)))
        except Exception:
            pass
        return RemoteVmm(endpoint, token, self.cell, obj.get("pid"), obj.get("cid"))
