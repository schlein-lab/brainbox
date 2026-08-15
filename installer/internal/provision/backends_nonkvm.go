package provision

import (
	"fmt"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/state"
)

func vmImportEntries(ctx BackendContext, defineRev, startRev string) []StateEntry {
	st := []StateEntry{networkStateEntry(ctx)}
	st = append(st, imageStateEntries(ctx)...)
	st = append(st,
		StateEntry{Action: state.ActDefineDomain, Target: ctx.Name, Reverse: defineRev},
		StateEntry{Action: state.ActStartDomain, Target: ctx.Name, Reverse: startRev},
	)
	return st
}

type synologyDriver struct{}

func (synologyDriver) Backend() detect.Backend { return detect.BackendSynology }

func (synologyDriver) Plan(ctx BackendContext) BackendPlan {
	steps := []string{
		"locate the VMM storage volume (DSM Storage Pool); convert golden image to a VMM disk image",
		"TODO(SDK): authenticate to DSM — POST /webapi/auth.cgi (SYNO.API.Auth) → sid cookie",
		"TODO(SDK): SYNO.Virtualization.API.Guest.create — name=" + ctx.Name +
			fmt.Sprintf(", vram=%dMiB, vcpu=%d, autorun=on", ctx.Spec.VRAMMiB, ctx.Spec.VCPU),
		"TODO(SDK): SYNO.Virtualization.API.Guest.Image.create — import the converted disk as vda; add DATA disk as vdb",
		"TODO(SDK): attach NIC to the VMM virtual switch bound to the LAN bridge (Open vSwitch ovs_eth*)",
		"TODO(SDK): SYNO.Virtualization.API.Guest.power power_on=true",
		"NOTE: DSM VMM has NO i6300esb passthrough → the in-guest softdog is the watchdog; first-boot self-test confirms",
		"first-boot: open cockpit onboarding via the VMM console / port-forward",
	}
	return BackendPlan{
		Backend:  detect.BackendSynology,
		Title:    "Synology VMM guest (DSM Virtualization API)",
		Maturity: MaturityStub,
		Steps:    steps,
		State: vmImportEntries(ctx,
			"TODO(SDK): SYNO.Virtualization.API.Guest.delete name="+ctx.Name,
			"TODO(SDK): SYNO.Virtualization.API.Guest.power power_on=false"),
		Degraded: forceAmberIfWatchdogSoft(ctx, "Synology VMM has no hardware watchdog passthrough"),
	}
}

type qnapDriver struct{}

func (qnapDriver) Backend() detect.Backend { return detect.BackendQNAP }

func (qnapDriver) Plan(ctx BackendContext) BackendPlan {
	steps := []string{
		"locate the Virtualization Station datastore (a /share/CACHEDEV*_DATA volume); convert golden image",
		"TODO(SDK): authenticate — GET /cgi-bin/authLogin.cgi → QtsHttpSession (sid)",
		"TODO(SDK): Virtualization Station REST POST /qvs/v1/vms — create VM name=" + ctx.Name +
			fmt.Sprintf(" mem=%dMiB cpu=%d boot=uefi", ctx.Spec.VRAMMiB, ctx.Spec.VCPU),
		"TODO(SDK): POST /qvs/v1/vms/{id}/disks — attach converted root (vda) + DATA disk (vdb)",
		"TODO(SDK): POST /qvs/v1/vms/{id}/nics — bind NIC to the VS virtual switch on the LAN bridge",
		"TODO(SDK): POST /qvs/v1/vms/{id}/power {action:start}",
		"NOTE: VS exposes no i6300esb → softdog watchdog; nested-virt is commonly OFF on QNAP → expect amber",
		"first-boot watchdog self-test over the VS console",
	}
	return BackendPlan{
		Backend:  detect.BackendQNAP,
		Title:    "QNAP Virtualization Station VM (QVS REST API)",
		Maturity: MaturityStub,
		Steps:    steps,
		State: vmImportEntries(ctx,
			"TODO(SDK): DELETE /qvs/v1/vms/{id}",
			"TODO(SDK): POST /qvs/v1/vms/{id}/power {action:stop}"),
		Degraded: forceAmberIfWatchdogSoft(ctx, "QNAP VS has no hardware watchdog passthrough"),
	}
}

type truenasDriver struct{}

func (truenasDriver) Backend() detect.Backend { return detect.BackendTrueNAS }

func (truenasDriver) Plan(ctx BackendContext) BackendPlan {
	steps := []string{
		"create a zvol for the root + a zvol for DATA on the selected pool (zfs create -V)",
		"convert + dd the verified golden image into the root zvol",
		"TODO(SDK): authenticate to middlewared — wss://host/websocket (api_key) or `midclt`",
		"TODO(SDK): midclt call vm.create '{name:\"" + ctx.Name + "\", bootloader:UEFI, " +
			fmt.Sprintf("memory:%d, vcpus:%d}'", ctx.Spec.VRAMMiB, ctx.Spec.VCPU),
		"TODO(SDK): midclt call vm.device.create DISK(root zvol, virtio) + DISK(DATA zvol, virtio)",
		"TODO(SDK): midclt call vm.device.create NIC(type=BRIDGE, br_iface=<LAN bridge>)",
		"TODO(SDK): SCALE ≥ 24.10 uses incus — alt path: incus init/import + incus config device add",
		"TODO(SDK): midclt call vm.start <id>",
		"NOTE: SCALE's libvirt is internal; if the libvirt socket is reachable the libvirt driver is preferred over this",
		"first-boot watchdog self-test (i6300esb present under SCALE libvirt; softdog otherwise)",
	}
	return BackendPlan{
		Backend:  detect.BackendTrueNAS,
		Title:    "TrueNAS SCALE VM (middlewared/incus on ZFS zvols)",
		Maturity: MaturityStub,
		Steps:    steps,
		State: vmImportEntries(ctx,
			"TODO(SDK): midclt call vm.delete <id>; zfs destroy <root zvol>",
			"TODO(SDK): midclt call vm.stop <id>"),
		Degraded: DegradeFor(ctx.Report),
	}
}

type hyperVDriver struct{}

func (hyperVDriver) Backend() detect.Backend { return detect.BackendHyperV }

func (hyperVDriver) Plan(ctx BackendContext) BackendPlan {
	vhdx := ctx.DataDir + `\` + ctx.Name + "-root.vhdx"
	dataVhdx := ctx.DataDir + `\` + ctx.Name + "-data.vhdx"
	steps := []string{
		"qemu-img convert -O vhdx <verified golden image> " + vhdx + "   (Gen2 needs VHDX, not qcow2)",
		fmt.Sprintf("New-VHD -Path %s -SizeBytes %dGB -Dynamic   (separate DATA VHDX)", dataVhdx, ctx.Spec.DataGiB),
		"TODO(SDK): New-VM -Name " + ctx.Name + " -Generation 2 -MemoryStartupBytes " +
			fmt.Sprintf("%dMB -VHDPath %s -SwitchName <external vSwitch>", ctx.Spec.VRAMMiB, vhdx),
		"TODO(SDK): Set-VMProcessor -VMName " + ctx.Name + fmt.Sprintf(" -Count %d", ctx.Spec.VCPU) +
			" -ExposeVirtualizationExtensions $true   (nested-virt for CELL-VMs)",
		"TODO(SDK): Add-VMHardDiskDrive -VMName " + ctx.Name + " -Path " + dataVhdx,
		"TODO(SDK): Set-VMFirmware -VMName " + ctx.Name + " -EnableSecureBoot Off   (custom kernel)",
		"TODO(SDK): Set-VM -VMName " + ctx.Name + " -AutomaticStartAction Start -AutomaticStopAction ShutDown",
		"TODO(SDK): Start-VM -Name " + ctx.Name,
		"NOTE: Hyper-V has no i6300esb. The host-side substitute is a WMI/PowerShell health watcher " +
			"(Get-VMIntegrationService heartbeat) that Restart-VMs on failure; first-boot self-test confirms softdog.",
		"first-boot: vmconnect.exe console for cockpit onboarding",
	}
	return BackendPlan{
		Backend:  detect.BackendHyperV,
		Title:    "Hyper-V Generation-2 VM (New-VM + WMI)",
		Maturity: MaturityStub,
		Steps:    steps,
		State: vmImportEntries(ctx,
			"TODO(SDK): Remove-VM -Name "+ctx.Name+" -Force",
			"TODO(SDK): Stop-VM -Name "+ctx.Name+" -Force"),
		Degraded: forceAmberIfWatchdogSoft(ctx, "Hyper-V has no hardware watchdog passthrough (WMI heartbeat substitute)"),
		Extra:    []string{"host-side watchdog substitute: a scheduled PowerShell heartbeat watcher (Restart-VM on failure)"},
	}
}

type accelDriver struct{ kind detect.Backend }

func (d accelDriver) Backend() detect.Backend { return d.kind }

func (d accelDriver) Plan(ctx BackendContext) BackendPlan {
	var accel, emuNote, title, supervisor string
	switch d.kind {
	case detect.BackendHVF:
		accel, title = "hvf", "macOS Hypervisor.framework VM (vfkit / qemu -accel hvf)"
		emuNote = "vfkit (preferred on Apple Silicon, aarch64) or qemu-system -accel hvf"
		supervisor = "launchd plist (KeepAlive) at ~/Library/LaunchAgents/dev.brainarbeit.plist"
	default:
		accel, title = "whpx", "Windows Hypervisor Platform / WSL2 micro-VM (qemu -accel whpx)"
		emuNote = "qemu-system -accel whpx,kernel-irqchip=off (Win); under WSL2 /dev/kvm → qemu -accel kvm in the WSL guest"
		supervisor = "a Scheduled Task (Win) / systemd user unit (WSL2) with restart-on-failure"
	}
	steps := []string{
		"qemu-img convert -O qcow2 <verified golden image> " + ctx.Image.RootImg,
		fmt.Sprintf("qemu-img create -f qcow2 %s %dG   (DATA disk)", ctx.Image.DataImg, ctx.Spec.DataGiB),
		"TODO(SDK): write the host-side supervisor: " + supervisor,
		"TODO(SDK): launch " + emuNote + fmt.Sprintf(" -m %d -smp %d", ctx.Spec.VRAMMiB, ctx.Spec.VCPU),
		"TODO(SDK): NAT user-net with hostfwd 22000→22 and 8443→443 (these accelerators rarely bridge cleanly)",
		"NOTE: -accel " + accel + " gives a real VM but NO i6300esb → softdog watchdog only (amber); WSL2 also depends on the WSL VM living",
		"first-boot watchdog self-test over the serial pty",
	}
	return BackendPlan{
		Backend:  d.kind,
		Title:    title,
		Maturity: MaturityStub,
		Steps:    steps,
		State: vmImportEntries(ctx,
			"TODO(SDK): remove the "+supervisor+" supervisor + qemu process",
			"TODO(SDK): stop the qemu -accel "+accel+" process"),
		Degraded: forceAmberIfWatchdogSoft(ctx, "-accel "+accel+" has no hardware watchdog passthrough"),
		Extra:    []string{"host-side supervisor: " + supervisor},
	}
}

type containerDriver struct{}

func (containerDriver) Backend() detect.Backend { return detect.BackendContainer }

func (containerDriver) Plan(ctx BackendContext) BackendPlan {
	dataPath := ctx.DataDir + "/" + ctx.Name + "-data"
	steps := []string{
		"unpack the golden image ROOTFS (not a qcow2 — there is no VM) into a container image layer",
		"create a host-side DATA directory bind mount: " + dataPath + " (btrfs subvol; survives container recreate)",
		"TODO(SDK): podman/docker run --name " + ctx.Name + " --restart=on-failure " +
			fmt.Sprintf("--memory=%dm --cpus=%d", ctx.Spec.VRAMMiB, ctx.Spec.VCPU) +
			" -v " + dataPath + ":/data --network bridge <golden rootfs>",
		"TODO(SDK): the container ENTRYPOINT is a HOST-SIDE-SUPERVISED pn-init-compat shim, NOT PID1 init " +
			"(no cgroup self-carve, no /dev/watchdog pet — the host runtime's restart policy is the only recovery)",
		"register the host-side supervisor (systemd unit watching the container) — substitutes for the L0 watchdog",
		"pn-init starts pnd in DEGRADED mode: device-empowerment/firmware task-types are NOT registered",
		"EMIT the loud red self-report banner at every boot + raise health.degraded{reason:no-kvm}",
	}
	st := []StateEntry{
		networkStateEntry(ctx),
		{Action: state.ActCreateDisk, Target: dataPath, Reverse: "rm -rf " + dataPath + " (DATA kept unless --purge)"},
		{Action: state.ActDefineDomain, Target: ctx.Name, Reverse: "podman rm -f " + ctx.Name},
		{Action: state.ActStartDomain, Target: ctx.Name, Reverse: "podman stop " + ctx.Name},
	}
	return BackendPlan{
		Backend:  detect.BackendContainer,
		Title:    "bare namespaced container (RED — no KVM, no real appliance)",
		Maturity: MaturityStub,
		Steps:    steps,
		State:    st,
		Degraded: DegradeFor(ctx.Report),
		Extra:    []string{"host-side supervisor REQUIRED: systemd unit / runtime restart=on-failure (pn-init is not PID1)"},
	}
}

func forceAmberIfWatchdogSoft(ctx BackendContext, reason string) DegradedBanner {
	b := DegradeFor(ctx.Report)
	if b.Level == LevelGreen {
		return DegradedBanner{
			Level:    LevelAmber,
			Headline: "AMBER — reachable on its own LAN IP, but the hardware watchdog is a no-op.",
			Losses: []string{
				reason + " → the L0 hard-reset path is NOT real on this backend",
			},
			Substitutes: []string{
				"in-guest softdog watchdog + the backend's host-side restart policy substitute for HW reset",
				"first-boot watchdog self-test detects the no-op and switches pn-init to softdog automatically",
			},
		}
	}

	b.Losses = append(b.Losses, reason)
	return b
}

func init() {
	register(synologyDriver{})
	register(qnapDriver{})
	register(truenasDriver{})
	register(hyperVDriver{})
	register(accelDriver{kind: detect.BackendWHPX})
	register(accelDriver{kind: detect.BackendHVF})
	register(containerDriver{})
}
