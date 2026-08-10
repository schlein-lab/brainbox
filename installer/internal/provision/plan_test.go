package provision

import (
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
)

func TestPlanNetworkBridgedExisting(t *testing.T) {
	r := detect.Report{Verdict: detect.Green}
	r.Network.BridgePresent = "br0"
	r.Network.MDNSName = "portioneer.local"
	p := PlanNetwork(r)
	if p.Mode != "bridged" || p.Bridge != "br0" {
		t.Fatalf("want bridged/br0, got %s/%s", p.Mode, p.Bridge)
	}
	if !hasStep(p.Steps, "do NOT modify the bridge") {
		t.Errorf("must protect a pre-existing bridge: %v", p.Steps)
	}
}

func TestPlanNetworkNATWhenAmber(t *testing.T) {
	r := detect.Report{Verdict: detect.Amber}
	r.Network.MDNSName = "portioneer.local"
	p := PlanNetwork(r)
	if p.Mode != "nat" {
		t.Fatalf("amber should plan NAT, got %s", p.Mode)
	}
	if !hasStep(p.Steps, "port-forward") {
		t.Errorf("NAT plan should port-forward: %v", p.Steps)
	}
}

func TestPlanImageVerifiesBeforeConvert(t *testing.T) {
	p := PlanImage("brainarbeit", "/img", "amd64", sizing.Compute(64*1024, 8))
	if !strings.Contains(p.SigURL, ".minisig") {
		t.Errorf("expected minisig sig URL, got %s", p.SigURL)
	}
	verifyIdx, convertIdx := -1, -1
	for i, s := range p.Steps {
		if strings.Contains(s, "minisign -V") {
			verifyIdx = i
		}
		if strings.Contains(s, "qemu-img convert") {
			convertIdx = i
		}
	}
	if verifyIdx < 0 || convertIdx < 0 {
		t.Fatalf("missing verify/convert steps: %v", p.Steps)
	}
	if verifyIdx > convertIdx {
		t.Errorf("must VERIFY before CONVERT (verify@%d convert@%d)", verifyIdx, convertIdx)
	}
	if !hasStep(p.Steps, "separate DATA disk") {
		t.Errorf("must create a separate DATA disk: %v", p.Steps)
	}
}

func TestFirstBootHasWatchdogSelfTest(t *testing.T) {
	steps := FirstBootPlan("brainarbeit")
	if !hasStep(steps, "WATCHDOG SELF-TEST") {
		t.Errorf("first boot must self-test the watchdog: %v", steps)
	}
	if !hasStep(steps, "softdog fallback") {
		t.Errorf("first boot must mention softdog fallback: %v", steps)
	}
}

func TestBackendStubs(t *testing.T) {

	for _, b := range []detect.Backend{
		detect.BackendSynology, detect.BackendQNAP, detect.BackendTrueNAS,
		detect.BackendHyperV, detect.BackendWHPX, detect.BackendHVF,
		detect.BackendContainer,
	} {
		msg, ok := BackendStub(b)
		if !ok || !strings.Contains(msg, "TODO") {
			t.Errorf("backend %s should be a TODO(SDK) stub, got ok=%v msg=%q", b, ok, msg)
		}
	}

	for _, b := range []detect.Backend{detect.BackendLibvirt, detect.BackendQemuRaw} {
		if _, ok := BackendStub(b); ok {
			t.Errorf("backend %s is implemented (dry-run-mature), must NOT be a stub", b)
		}
	}
}

func hasStep(steps []string, sub string) bool {
	for _, s := range steps {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}
