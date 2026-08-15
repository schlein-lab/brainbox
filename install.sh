#!/usr/bin/env sh
set -eu

cat <<'TEXT'

  B R A I N B O X   1.0

  There is nothing to install with a script. Pick your platform, write the image,
  boot it. The box gives itself a name, its own keys and its own certificate, and
  then shows you a setup address with a QR code.

    A spare x86 machine, or any hypervisor that boots ISOs
        brainbox-1.0.0-amd64.iso
        Boot it. The installer runs on its own -- four steps, no input -- and
        reboots into the installed system.

    A Raspberry Pi 5
        brainbox-1.0.0-pi-arm64.img.xz
        Write it to an SD card or USB disk (>= 16 GB) with Raspberry Pi Imager
        ("Use custom") or dd. Read START-HERE.txt on the card afterwards.

    QEMU / libvirt / Proxmox      brainbox-1.0.0-amd64.qcow2
    Windows Hyper-V               brainbox-1.0.0-amd64.vhdx.xz
    VirtualBox                    brainbox-1.0.0-amd64.ova
    Linux with Docker (preview)   brainbox-1.0.0-docker-amd64.tar.xz

  All of them, with checksums and a signature:

      https://get.brainarbeit.com

  Verify before you write:

      minisign -Vm SHA256SUMS -p minisign.pub
      sha256sum -c SHA256SUMS --ignore-missing

  Then: open the address the box shows you, on any device in the same network.
  A wizard does the rest. No command line at any point.

  Sessions are hardware-virtualised microVMs, so the machine needs VT-x/AMD-V --
  and inside a VM, nested virtualization must be enabled. Without it the box
  boots and says so plainly; it never pretends.

  Source, licence (PolyForm Noncommercial) and documentation:

      https://github.com/schlein-lab/brainbox

TEXT
exit 0
