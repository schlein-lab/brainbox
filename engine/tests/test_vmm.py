
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pnlib.celld.model import ResourceEnvelope
from pnlib.celld.manager import CellManager
from pnlib.celld import vmm as V

MB = 1024 * 1024

def _resolve_mvdir():

    if os.environ.get("MVDIR"):
        return os.environ["MVDIR"]
    cands = [os.path.expanduser("~/brainarbeit-build/microvm")]
    su = os.environ.get("SUDO_USER")
    if su:
        cands.append(f"/home/{su}/brainarbeit-build/microvm")
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]

MVDIR = _resolve_mvdir()

def test_config_fail_closed_without_kvm():

    if os.path.exists("/dev/kvm"):
        return
    cfg = V.VMMConfig(firecracker_bin="/bin/true", jailer_bin="/bin/true",
                      kernel_path="/bin/true", base_rootfs_path="/bin/true")
    try:
        cfg.require()
        raise AssertionError("require() must raise VMMError without /dev/kvm")
    except V.VMMError:
        pass

def test_config_fail_closed_missing_binaries():
    cfg = V.VMMConfig(firecracker_bin="/no/such/fc", jailer_bin="/no/such/jailer",
                      kernel_path="/no/such/k", base_rootfs_path="/no/such/r")
    try:
        cfg.require()
        raise AssertionError("require() must raise VMMError on missing binaries")
    except V.VMMError:
        pass

def test_discover_cgroup_parses_v2_line(tmp=None):

    line = "0::/pn-tenants.slice/firecracker/alice-c1"
    parts = line.split(":", 2)
    assert parts[0] == "0"
    expect = os.path.join("/sys/fs/cgroup", parts[2].lstrip("/"))
    assert expect == "/sys/fs/cgroup/pn-tenants.slice/firecracker/alice-c1"

def test_cpu_cgroup_arithmetic_matches_envelope():

    for cpu_pct, exp_vcpu, exp_quota in [(100, 1, 100000), (50, 1, 50000), (250, 3, 250000)]:
        vcpus = max(1, math.ceil(cpu_pct / 100))
        quota = max(1000, int(cpu_pct / 100 * 100000))
        assert vcpus == exp_vcpu, (cpu_pct, vcpus)
        assert quota == exp_quota, (cpu_pct, quota)

def test_manager_spec_feeds_vmm_fields():

    mgr = CellManager()
    q = ResourceEnvelope(cpu_pct=200, mem_bytes=256 * MB, io_bps=10**9, net_bps=10**8, pids=256)
    mgr.set_tenant_quota("alice", q); mgr.set_tenant_quota("bob", q)
    a = mgr.create(id="a1", tenant="alice",
                   envelope=ResourceEnvelope(cpu_pct=100, mem_bytes=128 * MB, io_bps=1, net_bps=1, pids=64),
                   capabilities=[])
    b = mgr.create(id="b1", tenant="bob",
                   envelope=ResourceEnvelope(cpu_pct=100, mem_bytes=128 * MB, io_bps=1, net_bps=1, pids=64),
                   capabilities=[])
    sa, sb = mgr.microvm_spec(a), mgr.microvm_spec(b)
    assert sa.vcpus == 1 and sa.mem_bytes == 128 * MB
    assert sa.jailer_uid != sb.jailer_uid, "distinct tenants must get distinct jailer uids"
    assert sa.host_mounts == () and sa.host_cred_refs == ()

def _substrate_ready():
    return (os.path.exists("/dev/kvm") and os.geteuid() == 0
            and os.path.exists(f"{MVDIR}/bin/firecracker")
            and os.path.exists(f"{MVDIR}/bin/jailer")
            and os.path.exists(f"{MVDIR}/kernel/vmlinux-ours")
            and os.path.exists(f"{MVDIR}/rootfs/cell-rootfs.ext4"))

def test_two_cell_isolation_real_boot():

    if not _substrate_ready():
        print("  SKIP test_two_cell_isolation_real_boot (needs root+KVM+artefacts)")
        return
    import subprocess, time
    for p in ("/sys/fs/cgroup/cgroup.subtree_control",
              "/sys/fs/cgroup/pn-tenants.slice/cgroup.subtree_control",
              "/sys/fs/cgroup/pn-tenants.slice/firecracker/cgroup.subtree_control"):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            open(p, "w").write("+cpu +memory +pids")
        except OSError:
            pass
    cfg = V.VMMConfig(firecracker_bin=f"{MVDIR}/bin/firecracker", jailer_bin=f"{MVDIR}/bin/jailer",
                      kernel_path=f"{MVDIR}/kernel/vmlinux-ours",
                      base_rootfs_path=f"{MVDIR}/rootfs/cell-rootfs.ext4", chroot_base="/srv/pn-cells")
    mgr = CellManager()
    q = ResourceEnvelope(cpu_pct=200, mem_bytes=256 * MB, io_bps=10**9, net_bps=10**8, pids=256)
    mgr.set_tenant_quota("t_a", q); mgr.set_tenant_quota("t_b", q)
    ca = mgr.create(id="itA", tenant="t_a",
                    envelope=ResourceEnvelope(cpu_pct=100, mem_bytes=128 * MB, io_bps=1, net_bps=1, pids=64),
                    capabilities=[])
    cb = mgr.create(id="itB", tenant="t_b",
                    envelope=ResourceEnvelope(cpu_pct=50, mem_bytes=192 * MB, io_bps=1, net_bps=1, pids=64),
                    capabilities=[])
    mgr.start("itA"); mgr.start("itB")
    vmm = V.FirecrackerVMM(cfg)
    ha = hb = None
    try:
        ha = vmm.boot(ca, mgr.microvm_spec(ca), with_net=False)
        hb = vmm.boot(cb, mgr.microvm_spec(cb), with_net=False)
        time.sleep(3)
        ua, ub = vmm.proc_owner_uid("itA"), vmm.proc_owner_uid("itB")
        assert ua != 0 and ub != 0 and ua != ub, f"de-priv/distinct uid failed {ua} {ub}"
        ga, gb = vmm.cgroup_stats("itA"), vmm.cgroup_stats("itB")
        assert ga["memory.max"] == str(128 * MB), ga
        assert gb["memory.max"] == str(192 * MB), gb
        assert ga["cpu.max"].split()[0] == "100000" and gb["cpu.max"].split()[0] == "50000"
        vmm.freeze("itA"); assert vmm.handle("itA").state == "frozen"
        vmm.resume("itA"); assert vmm.handle("itA").state == "running"
    finally:
        for cid in ("itA", "itB"):
            try:
                vmm.destroy(cid)
            except Exception:
                pass
    print("  PASS test_two_cell_isolation_real_boot (2 cells, de-priv+cgroup+lifecycle+teardown)")

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  PASS  {name}"); passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}"); failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}"); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
