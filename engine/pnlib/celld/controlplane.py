
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CONTROL_SLICE = "control.slice"
TENANTS_SLICE = "pn-tenants.slice"

NODE_CONTROL = "control"
NODE_BROKER = "broker-door"
NODE_CELL = "cell"
NODE_HOST = "host"
_KINDS = (NODE_CONTROL, NODE_BROKER, NODE_CELL, NODE_HOST)

@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    slice: str
    reserved: bool = False

    def validate(self):
        if self.kind not in _KINDS:
            raise ValueError(f"unknown node kind {self.kind!r}")
        return self

@dataclass(frozen=True)
class Edge:

    src: str
    dst: str
    kind: str = "reach"

@dataclass
class Topology:
    nodes: dict
    edges: list

    def node(self, nid: str) -> Optional[Node]:
        return self.nodes.get(nid)

    def add_edge(self, src: str, dst: str, kind: str = "reach") -> "Topology":
        self.edges.append(Edge(src, dst, kind))
        return self

@dataclass(frozen=True)
class InvariantResult:

    ok: bool
    violations: tuple

    def __bool__(self):
        return self.ok

def check_isolation(topology: Topology) -> InvariantResult:

    nodes = topology.nodes
    violations: list = []

    for n in nodes.values():
        try:
            n.validate()
        except ValueError as e:
            violations.append((n.id, n.id, str(e)))
            continue
        if n.kind == NODE_CONTROL and n.slice != CONTROL_SLICE:
            violations.append((n.id, n.id,
                               f"control node not in the reserved {CONTROL_SLICE} (is {n.slice!r})"))

        if n.kind == NODE_BROKER and n.slice != CONTROL_SLICE:
            violations.append((n.id, n.id,
                               f"broker-door not in the reserved {CONTROL_SLICE} (is {n.slice!r}) — "
                               f"the mediated ingress must live in the control plane"))
        if n.kind == NODE_CELL and n.slice == CONTROL_SLICE:
            violations.append((n.id, n.id,
                               f"tenant cell placed in {CONTROL_SLICE} (must be under {TENANTS_SLICE})"))

    for e in topology.edges:
        src = nodes.get(e.src)
        dst = nodes.get(e.dst)
        if src is None or dst is None:
            violations.append((e.src, e.dst, "edge references an unknown node"))
            continue
        if src.kind != NODE_CELL:
            continue
        if dst.kind == NODE_BROKER:
            continue
        if dst.kind == NODE_CONTROL:
            reason = "cell can reach the control plane (control.slice) — FORBIDDEN"
        elif dst.kind == NODE_CELL:
            reason = "cell can reach a sibling cell — FORBIDDEN"
        elif dst.kind == NODE_HOST:
            reason = "cell can reach the host — FORBIDDEN"
        else:
            reason = f"cell can reach a {dst.kind!r} node — FORBIDDEN"
        violations.append((e.src, e.dst, reason))

    return InvariantResult(ok=(len(violations) == 0), violations=tuple(violations))

def isolated_topology(cell_ids, *, broker_id: str = "broker",
                      control_ids=("pn-celld", "scheduler")) -> Topology:

    nodes: dict = {}
    for cid in control_ids:
        nodes[cid] = Node(cid, NODE_CONTROL, CONTROL_SLICE, reserved=True)
    nodes[broker_id] = Node(broker_id, NODE_BROKER, CONTROL_SLICE, reserved=True)
    edges: list = []
    for cell_id in cell_ids:
        nodes[cell_id] = Node(cell_id, NODE_CELL, f"pn-cell-{cell_id}.slice")
        edges.append(Edge(cell_id, broker_id, "broker-call"))
    return Topology(nodes=nodes, edges=edges)

__all__ = [
    "Node", "Edge", "Topology", "InvariantResult", "check_isolation", "isolated_topology",
    "CONTROL_SLICE", "TENANTS_SLICE", "NODE_CONTROL", "NODE_BROKER", "NODE_CELL", "NODE_HOST",
]
