#!/usr/bin/env python3

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from pnlib import rootkey, captok
from pnlib.celld import (
    Cell, ResourceEnvelope, CellManager, MicroVMSpec,
    IllegalTransition, QuotaExceeded, AuthorityError,
    CREATED, RUNNING, FROZEN, STOPPED, DESTROYED,
    Topology, check_isolation, isolated_topology, Edge,
    NODE_CONTROL, NODE_CELL, CONTROL_SLICE, TENANTS_SLICE,
    derive_cell_capability, attempt_escalation, verify_cell_capability,
    assert_no_host_creds, must_go_through_broker, guard_in_cell,
)

PASS = 0
FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")

def expect_raise(exc, fn, label):

    global PASS, FAIL
    try:
        fn()
    except exc:
        PASS += 1
        print(f"  ok   {label} (rejected)")
        return
    except Exception as e:
        FAIL += 1
        print(f"  FAIL {label} (wrong exception {type(e).__name__}: {e})")
        return
    FAIL += 1
    print(f"  FAIL {label} (NOT rejected — should have raised {exc.__name__})")

def test_lifecycle():
    print("\n[1] cell lifecycle create->start->freeze->resume->stop->destroy")
    mgr = CellManager()
    env = ResourceEnvelope(cpu_pct=50, mem_bytes=512 * 1024 * 1024, io_bps=10_000_000,
                           net_bps=5_000_000, pids=256)
    c = mgr.create(id="c1", tenant="alice", envelope=env, capabilities={"read", "compute"})
    check(c.state == CREATED, "create -> CREATED")

    check(mgr.start("c1").state == RUNNING, "start: CREATED -> RUNNING")
    check(mgr.freeze("c1").state == FROZEN, "freeze: RUNNING -> FROZEN")
    check(mgr.resume("c1").state == RUNNING, "resume: FROZEN -> RUNNING")
    check(mgr.stop("c1").state == STOPPED, "stop: RUNNING -> STOPPED")
    check(mgr.destroy("c1").state == DESTROYED, "destroy: STOPPED -> DESTROYED")

    mgr2 = CellManager()
    mgr2.create(id="c2", tenant="alice", envelope=env, capabilities={"read"})
    expect_raise(IllegalTransition, lambda: mgr2.freeze("c2"), "freeze before start")
    expect_raise(IllegalTransition, lambda: mgr2.resume("c2"), "resume before start")
    mgr2.start("c2")
    expect_raise(IllegalTransition, lambda: mgr2.start("c2"), "start while RUNNING")
    expect_raise(IllegalTransition, lambda: mgr2.resume("c2"), "resume while RUNNING")
    expect_raise(IllegalTransition, lambda: mgr2.destroy("c2"), "destroy while RUNNING (must stop first)")
    mgr2.stop("c2")
    mgr2.destroy("c2")

    expect_raise(Exception, lambda: mgr2.start("c2"), "act after destroy")

def test_control_plane_separation():
    print("\n[2] control-plane-separation invariant")
    topo = isolated_topology(["alice", "bob"])
    r = check_isolation(topo)
    check(r.ok and not r.violations, "isolated topology PASSES (cells reach only the broker-door)")
    check(topo.nodes["alice"].slice != CONTROL_SLICE, "cell is under the tenant slice, not control.slice")

    bad1 = isolated_topology(["alice", "bob"])
    bad1.add_edge("alice", "pn-celld", "reach")
    r1 = check_isolation(bad1)
    check(not r1.ok, "cell -> control.slice FAILS the invariant")
    check(any("control plane" in v[2] for v in r1.violations), "violation names the control-plane reach")

    bad2 = isolated_topology(["alice", "bob"])
    bad2.add_edge("alice", "bob", "reach")
    r2 = check_isolation(bad2)
    check(not r2.ok, "cell -> sibling cell FAILS the invariant")
    check(any("sibling cell" in v[2] for v in r2.violations), "violation names the sibling reach")

    from pnlib.celld import Node
    bad3 = isolated_topology(["alice"])
    bad3.nodes["alice"] = Node("alice", NODE_CELL, CONTROL_SLICE)
    r3 = check_isolation(bad3)
    check(not r3.ok, "cell placed in control.slice FAILS (wrong slice)")

def test_capability_scoping():
    print("\n[3] capability scoping (<= parent authority) + no host creds")
    priv, pub = rootkey.generate_owner_keypair_offbox()
    box = "box-1"

    parent = captok.mint(
        owner_priv=priv, owner_pub=pub, agent="tenant:alice", audience=box,
        max_redelegation_depth=3,
        caveats=[
            captok.Caveat.scope("cap", ["read", "write", "net"]),
            captok.Caveat.num_leq("cpu", 80),
            captok.Caveat.num_leq("mem", 1024 * 1024 * 1024),
        ],
    )
    parent_grant = captok.verify(parent, owner_pubkey=pub, audience=box)

    env = ResourceEnvelope(cpu_pct=50, mem_bytes=512 * 1024 * 1024, io_bps=1_000_000,
                           net_bps=1_000_000)
    cell = Cell(id="c1", tenant="alice", envelope=env, capabilities={"read"}, parent="tenant:alice")

    tok = derive_cell_capability(parent, cell)
    grant = verify_cell_capability(tok, owner_pubkey=pub, audience=box, parent_grant=parent_grant)
    check(grant.scopes["cap"] == frozenset({"read"}), "derived cell scope narrowed to {read}")
    check(grant.num_caps["cpu"] == 50 and grant.num_caps["cpu"] <= 80, "cell cpu ceiling <= parent")
    check(grant.delegation.agents == ("tenant:alice", "cell:alice"), "delegation chain parent->cell")

    expect_raise(captok.CapTokError,
                 lambda: attempt_escalation(parent, tenant="alice", caps=["read", "admin"]),
                 "mint capability 'admin' the parent lacks")

    expect_raise(captok.CapTokError,
                 lambda: attempt_escalation(parent, tenant="alice", cpu=200),
                 "raise cpu ceiling above the parent")

    assert_no_host_creds(cell)
    check(cell.holds_no_host_creds is True and cell.host_cred_refs == (), "cell holds no host creds")
    expect_raise(AuthorityError,
                 lambda: Cell(id="x", tenant="alice", envelope=env, capabilities={"read"},
                              host_cred_refs=("/etc/shadow",)),
                 "construct a cell holding a host credential")

    check(must_go_through_broker("host.exec"), "host.exec is a brokered/privileged action")
    check(not must_go_through_broker("compute"), "pure compute is allowed in-cell")
    expect_raise(AuthorityError, lambda: guard_in_cell("secret.unseal"),
                 "privileged action attempted in-cell")
    guard_in_cell("compute")
    check(True, "in-cell compute passes the guard")

def test_resource_envelope():
    print("\n[4] resource envelope caps + from-scratch microVM spec")
    mgr = CellManager()
    quota = ResourceEnvelope(cpu_pct=100, mem_bytes=2 * 1024 * 1024 * 1024,
                             io_bps=50_000_000, net_bps=20_000_000, pids=1024)
    mgr.set_tenant_quota("alice", quota)
    mgr.set_parent_authority("alice", {"read", "write", "compute"})

    env_ok = ResourceEnvelope(cpu_pct=50, mem_bytes=512 * 1024 * 1024, io_bps=10_000_000,
                              net_bps=5_000_000, pids=256)
    c = mgr.create(id="c1", tenant="alice", envelope=env_ok, capabilities={"read", "compute"})
    check(c.state == CREATED, "cell within quota is admitted")

    over_cpu = ResourceEnvelope(cpu_pct=150, mem_bytes=512 * 1024 * 1024, io_bps=1_000_000,
                                net_bps=1_000_000)
    expect_raise(QuotaExceeded,
                 lambda: mgr.create(id="c2", tenant="alice", envelope=over_cpu, capabilities={"read"}),
                 "cpu envelope above tenant quota")

    over_mem = ResourceEnvelope(cpu_pct=10, mem_bytes=4 * 1024 * 1024 * 1024, io_bps=1_000_000,
                                net_bps=1_000_000)
    expect_raise(QuotaExceeded,
                 lambda: mgr.create(id="c3", tenant="alice", envelope=over_mem, capabilities={"read"}),
                 "mem envelope above tenant quota")

    expect_raise(AuthorityError,
                 lambda: mgr.create(id="c4", tenant="alice", envelope=env_ok,
                                    capabilities={"read", "admin"}),
                 "capability set above parent authority")

    d = mgr.slice_descriptor(c)
    check(d["parent_slice"] != CONTROL_SLICE and not d["is_control_slice"],
          "cell slice nests under the tenant slice, never control.slice")
    check(d["controllers"]["MemoryMax"] == env_ok.mem_bytes, "MemoryMax == envelope mem cap")
    check(d["controllers"]["CPUQuota"] == "50%", "CPUQuota == envelope cpu cap")
    check(d["controllers"]["TasksMax"] == 256, "TasksMax == envelope pids cap")

    spec = mgr.microvm_spec(c)
    check(isinstance(spec, MicroVMSpec) and spec.boots_own_kernel, "microVM boots its OWN kernel")
    check(spec.host_mounts == () and spec.host_cred_refs == (),
          "microVM spec has NO host mounts and NO host creds")
    check(spec.mem_bytes == env_ok.mem_bytes and spec.vcpus == 1, "vcpus/mem derived from envelope")
    sd = spec.to_dict()
    check(sd["rootfs"]["base_ro"].startswith("cas:") and sd["rootfs"]["delta_ephemeral"] is True,
          "rootfs = RO shared CAS base + ephemeral per-tenant delta")
    check(any(v["type"] == "vsock" for v in sd["virtio"]),
          "control/seat channel is virtio-vsock (no per-cell TCP)")

    from dataclasses import replace as _replace
    bad = _replace(spec, host_mounts=("/:/host",))
    expect_raise(AuthorityError, bad.validate, "microVM spec with a host mount")

def main():
    print("=== T13 per-tenant CELL / microVM MANAGER MODEL — test suite ===")
    test_lifecycle()
    test_control_plane_separation()
    test_capability_scoping()
    test_resource_envelope()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    print("NOTE: model/manager/config-generation only. Actual microVM isolation needs a real VMM +"
          " KVM and is SEPARATE infra (see ~/brainarbeit/os/pn-vmm).")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
