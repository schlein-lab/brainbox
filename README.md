<p align="center">
  <img src="assets/brainbox-lockup.png" alt="Brainbox" width="440">
</p>

<p align="center">
  <strong>Give every AI agent its own computer - empowering your agents while protecting your system and data.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-0d9488"></a>
  <img alt="Platform: x86-64" src="https://img.shields.io/badge/platform-x86--64-4f46e5">
  <img alt="Release" src="https://img.shields.io/badge/release-1.0.1-16a34a">
  <a href="https://get.brainarbeit.com/brainbox-installer-amd64-1.0.1.iso"><img alt="Download installer ISO" src="https://img.shields.io/badge/download-installer%20ISO-0d9488"></a>
  <a href="https://brainarbeit.com"><img alt="brainarbeit.com" src="https://img.shields.io/badge/web-brainarbeit.com-111827"></a>
</p>

---

AI agents are finally good enough to *do* real work, not just chat - drive a desktop, run code,
use real applications, handle your files, answer you out loud. But doing real work takes real
access, and an agent that reads the open web can be turned against you by what it reads. Handing
something that much power over your machine and your data is a bet most people, researchers or clinicans won't make.

**Brainbox changes the terms of that bet.** It's an appliance you run on a computer you own. Every
agent session gets a *real, full computer* of its own - its own screen, its own apps and tools,
its own Linux - sealed inside its own virtual machine. Inside that wall the agent has genuine power
to get things done. Outside it, nothing gets out: not a hijacked session, not your files, not your
keys. Autonomous AI that actually works for you - in your building, your clinic, your sensitive environment, 
under your control, with the radius of any single agent contained to a machine you can throw away. 
Voice lane is already local by design - if you use local LLM, you can even use it 
in highly sensitive areas and no data will leave the building.

## What your agents can actually do

**Operate a real computer, not just a chat box, also in protected environments** 
Each session is a genuine Linux machine with a graphical desktop the agent can *use* - 
open a browser, click through an application, drive an editor, run and debug code, install tools. 
It acts like a person at a keyboard, because it has one. 
You can watch over its shoulder from the portal, or take the controls yourself at any time. 

**Agent work is sealed, but are empowered at the same time - by design** 
Brainbox includes novel features that help agents operate programs which have been build in the pre-AI era.
It can fill in your documents, use programs, operate on GUI level faster and easier than on regular systems.
These features include own display server to improve agents in operating GUI. Brainbox exposes a mediaserver, 
where you can share or get files from the different sessions. 

**Talk to it, and let it talk back.** Brainbox can listen and speak, so your AI is something you
*use in the room*, hands-free - a question out loud, an answer out loud. The voice can come from the
box itself, the device in your hand, or your existing Home Assistant speakers. It does not need a cloud 
for its voice lane.

**Bring your own brain - keep your account.** Brainbox doesn't sell or proxy a model. You connect
*your* Claude/Codex/... account (a subscription sign-in, a token, or an API key) once in the wizard; it stays
on the box, never passes through anyone else, and you can swap or disconnect it whenever you like. 
Of course you can also use your local LLM models, such as Ollama. You can centralize and administrate the LLM accounts,
or let users bring their individual subscription. It's your choice how to handle LLM accounts.

**Put it to work on real jobs, at real scale.** Several boxes can share one workload. That isn't a
promise on a slide - it's how [doesitreproduce.com](https://doesitreproduce.com) re-runs thousands
of published scientific analyses, on a fleet of these same boxes, today. 
Any computer can participate with its CPU/RAM/disk space - your laptop, NAS, Raspberry Pi,...

**Make it your household's, not just yours.** Add member accounts for the people
you live or work with. Run the whole interface in your own language - German and English ship in the
box, more can be added later, even to a box that's already running. 

**Work from everywhere.** Adding telegram or the advanced off-lan relay client helps you to continue
your work from everywhere - security hardened. Also, brainbox serves thin-clients for windows and linux, making it easy
for standard users to participate. 

## Why you can hand an agent that much power

The reason all of that is *safe* to offer is a single design decision: **every session runs in its
own virtual machine, with its own kernel.** Not a prompt rule, not a filter, not a shared-kernel
sandbox — a real hardware boundary, the kind hypervisors use to keep whole servers apart.

Prompt injection - hidden instructions smuggled in through what an agent reads - can't be filtered
away reliably; anyone who claims otherwise is guessing. So Brainbox assumes a session *will*
eventually be tricked and makes that a non-event. A compromised session is trapped in its own
disposable machine: it can't reach your files, your network, your other sessions, or your AI
credentials (those are attached at a broker on the boundary, so the session never holds them). You
delete the machine, and the problem is gone with it.

The same wall works in both directions. Because a session is genuinely sealed, it's safe to give it
a real computer to work on - full userland, a graphical desktop, access down to the system-call
level. The isolation isn't there to shrink the agent into uselessness; it's what makes real
autonomy safe to grant. **Shield and workshop are the same wall.**

## Built from scratch, on purpose

An "agent OS" usually turns out to be an app, a framework, or a cloud service running on
top of an ordinary operating system. Brainbox is the operating system itself - you install
it, and it owns the whole machine. It was built from the ground up for one purpose, running
autonomous agents, and every release sharpens it for that purpose and nothing else.

A box that hosts autonomous AI has to be understandable and controllable end to end, so Brainbox
doesn't sit on a general-purpose Linux distribution and a pile of background services it can't
account for. The payoff is a box that is inspectable, predictable, and comes back cleanly from 
a power cut with nobody watching.

### Under the hood

| Component | Path | What it does |
|---|---|---|
| `pn-vmm` | [`os/pn-vmm/`](os/pn-vmm/) | Gives each session its own virtual machine and kernel, with no network unless you grant it. Sandboxes itself down to the system-call level and mediates what runs inside a session. |
| `phantom` | [`components/phantom/`](components/phantom/) | The graphical layer — a compositor, input, and screen streaming — so an agent can operate a real desktop, and you can watch or take over. |
| portioneer | [`engine/`](engine/) | Shares CPU, memory, and AI access fairly across tasks, so interactive work stays fast and heavy jobs queue instead of swamping the box. |
| portal | [`cockpit/`](cockpit/) | The web control panel for sessions, files, devices, voice, and members. Runs unprivileged; the few root actions go through fixed, argument-checked helpers. |
| `pn-init` | [`os/init/`](os/init/) | The small init, written in C, that starts and supervises everything and recovers the box after a power cut — no systemd. |
| setup wizard | [`os/image/`](os/image/) | First-boot setup in the browser or on-screen. Claiming a fresh box needs the one-time code shown on its own display. |

## Install

Brainbox **is** the operating system — it doesn't install alongside Windows or Linux.

1. Download **[`brainbox-installer-amd64-1.0.1.iso`](https://get.brainarbeit.com/brainbox-installer-amd64-1.0.1.iso)**
   (~2.9 GB) and, if you like, verify it against its
   [SHA-256](https://get.brainarbeit.com/brainbox-installer-amd64-1.0.1.iso.sha256).
2. Boot it on a machine or VM with an empty disk of at least 16 GB. Agent sessions run as real
   virtual machines, so the host must allow hardware virtualization — Intel **VT-x** or AMD **AMD-V**
   (on by default on most PCs; inside a VM, enable *nested virtualization*).
3. The installer writes Brainbox to the disk and reboots into it. Remove the ISO.
4. The box shows its address, a QR code, and a one-time setup code on its screen.
5. Open that address in a browser on the same network and follow the wizard. The setup code proves
   you're the one standing at the box.

## Honest about the limits

Brainbox is built to fail safe - when something is unclear it stays shut, not open. Sessions get no
network until you grant it; a fresh box shows strangers only a stripped-down view; internal services
never listen on the wire; SSH is key-only with root locked and a random per-image password; and
deleting a session or device needs a second confirmation that, if it can't be reached, simply blocks
the action.

And, just as plainly, what Brainbox does **not** claim:

- Whoever runs the box may be able to see inside the sessions. The walls are between sessions, not between a
  session and the box's owner.
- Nothing pauses an agent for approval in the middle of a task.
- The wall between sessions is software, and software has bugs. "Much harder to break out of" is
  honest; "impossible" is not.

Found a security problem? See [SECURITY.md](SECURITY.md) — please report it privately first.

## Releases

### 1.0.1
- **Phantom / GUI operation** - the graphical layer was improved so agents drive real desktop applications more reliably.
- **Internationalization** - minor i18n refinements.
- **Hardening** - additional security hardening across the box.
- **Relay** - improvements to the off-LAN relay (steadier upkeep and transfers).

Installer: [`brainbox-installer-amd64-1.0.1.iso`](https://get.brainarbeit.com/brainbox-installer-amd64-1.0.1.iso) - [SHA-256](https://get.brainarbeit.com/brainbox-installer-amd64-1.0.1.iso.sha256)

### 1.0.0
- First public release.

## License

**PolyForm Noncommercial 1.0.0** — free for personal, research, educational, and other noncommercial
use; see [LICENSE](LICENSE). For commercial use, get in touch: **schlein.lab@gmail.com**.

## Contact

- Product: **[brainarbeit.com](https://brainarbeit.com)** · Lab: **[schlein-lab.com](https://schlein-lab.com)**
- Email: **schlein.lab@gmail.com** · Legal notice: [IMPRESSUM.md](IMPRESSUM.md)

Copyright © 2026 Dr. Dr. Christian Schlein, MBA (schlein-lab).
