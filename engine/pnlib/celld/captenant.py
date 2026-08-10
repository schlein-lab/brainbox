
from __future__ import annotations

from pnlib import captok
from .model import Cell, AuthorityError

CAP_DIM = "cap"
DIM_CPU = "cpu"
DIM_MEM = "mem"
DIM_IO = "io"
DIM_NET = "net"

PRIVILEGED_ACTIONS = frozenset({
    "host.exec", "host.mount", "net.configure", "wireguard.up", "secret.unseal",
    "device.attach", "cgroup.write", "vm.spawn", "slice.write", "control.call",
})

def _envelope_caveats(cell: Cell):
    env = cell.envelope
    return [
        captok.Caveat.scope(CAP_DIM, sorted(cell.capabilities)),
        captok.Caveat.num_leq(DIM_CPU, env.cpu_pct),
        captok.Caveat.num_leq(DIM_MEM, env.mem_bytes),
        captok.Caveat.num_leq(DIM_IO, env.io_bps),
        captok.Caveat.num_leq(DIM_NET, env.net_bps),
    ]

def derive_cell_capability(parent_token: "captok.CapToken", cell: Cell):

    return captok.attenuate(parent_token, agent=f"cell:{cell.tenant}",
                            caveats=_envelope_caveats(cell))

def attempt_escalation(parent_token: "captok.CapToken", *, tenant: str,
                       caps=(), cpu=None, mem=None, io=None, net=None):

    caveats = []
    if caps:
        caveats.append(captok.Caveat.scope(CAP_DIM, sorted(caps)))
    if cpu is not None:
        caveats.append(captok.Caveat.num_leq(DIM_CPU, cpu))
    if mem is not None:
        caveats.append(captok.Caveat.num_leq(DIM_MEM, mem))
    if io is not None:
        caveats.append(captok.Caveat.num_leq(DIM_IO, io))
    if net is not None:
        caveats.append(captok.Caveat.num_leq(DIM_NET, net))
    return captok.attenuate(parent_token, agent=f"cell:{tenant}", caveats=caveats)

def verify_cell_capability(cell_token, *, owner_pubkey, audience, parent_grant=None):

    grant = captok.verify(cell_token, owner_pubkey=owner_pubkey, audience=audience)
    if parent_grant is not None:

        for dim, pset in parent_grant.scopes.items():
            cset = grant.scopes.get(dim, frozenset())
            if not cset <= pset:
                raise captok.CapTokError(
                    f"cell scope {dim!r} {sorted(cset)} exceeds parent {sorted(pset)}")

        for dim, pcap in parent_grant.num_caps.items():
            ccap = grant.num_caps.get(dim)
            if ccap is not None and ccap > pcap:
                raise captok.CapTokError(
                    f"cell num cap {dim!r} {ccap} exceeds parent {pcap}")
    return grant

def assert_no_host_creds(cell: Cell) -> None:

    if cell.host_cred_refs:
        raise AuthorityError(
            f"cell {cell.id!r} holds host credentials {tuple(cell.host_cred_refs)!r} — FORBIDDEN")
    if cell.holds_no_host_creds is not True:
        raise AuthorityError(f"cell {cell.id!r} does not assert holds_no_host_creds")

def must_go_through_broker(action: str) -> bool:

    return action in PRIVILEGED_ACTIONS

def guard_in_cell(action: str) -> None:

    if must_go_through_broker(action):
        raise AuthorityError(
            f"privileged action {action!r} cannot run in-cell — it must go through the broker "
            f"(the cell holds no host authority)")

__all__ = [
    "CAP_DIM", "DIM_CPU", "DIM_MEM", "DIM_IO", "DIM_NET", "PRIVILEGED_ACTIONS",
    "derive_cell_capability", "attempt_escalation", "verify_cell_capability",
    "assert_no_host_creds", "must_go_through_broker", "guard_in_cell",
]
