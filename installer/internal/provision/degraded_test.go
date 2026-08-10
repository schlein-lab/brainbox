package provision

import (
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
)

func TestDegradeGreenHasNoBanner(t *testing.T) {
	r := mkReport(detect.BackendLibvirt, detect.Green, "br0")
	b := DegradeFor(r)
	if b.Level != LevelGreen {
		t.Fatalf("green report must yield no degradation, got %s", b.Level)
	}
	if b.Render() != "" {
		t.Errorf("green must render an empty banner")
	}
}

func TestDegradeAmberNoBridge(t *testing.T) {
	r := mkReport(detect.BackendLibvirt, detect.Amber, "")
	b := DegradeFor(r)
	if b.Level != LevelAmber {
		t.Fatalf("amber expected, got %s", b.Level)
	}
	if !lossesMention(b, "does NOT get its own LAN IP") {
		t.Errorf("amber/no-bridge must say the node loses its LAN IP: %v", b.Losses)
	}
	if !subsMention(b, "port-forward") {
		t.Errorf("amber/no-bridge must substitute NAT port-forwards: %v", b.Substitutes)
	}
}

func TestDegradeAmberNestedMicroVMInContainer(t *testing.T) {
	r := mkReport(detect.BackendLibvirt, detect.Amber, "")
	r.HWVirt.InContainer = true
	r.HWVirt.Nested = false
	b := DegradeFor(r)
	if b.Level != LevelAmber {
		t.Fatalf("amber expected, got %s", b.Level)
	}
	if !strings.Contains(b.Headline, "nested micro-VM inside a container") {
		t.Errorf("must identify the nested-microVM-in-container cause: %q", b.Headline)
	}
	if !lossesMention(b, "CELL-VM isolation") {
		t.Errorf("must call out the CELL-VM isolation loss: %v", b.Losses)
	}
}

func TestDegradeRedIsLoudAndUnregistersDeviceTaskTypes(t *testing.T) {
	r := mkReport(detect.BackendContainer, detect.Red, "")
	b := DegradeFor(r)
	if b.Level != LevelRed {
		t.Fatalf("red expected, got %s", b.Level)
	}
	if !lossesMention(b, "pn-init is NOT PID1") {
		t.Errorf("RED must state pn-init is not PID1: %v", b.Losses)
	}
	if !lossesMention(b, "hardware watchdog") {
		t.Errorf("RED must state the HW watchdog/reset is unavailable: %v", b.Losses)
	}
	if !subsMention(b, "HOST-SIDE supervisor") {
		t.Errorf("RED must substitute a host-side supervisor: %v", b.Substitutes)
	}

	must := []string{"device.bind", "device.flash", "firmware.write"}
	for _, m := range must {
		found := false
		for _, tt := range b.UnregisteredTaskTypes {
			if tt == m {
				found = true
			}
		}
		if !found {
			t.Errorf("RED must leave %s unregistered: %v", m, b.UnregisteredTaskTypes)
		}
	}

	out := b.Render()
	if !strings.Contains(out, "====") || !strings.Contains(out, "RED") {
		t.Errorf("RED banner must be loud/boxed:\n%s", out)
	}
}

func TestWatchdogSoftBackendDowngradesGreenToAmber(t *testing.T) {
	for _, b := range []detect.Backend{
		detect.BackendSynology, detect.BackendQNAP, detect.BackendHyperV,
		detect.BackendWHPX, detect.BackendHVF,
	} {
		p, _ := PlanFor(ctxFor(b, detect.Green, "br0"))
		if p.Degraded.Level != LevelAmber {
			t.Errorf("backend %s on a green host must downgrade to amber (soft watchdog), got %s", b, p.Degraded.Level)
		}
		if !lossesMention(p.Degraded, "watchdog") {
			t.Errorf("backend %s amber must mention the watchdog no-op: %v", b, p.Degraded.Losses)
		}
	}
}

func TestLibvirtGreenStaysGreen(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendLibvirt, detect.Green, "br0"))
	if p.Degraded.Level != LevelGreen {
		t.Errorf("libvirt on a green host must stay green, got %s", p.Degraded.Level)
	}
}

func lossesMention(b DegradedBanner, sub string) bool {
	for _, l := range b.Losses {
		if strings.Contains(l, sub) {
			return true
		}
	}
	return false
}

func subsMention(b DegradedBanner, sub string) bool {
	for _, s := range b.Substitutes {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}
