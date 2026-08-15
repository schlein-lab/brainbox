
from __future__ import annotations

import http.client
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from .model import Cell
from .manager import MicroVMSpec

class VMMError(Exception):
    pass

class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self._unix_path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._unix_path)
        self.sock = s

def _api(sock_path: str, method: str, route: str, body: Optional[dict] = None) -> tuple[int, str]:

    payload = json.dumps(body) if body is not None else None
    try:
        conn = _UnixHTTP(sock_path, timeout=10)
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, route, body=payload, headers=headers)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        conn.close()
        return resp.status, text
    except (OSError, http.client.HTTPException) as e:
        raise VMMError(f"Firecracker API {method} {route} failed: {e}") from e

def _api_ok(sock_path, method, route, body=None):
    st, txt = _api(sock_path, method, route, body)
    if st >= 300:
        raise VMMError(f"Firecracker API {method} {route} -> HTTP {st}: {txt}")
    return txt

@dataclass(frozen=True)
class VMMConfig:
    firecracker_bin: str
    jailer_bin: str
    kernel_path: str
    base_rootfs_path: str
    chroot_base: str = "/srv/pn-cells"
    tenants_parent_cgroup: str = "pn-tenants.slice"
    net_base_octet: int = 200
    gid: int = 0

    def require(self) -> "VMMConfig":
        if not os.path.exists("/dev/kvm"):
            raise VMMError("/dev/kvm absent — real cells need KVM (this box or a nested VM). "
                           "Refusing to pretend.")
        for p in (self.firecracker_bin, self.jailer_bin, self.kernel_path, self.base_rootfs_path):
            if not os.path.exists(p):
                raise VMMError(f"required path missing: {p}")
        return self

@dataclass
class CellHandle:
    cell_id: str
    tenant: str
    pid: int
    api_sock: str
    jail_root: str
    cgroup_dir: str
    jailer_uid: int
    tap: Optional[str] = None
    host_ip: Optional[str] = None
    guest_ip: Optional[str] = None
    state: str = "running"
    proc: object = None

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("cell_id", "tenant", "pid", "api_sock", "jail_root", "cgroup_dir",
                 "jailer_uid", "tap", "host_ip", "guest_ip", "state")}

def _run(cmd, **kw):

    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise VMMError(f"cmd {' '.join(cmd)} failed ({p.returncode}): {p.stderr.strip()}")
    return p.stdout

_CID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def _validate_cid(cid) -> str:

    if not isinstance(cid, str) or not _CID_RE.match(cid):
        raise VMMError(f"invalid cell id {cid!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return cid

def _assert_within(base: str, path: str) -> None:

    b = os.path.realpath(base)
    p = os.path.realpath(path)
    if p != b and not p.startswith(b + os.sep):
        raise VMMError(f"path {path!r} escapes {base!r}")

class FirecrackerVMM:

    def __init__(self, cfg: VMMConfig):
        self.cfg = cfg.require()
        self._cells: dict[str, CellHandle] = {}
        self._net_idx: dict[str, int] = {}

    def _alloc_idx(self, cell_id: str) -> int:

        if cell_id in self._net_idx:
            return self._net_idx[cell_id]
        used = set(self._net_idx.values())
        for i in range(256):
            if i not in used:
                self._net_idx[cell_id] = i
                return i
        raise VMMError("cell net pool exhausted (256 /30 subnets in use)")

    def _alloc_net(self, cell_id: str, uid: int, gid: int) -> tuple[str, str, str, str]:

        idx = self._alloc_idx(cell_id)
        b = self.cfg.net_base_octet
        tap = f"fctap{idx}"
        host_ip = f"10.{b}.{idx}.1"
        guest_ip = f"10.{b}.{idx}.2"
        mac = "06:00:%02x:%02x:%02x:%02x" % (b & 0xff, idx & 0xff, 0, 2)

        subprocess.run(["ip", "link", "del", tap], capture_output=True)
        _run(["ip", "tuntap", "add", "dev", tap, "mode", "tap", "user", str(uid), "group", str(gid)])
        _run(["ip", "addr", "add", f"{host_ip}/30", "dev", tap])
        _run(["ip", "link", "set", tap, "up"])
        return tap, f"{host_ip}/30", guest_ip, mac

    def boot(self, cell: Cell, spec: MicroVMSpec, *, with_net: bool = True) -> CellHandle:

        cid = _validate_cid(cell.id)
        if cid in self._cells:
            raise VMMError(f"cell {cid!r} already booted")
        cfg = self.cfg
        env = cell.envelope
        vcpus = max(1, math.ceil(env.cpu_pct / 100))
        cpu_period = 100000
        cpu_quota = max(1000, int(env.cpu_pct / 100 * cpu_period))

        jail_root = os.path.join(cfg.chroot_base, "firecracker", cid, "root")
        cgroup_dir = os.path.join("/sys/fs/cgroup", cfg.tenants_parent_cgroup, "firecracker", cid)
        _assert_within(os.path.join(cfg.chroot_base, "firecracker"), jail_root)
        _assert_within(os.path.join("/sys/fs/cgroup", cfg.tenants_parent_cgroup, "firecracker"), cgroup_dir)
        tap = host_cidr = guest_ip = guest_mac = None
        proc = None
        try:

            if os.path.isdir(os.path.dirname(jail_root)):
                shutil.rmtree(os.path.dirname(jail_root), ignore_errors=True)
            os.makedirs(jail_root, exist_ok=True)
            kern_in = os.path.join(jail_root, "vmlinux")
            root_in = os.path.join(jail_root, "rootfs.ext4")
            shutil.copyfile(cfg.kernel_path, kern_in)
            shutil.copyfile(cfg.base_rootfs_path, root_in)
            os.chown(jail_root, spec.jailer_uid, cfg.gid)
            os.chown(kern_in, spec.jailer_uid, cfg.gid)
            os.chown(root_in, spec.jailer_uid, cfg.gid)

            if with_net:
                tap, host_cidr, guest_ip, guest_mac = self._alloc_net(cid, spec.jailer_uid, cfg.gid)

            cg_parent = cfg.tenants_parent_cgroup
            jail_cmd = [
                cfg.jailer_bin,
                "--id", cid,
                "--exec-file", cfg.firecracker_bin,
                "--uid", str(spec.jailer_uid),
                "--gid", str(cfg.gid),
                "--chroot-base-dir", cfg.chroot_base,
                "--cgroup-version", "2",
                "--parent-cgroup", cg_parent,
                "--cgroup", f"memory.max={env.mem_bytes}",
                "--cgroup", f"cpu.max={cpu_quota} {cpu_period}",
                "--cgroup", f"pids.max={env.pids}",
                "--",
                "--api-sock", "/run/firecracker.socket",
            ]
            proc = subprocess.Popen(jail_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            api_sock = os.path.join(jail_root, "run", "firecracker.socket")
            self._wait_for_socket(api_sock, proc, timeout=8)

            mem_mib_cap = max(1, env.mem_bytes // (1024 * 1024))
            guest_mib = max(16, mem_mib_cap - 48)
            _api_ok(api_sock, "PUT", "/machine-config",
                    {"vcpu_count": vcpus, "mem_size_mib": guest_mib, "smt": False})
            cmdline = spec.cmdline
            if with_net:

                cmdline += f" ip={guest_ip}::{host_cidr.split('/')[0]}:255.255.255.252::eth0:off"
            cmdline += f" CELL_ID={cid} TENANT={cell.tenant}"
            _api_ok(api_sock, "PUT", "/boot-source",
                    {"kernel_image_path": "vmlinux", "boot_args": cmdline})
            _api_ok(api_sock, "PUT", "/drives/rootfs",
                    {"drive_id": "rootfs", "path_on_host": "rootfs.ext4",
                     "is_root_device": True, "is_read_only": False})
            if with_net:
                _api_ok(api_sock, "PUT", "/network-interfaces/eth0",
                        {"iface_id": "eth0", "host_dev_name": tap, "guest_mac": guest_mac})
            _api_ok(api_sock, "PUT", "/actions", {"action_type": "InstanceStart"})

            cgroup_dir = self._discover_cgroup(proc.pid) or cgroup_dir

            h = CellHandle(cell_id=cid, tenant=cell.tenant, pid=proc.pid, api_sock=api_sock,
                           jail_root=jail_root, cgroup_dir=cgroup_dir, jailer_uid=spec.jailer_uid,
                           tap=tap, host_ip=(host_cidr.split("/")[0] if host_cidr else None),
                           guest_ip=guest_ip, state="running", proc=proc)
            self._cells[cid] = h
            return h
        except Exception as e:

            self._cgroup_kill(cgroup_dir)
            self._reap_proc(proc)
            self._teardown(cid, jail_root, cgroup_dir, tap)
            self._net_idx.pop(cid, None)
            raise VMMError(f"boot of cell {cid!r} failed: {e}") from e

    def _wait_for_socket(self, sock_path, proc, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(sock_path):
                return
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                raise VMMError(f"jailer/firecracker exited early ({proc.returncode}): {err[-400:]}")
            time.sleep(0.05)
        raise VMMError(f"api socket {sock_path} never appeared within {timeout}s")

    def freeze(self, cell_id: str) -> None:
        h = self._require(cell_id)
        _api_ok(h.api_sock, "PATCH", "/vm", {"state": "Paused"})
        h.state = "frozen"

    def resume(self, cell_id: str) -> None:
        h = self._require(cell_id)
        _api_ok(h.api_sock, "PATCH", "/vm", {"state": "Resumed"})
        h.state = "running"

    def stop(self, cell_id: str) -> None:

        h = self._cells.get(cell_id)
        if h is None:
            return
        try:
            _api(h.api_sock, "PUT", "/actions", {"action_type": "SendCtrlAltDel"})
        except VMMError:
            pass
        self._cgroup_kill(h.cgroup_dir)
        self._reap_proc(h.proc)
        self._teardown(cell_id, os.path.dirname(h.jail_root), h.cgroup_dir, h.tap)
        self._net_idx.pop(cell_id, None)
        h.pid = None
        h.state = "stopped"
        self._cells.pop(cell_id, None)

    def destroy(self, cell_id: str) -> None:

        h = self._cells.get(cell_id)
        if h is None:
            return
        self._cgroup_kill(h.cgroup_dir)
        self._reap_proc(h.proc)
        self._teardown(cell_id, os.path.dirname(h.jail_root), h.cgroup_dir, h.tap)
        self._net_idx.pop(cell_id, None)
        h.state = "destroyed"
        self._cells.pop(cell_id, None)

    def cgroup_stats(self, cell_id: str) -> dict:

        h = self._require(cell_id)
        out = {}
        for f in ("memory.max", "memory.current", "cpu.max", "pids.max", "pids.current"):
            p = os.path.join(h.cgroup_dir, f)
            try:
                out[f] = open(p).read().strip()
            except OSError:
                out[f] = None
        return out

    def proc_owner_uid(self, cell_id: str) -> int:

        h = self._require(cell_id)
        if h.pid is None:
            raise VMMError(f"cell {cell_id!r} is not running")
        st = os.stat(f"/proc/{h.pid}")
        return st.st_uid

    def handle(self, cell_id: str) -> CellHandle:
        return self._require(cell_id)

    def _require(self, cell_id: str) -> CellHandle:
        h = self._cells.get(cell_id)
        if h is None:
            raise VMMError(f"no live cell {cell_id!r}")
        return h

    @staticmethod
    def _discover_cgroup(pid: int) -> Optional[str]:

        try:
            for row in open(f"/proc/{pid}/cgroup").read().splitlines():
                parts = row.split(":", 2)
                if len(parts) == 3 and (parts[0] == "0" or parts[1] == ""):
                    return os.path.join("/sys/fs/cgroup", parts[2].lstrip("/"))
        except OSError:
            pass
        return None

    def _reap_proc(self, proc):

        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass

    def _cgroup_kill(self, cgroup_dir):

        if not cgroup_dir or not os.path.isdir(cgroup_dir):
            return
        try:
            open(os.path.join(cgroup_dir, "cgroup.kill"), "w").write("1")
        except OSError:
            try:
                for line in open(os.path.join(cgroup_dir, "cgroup.procs")):
                    try:
                        os.kill(int(line.strip()), signal.SIGKILL)
                    except (ProcessLookupError, ValueError):
                        pass
            except OSError:
                pass
        for _ in range(60):
            try:
                if not open(os.path.join(cgroup_dir, "cgroup.procs")).read().strip():
                    return
            except OSError:
                return
            time.sleep(0.05)

    def _teardown(self, cell_id, jail_parent, cgroup_dir, tap):
        if tap:
            subprocess.run(["ip", "link", "del", tap], capture_output=True)
        if cgroup_dir and os.path.isdir(cgroup_dir):
            try:
                os.rmdir(cgroup_dir)
            except OSError:
                pass
        if jail_parent and os.path.isdir(jail_parent):
            shutil.rmtree(jail_parent, ignore_errors=True)

__all__ = ["FirecrackerVMM", "VMMConfig", "CellHandle", "VMMError"]
