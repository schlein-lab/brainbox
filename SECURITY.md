# Security Policy

Brainbox is built to **fail closed**: capabilities are off until you turn them on,
local services bind to loopback, the box is claimed before it listens on the network,
and the one privileged helper is argument-locked and audited. That is a design posture,
**not a guarantee** — we don't claim Brainbox is "secure." Please review the code and
report anything that looks wrong.

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** — do not open a public issue for
them.

- **Email:** schlein.lab@gmail.com
- Subject line: `SECURITY: <short summary>`
- Include: affected component/path, version or commit, a description, and (ideally) a
  minimal reproduction or proof of concept.

You will get an acknowledgement as soon as reasonably possible. Please give a reasonable
window to investigate and prepare a fix before any public disclosure. This is a personal,
noncommercial project, so timelines are best-effort rather than contractual.

## Scope

In scope: the code in this repository — the boot chain (`pn-init`), the microVM monitor
(`pn-vmm`), the scheduler (`engine/`), the portal (`cockpit/`), the appliance/ISO builders
(`os/image/`), and the setup wizard.

Out of scope: the separately-released relay client (its own repository), third-party
dependencies (report those upstream), and issues that require pre-existing root/physical
access to the box.

## Good to know

- First-boot credentials are **per-image and random**; SSH is **key-only** with root login
  disabled.
- The LAN media server (SMB/DLNA) is **off by default** and reversible from the portal.
- Test fixtures in this repository may contain **intentionally fake** secrets/keys to
  exercise detection paths — these are not real credentials.

Thank you for helping keep people who self-host their own AI box safer.
