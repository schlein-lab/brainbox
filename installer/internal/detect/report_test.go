package detect

import (
	"strings"
	"testing"
)

func TestDecideGreen(t *testing.T) {
	v, b, _, blockers, _ := Decide(
		PlatformLinux,
		HWVirt{DevKVM: true, CPUFlag: "vmx", Nested: true},
		MgmtLayer{Libvirt: true, QemuPresent: true, QemuImgPresent: true},
		Network{Bridgeable: true, BridgePresent: "br0"},
	)
	if v != Green {
		t.Fatalf("verdict=%s want green", v)
	}
	if b != BackendLibvirt {
		t.Fatalf("backend=%s want libvirt", b)
	}
	if len(blockers) != 0 {
		t.Fatalf("unexpected blockers: %v", blockers)
	}
}

func TestDecideAmberNoBridge(t *testing.T) {
	v, b, _, _, warnings := Decide(
		PlatformLinux,
		HWVirt{DevKVM: true, CPUFlag: "vmx", Nested: true},
		MgmtLayer{Libvirt: true, QemuPresent: true, QemuImgPresent: true},
		Network{Bridgeable: false},
	)
	if v != Amber {
		t.Fatalf("verdict=%s want amber", v)
	}
	if b != BackendLibvirt {
		t.Fatalf("backend=%s want libvirt", b)
	}
	if !hasSubstr(warnings, "no LAN bridge") {
		t.Fatalf("expected no-bridge warning, got %v", warnings)
	}
}

func TestDecideRedNoKVM(t *testing.T) {
	v, b, _, blockers, _ := Decide(
		PlatformLinux,
		HWVirt{DevKVM: false, CPUFlag: ""},
		MgmtLayer{},
		Network{Bridgeable: true},
	)
	if v != Red {
		t.Fatalf("verdict=%s want red", v)
	}
	if b != BackendContainer {
		t.Fatalf("backend=%s want container", b)
	}
	if !hasSubstr(blockers, "no usable KVM") {
		t.Fatalf("expected no-KVM blocker, got %v", blockers)
	}
}

func TestDecideVMXButNoDevKVMIsBlocker(t *testing.T) {

	v, _, _, blockers, _ := Decide(
		PlatformLinux,
		HWVirt{DevKVM: false, CPUFlag: "vmx"},
		MgmtLayer{},
		Network{Bridgeable: true},
	)
	if v != Red {
		t.Fatalf("verdict=%s want red", v)
	}
	if !hasSubstr(blockers, "enable virtualization in firmware") {
		t.Fatalf("expected firmware blocker, got %v", blockers)
	}
}

func TestDecideKVMNoMgmtLayer(t *testing.T) {

	v, b, _, blockers, _ := Decide(
		PlatformLinux,
		HWVirt{DevKVM: true, CPUFlag: "vmx", Nested: true},
		MgmtLayer{Libvirt: false, QemuPresent: false},
		Network{Bridgeable: true, BridgePresent: "br0"},
	)
	if v != Green {
		t.Fatalf("verdict=%s want green (KVM+bridge present)", v)
	}
	if b != BackendNone {
		t.Fatalf("backend=%s want none (no libvirt/qemu)", b)
	}
	if !hasSubstr(blockers, "neither libvirt nor qemu") {
		t.Fatalf("expected missing-mgmt blocker, got %v", blockers)
	}
}

func TestDecideQemuRawFallback(t *testing.T) {
	v, b, _, _, warnings := Decide(
		PlatformLinux,
		HWVirt{DevKVM: true, CPUFlag: "svm", Nested: true},
		MgmtLayer{Libvirt: false, QemuPresent: true, QemuImgPresent: true},
		Network{Bridgeable: true, BridgePresent: "br0"},
	)
	if v != Green {
		t.Fatalf("verdict=%s want green", v)
	}
	if b != BackendQemuRaw {
		t.Fatalf("backend=%s want qemu-raw", b)
	}
	if !hasSubstr(warnings, "raw-qemu backend") {
		t.Fatalf("expected raw-qemu warning, got %v", warnings)
	}
}

func TestSelectBackendNAS(t *testing.T) {
	if b := selectBackend(PlatformSynology, true, MgmtLayer{}); b != BackendSynology {
		t.Errorf("synology backend=%s", b)
	}
	if b := selectBackend(PlatformQNAP, true, MgmtLayer{}); b != BackendQNAP {
		t.Errorf("qnap backend=%s", b)
	}

	if b := selectBackend(PlatformWindows, true, MgmtLayer{HyperVWMI: true}); b != BackendHyperV {
		t.Errorf("windows+wmi backend=%s want hyper-v", b)
	}
	if b := selectBackend(PlatformWindows, true, MgmtLayer{HyperVWMI: false}); b != BackendWHPX {
		t.Errorf("windows-no-wmi backend=%s want whpx", b)
	}

	if b := selectBackend(PlatformTrueNAS, true, MgmtLayer{Libvirt: true}); b != BackendLibvirt {
		t.Errorf("truenas+libvirt backend=%s want libvirt", b)
	}
	if b := selectBackend(PlatformTrueNAS, true, MgmtLayer{Libvirt: false}); b != BackendTrueNAS {
		t.Errorf("truenas-no-libvirt backend=%s want truenas", b)
	}

	if b := selectBackend(PlatformMacOS, false, MgmtLayer{}); b != BackendHVF {
		t.Errorf("macos backend=%s want hvf", b)
	}
}

func TestAssembleEchoesSizing(t *testing.T) {
	r := assemble(PlatformLinux, "Test OS", "amd64",
		HWVirt{DevKVM: true, CPUFlag: "vmx", Nested: true},
		MgmtLayer{Libvirt: true, QemuPresent: true, QemuImgPresent: true},
		mkCap(7939, 6),
		Network{Bridgeable: true, BridgePresent: "br0", mDNSName: "portioneer.local"},
	)
	if r.Verdict != Green {
		t.Fatalf("verdict=%s", r.Verdict)
	}
	if r.Capacity.VRAMMiB != 4096 || r.Capacity.VCPU != 3 {
		t.Fatalf("sizing not echoed: %+v", r.Capacity)
	}
	if r.Network.MDNSName != "portioneer.local" {
		t.Fatalf("mDNS name lost: %q", r.Network.MDNSName)
	}
}

func hasSubstr(ss []string, sub string) bool {
	for _, s := range ss {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}
