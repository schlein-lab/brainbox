package detect

import "github.com/schlein-lab/brainarbeit/installer/internal/sizing"

type Verdict string

const (

	Green Verdict = "green"

	Amber Verdict = "amber"

	Red Verdict = "red"
)

type Backend string

const (
	BackendLibvirt   Backend = "libvirt"
	BackendQemuRaw   Backend = "qemu-raw"
	BackendSynology  Backend = "synology-vmm"
	BackendQNAP      Backend = "qnap-vs"
	BackendTrueNAS   Backend = "truenas"
	BackendHyperV    Backend = "hyper-v"
	BackendWHPX      Backend = "whpx"
	BackendHVF       Backend = "hvf"
	BackendContainer Backend = "container"
	BackendNone      Backend = "none"
)

type Platform string

const (
	PlatformLinux    Platform = "linux"
	PlatformSynology Platform = "synology-dsm"
	PlatformQNAP     Platform = "qnap-qts"
	PlatformTrueNAS  Platform = "truenas"
	PlatformWindows  Platform = "windows"
	PlatformMacOS    Platform = "macos"
	PlatformUnknown  Platform = "unknown"
)

type HWVirt struct {
	DevKVM      bool
	CPUFlag     string
	Nested      bool
	InContainer bool
	HyperV      bool
}

type MgmtLayer struct {
	Libvirt        bool
	QemuPresent    bool
	QemuImgPresent bool
	SynologyVMM    bool
	QNAPVS         bool
	HyperVWMI      bool
	Backend        Backend
}

type Network struct {
	PrimaryIface  string
	PrimaryIP     string
	DefaultGW     string
	BridgePresent string
	Bridgeable    bool
	DHCP          bool
	mDNSName      string
}

type Capacity struct {
	RAMMiB int
	Cores  int
	Sizing sizing.Spec
}

type Report struct {
	Platform Platform   `json:"platform"`
	OSPretty string     `json:"os"`
	Arch     string     `json:"arch"`
	HWVirt   HWVirtJSON `json:"hw_virt"`
	Mgmt     MgmtJSON   `json:"mgmt_layer"`
	Capacity CapJSON    `json:"capacity"`
	Network  NetJSON    `json:"network"`
	Verdict  Verdict    `json:"verdict"`
	Backend  Backend    `json:"backend"`
	Reasons  []string   `json:"reasons"`
	Blockers []string   `json:"blockers"`
	Warnings []string   `json:"warnings"`
}

func Decide(p Platform, hv HWVirt, mg MgmtLayer, net Network) (Verdict, Backend, []string, []string, []string) {
	var reasons, blockers, warnings []string

	hasKVM := hv.DevKVM && hv.CPUFlag != ""
	switch {
	case hv.DevKVM && hv.CPUFlag == "":
		warnings = append(warnings, "/dev/kvm present but no vmx/svm CPU flag — virtualization may be emulated/slow")
	case !hv.DevKVM && hv.CPUFlag != "":
		blockers = append(blockers, "CPU supports "+hv.CPUFlag+" but /dev/kvm is absent — enable virtualization in firmware / load the kvm module")
	}

	backend := selectBackend(p, hasKVM, mg)

	var v Verdict
	switch {
	case !hasKVM:
		v = Red
		blockers = append(blockers, "no usable KVM → bare namespaced container fallback: pn-init is not PID1, HW-reset/L0 recovery and device-empowerment are unavailable")
	case hv.InContainer && !hv.Nested:
		v = Amber
		warnings = append(warnings, "running inside a container without nested-virt → micro-VM path degraded")
	case net.Bridgeable:
		v = Green
		reasons = append(reasons, "KVM available and a LAN bridge is possible → real VM with its own LAN IP")
	default:
		v = Amber
		warnings = append(warnings, "KVM available but no LAN bridge → NAT + port-forward; mDNS discovery degraded")
	}

	if hasKVM && !hv.Nested {
		warnings = append(warnings, "nested-virt disabled — per-user CELL-VM isolation (§16) will fall back to namespaced cells")
	}
	if hasKVM && !mg.Libvirt {
		if mg.QemuPresent {
			warnings = append(warnings, "no libvirtd/virtqemud — will use the supervised raw-qemu backend")
		} else {
			blockers = append(blockers, "KVM present but neither libvirt nor qemu-system-* is installed — install libvirt+qemu (or qemu) before install")
		}
	}
	if hasKVM && !mg.QemuImgPresent {
		warnings = append(warnings, "qemu-img not found — the factory bundles its own for image conversion")
	}

	if v == Green {
		reasons = append(reasons, "backend="+string(backend))
	}
	return v, backend, reasons, blockers, warnings
}

func selectBackend(p Platform, hasKVM bool, mg MgmtLayer) Backend {
	switch p {
	case PlatformSynology:
		return BackendSynology
	case PlatformQNAP:
		return BackendQNAP
	case PlatformTrueNAS:

		if mg.Libvirt {
			return BackendLibvirt
		}
		return BackendTrueNAS
	case PlatformWindows:

		if mg.HyperVWMI {
			return BackendHyperV
		}
		return BackendWHPX
	case PlatformMacOS:

		return BackendHVF
	}
	if !hasKVM {
		return BackendContainer
	}
	if mg.Libvirt {
		return BackendLibvirt
	}
	if mg.QemuPresent {
		return BackendQemuRaw
	}
	return BackendNone
}
