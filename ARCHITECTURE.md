# BrainBox — Architecture

BrainBox is a self-hosted appliance that runs AI agents in hardware-isolated microVMs
on a computer you own. It ships as an immutable, single-owner OS image — no host Linux
administration, no cloud dependency. The source is organized in four layers.

## `os/` — the substrate

- **`os/init` — `pn-init`**: PID 1. A from-scratch init, service supervisor, tiered
  watchdog, and cgroup-delegation layer. The appliance runs no systemd.
- **`os/pn-vmm` — the microVM monitor**: a from-scratch KVM VMM. Every agent session
  boots in its own hardware-isolated *cell* (jail + seccomp + virtio + vsock). Host-side
  *broker lanes* grant a cell governed, revocable access to the LLM, the network, and the
  portal — the cell never holds host credentials.
- **`os/image`**: reproducible A/B disk image and BIOS/UEFI installer ISO build. The image
  boots straight into first-run setup.
- **`os/node-agent`, `os/hypervisor`, `os/breakglass`**: fleet worker-node agent,
  host-side OOM/eviction protection, and a portal-independent repair console.

## `engine/` — scheduling & runtime

- **`engine/pnlib`**: the scheduler, the admission/queue and fair-share layer, cgroup
  accounting, and the brain (LLM) plane. All compute — LLM and non-LLM — is governed by a
  single queue with per-principal contingents.
- **`engine/tools`**: the `pn*` command-line tools. **`engine/relaylib`**: the off-LAN
  relay protocol used by remote clients.

## `cockpit/` — the portal

- **`cockpit/server` — `brainbox-portal`**: the web cockpit (sessions, voice, files,
  approvals, admin), stdlib-only Python. **`cockpit/server/webapp`**: the single-page app.

## `gateway/` — the public API

- A stdlib HTTP/WebSocket gateway that fronts the box's one bus, with OpenAPI and AsyncAPI
  specs, reference Python/TypeScript SDKs, and an MCP server. It reuses the box's existing
  auth model (durable token + mandatory 2FA + capability intersection) and adds no new
  trust assumptions.

## `clients/` — client surfaces

- One framework-free client *core*, plus a PWA, a single-file CLI (`bbx`), and SDKs.

## `components/` — phantom

- A from-scratch, zero-dependency Wayland compositor that gives a caged agent its own
  screen and GUI, so it can drive real Linux programs through their own I/O.

## Isolation model

The trust boundary is hardware virtualization: an agent runs inside a KVM guest, not a
container. It reaches the outside world only through explicit, per-request broker lanes
that the host mediates and can cut at any time. See `LICENSE` for terms.
