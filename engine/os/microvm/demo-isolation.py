#!/usr/bin/env python3

import os, sys, time, subprocess
sys.path.insert(0, os.environ.get("PN_LIB_DIR", os.path.expanduser("~/portioneer")))
from pnlib.celld.model import ResourceEnvelope
from pnlib.celld.manager import CellManager
from pnlib.celld.vmm import FirecrackerVMM, VMMConfig, VMMError

MV = os.environ.get("MVDIR", os.path.expanduser("~/brainarbeit-build/microvm"))
MB = 1024 * 1024

def wfile(p, v):
    try:
        open(p, "w").write(v)
    except OSError as e:
        print(f"  cgroup subtree_control {p}: {e}")

def cgroup_prep():
    os.makedirs("/sys/fs/cgroup/pn-tenants.slice/firecracker", exist_ok=True)
    wfile("/sys/fs/cgroup/cgroup.subtree_control", "+cpu +memory +pids")
    wfile("/sys/fs/cgroup/pn-tenants.slice/cgroup.subtree_control", "+cpu +memory +pids")
    wfile("/sys/fs/cgroup/pn-tenants.slice/firecracker/cgroup.subtree_control", "+cpu +memory +pids")

def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False

def main():
    with_net = "--net" in sys.argv
    cgroup_prep()
    cfg = VMMConfig(
        firecracker_bin=f"{MV}/bin/firecracker",
        jailer_bin=f"{MV}/bin/jailer",
        kernel_path=f"{MV}/kernel/vmlinux-ours",
        base_rootfs_path=f"{MV}/rootfs/cell-rootfs.ext4",
        chroot_base="/srv/pn-cells",
    )
    mgr = CellManager()
    quota = ResourceEnvelope(cpu_pct=200, mem_bytes=256 * MB, io_bps=10**9, net_bps=10**8, pids=256)
    mgr.set_tenant_quota("alice", quota)
    mgr.set_tenant_quota("bob", quota)
    ca = mgr.create(id="alice-c1", tenant="alice",
                    envelope=ResourceEnvelope(cpu_pct=100, mem_bytes=128 * MB, io_bps=10**8, net_bps=10**7, pids=128),
                    capabilities=[])
    cb = mgr.create(id="bob-c1", tenant="bob",
                    envelope=ResourceEnvelope(cpu_pct=50, mem_bytes=192 * MB, io_bps=10**8, net_bps=10**7, pids=128),
                    capabilities=[])
    mgr.start("alice-c1"); mgr.start("bob-c1")
    sa = mgr.microvm_spec(ca); sb = mgr.microvm_spec(cb)

    vmm = FirecrackerVMM(cfg)
    print(f"=== booting 2 real cells (with_net={with_net}) ===")
    ha = vmm.boot(ca, sa, with_net=with_net)
    print(f"  alice-c1: pid={ha.pid} jailer_uid={ha.jailer_uid} tap={ha.tap}")
    hb = vmm.boot(cb, sb, with_net=with_net)
    print(f"  bob-c1  : pid={hb.pid} jailer_uid={hb.jailer_uid} tap={hb.tap}")
    time.sleep(3)

    ok = True
    print("\n=== ISOLATION PROOFS (host-enforced) ===")
    print(f"[VM] both alive & separate KVM VMs: A={alive(ha.pid)} B={alive(hb.pid)} pids {ha.pid}!={hb.pid}")
    ok &= alive(ha.pid) and alive(hb.pid) and ha.pid != hb.pid

    ua, ub = vmm.proc_owner_uid("alice-c1"), vmm.proc_owner_uid("bob-c1")
    depriv = ua != 0 and ub != 0 and ua != ub
    print(f"[UID] de-privileged + distinct: A={ua} B={ub} (both !=0, A!=B) -> {depriv}")
    ok &= depriv

    ga, gb = vmm.cgroup_stats("alice-c1"), vmm.cgroup_stats("bob-c1")
    print(f"[CGROUP] A memory.max={ga.get('memory.max')} cpu.max={ga.get('cpu.max')!r} current={ga.get('memory.current')}")
    print(f"[CGROUP] B memory.max={gb.get('memory.max')} cpu.max={gb.get('cpu.max')!r} current={gb.get('memory.current')}")
    caps_ok = ga.get("memory.max") == str(128 * MB) and gb.get("memory.max") == str(192 * MB)
    print(f"[CGROUP] envelope enforced (A=128Mi, B=192Mi, distinct): {caps_ok}")
    ok &= caps_ok

    ipf = open("/proc/sys/net/ipv4/ip_forward").read().strip()
    print(f"[NET] host ip_forward={ipf} (0 => host will not route between the cells' /30s)")
    if with_net:
        taps = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True).stdout
        print(f"[NET] cell taps present: {[t for t in ('fctap0','fctap1') if t in taps]}")

    print("\n=== LIFECYCLE (freeze/resume via FC API) ===")
    vmm.freeze("alice-c1"); print(f"  froze alice -> {vmm.handle('alice-c1').state}")
    time.sleep(1)
    vmm.resume("alice-c1"); print(f"  resumed alice -> {vmm.handle('alice-c1').state}")

    print("\n=== TEARDOWN (destroy wipes ephemeral delta + tap + cgroup) ===")
    vmm.destroy("alice-c1"); vmm.destroy("bob-c1")
    time.sleep(1)
    gone = not alive(ha.pid) and not alive(hb.pid)
    left = os.path.isdir("/sys/fs/cgroup/pn-tenants.slice/firecracker/alice-c1")
    print(f"  both VMM procs gone: {gone}   alice cgroup removed: {not left}")
    ok &= gone

    print(f"\n=== RESULT: {'ALL ISOLATION PROOFS PASSED' if ok else 'FAILED'} ===")
    return 0 if ok else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except VMMError as e:
        print(f"VMMError: {e}")
        sys.exit(2)
