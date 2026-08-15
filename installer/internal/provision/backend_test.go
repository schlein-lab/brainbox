package provision

import (
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
	"github.com/schlein-lab/brainarbeit/installer/internal/state"
)

func mkReport(backend detect.Backend, verdict detect.Verdict, bridge string) detect.Report {
	r := detect.Report{Arch: "amd64", Backend: backend, Verdict: verdict}
	r.Network.MDNSName = "portioneer.local"
	r.Network.PrimaryIface = "eth0"
	if bridge != "" {
		r.Network.BridgePresent = bridge
	}
	return r
}

func ctxFor(backend detect.Backend, verdict detect.Verdict, bridge string) BackendContext {
	r := mkReport(backend, verdict, bridge)
	return NewContext("brainarbeit", "/var/lib/brainarbeit", r, sizing.Compute(32*1024, 8))
}

func TestAllBackendsRegistered(t *testing.T) {
	want := []detect.Backend{
		detect.BackendLibvirt, detect.BackendQemuRaw, detect.BackendSynology,
		detect.BackendQNAP, detect.BackendTrueNAS, detect.BackendHyperV,
		detect.BackendWHPX, detect.BackendHVF, detect.BackendContainer,
	}
	for _, b := range want {
		d, ok := DriverFor(b)
		if !ok {
			t.Errorf("no driver registered for backend %s", b)
			continue
		}
		p := d.Plan(ctxFor(b, detect.Green, "br0"))
		if p.Backend != b {
			t.Errorf("driver for %s reported backend %s", b, p.Backend)
		}
		if len(p.Steps) == 0 {
			t.Errorf("backend %s produced no steps", b)
		}
		if len(p.State) == 0 {
			t.Errorf("backend %s recorded no factory-state entries", b)
		}
	}
}

func TestBackendMaturity(t *testing.T) {
	mature := map[detect.Backend]bool{detect.BackendLibvirt: true, detect.BackendQemuRaw: true}
	for _, b := range Backends() {
		p, _ := PlanFor(ctxFor(b, verdictFor(b), "br0"))
		if mature[b] {
			if p.IsStub() {
				t.Errorf("%s should be dry-run-mature, got stub", b)
			}
		} else {
			if !p.IsStub() {
				t.Errorf("%s should be a stub needing a vendor SDK", b)
			}

			if !hasStep(p.Steps, "TODO(SDK)") {
				t.Errorf("%s stub must carry a TODO(SDK) step: %v", b, p.Steps)
			}
		}
	}
}

func verdictFor(b detect.Backend) detect.Verdict {
	if b == detect.BackendContainer {
		return detect.Red
	}
	return detect.Green
}

func TestLibvirtPlanShape(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendLibvirt, detect.Green, "br0"))
	if !hasStep(p.Extra, "i6300esb") && !extraHas(p, "i6300esb") {
		t.Errorf("libvirt plan must render the i6300esb watchdog XML")
	}
	if !hasStep(p.Steps, "separate btrfs DATA disk") {
		t.Errorf("libvirt must create a separate DATA disk: %v", p.Steps)
	}
	if !stateHasReverse(p, "virsh undefine") {
		t.Errorf("define must record a virsh undefine reverse: %v", p.State)
	}
	if !stateHasReverse(p, "virsh destroy") {
		t.Errorf("start must record a virsh destroy reverse: %v", p.State)
	}
}

func TestQemuRawHasSupervisor(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendQemuRaw, detect.Green, "br0"))
	if !hasStep(p.Steps, "host-side supervisor unit") {
		t.Errorf("raw-qemu must write a supervisor unit: %v", p.Steps)
	}
	if !hasStep(p.Steps, "-accel kvm") && !stepContainsAll(p.Steps, "accel=kvm") {
		t.Errorf("raw-qemu must launch qemu with kvm accel: %v", p.Steps)
	}
}

func TestVendorBackendsReferenceTheirSDK(t *testing.T) {
	cases := map[detect.Backend]string{
		detect.BackendSynology: "SYNO.Virtualization.API",
		detect.BackendQNAP:     "/qvs/v1/vms",
		detect.BackendTrueNAS:  "midclt call vm.create",
		detect.BackendHyperV:   "New-VM",
		detect.BackendHVF:      "hvf",
		detect.BackendWHPX:     "whpx",
	}
	for b, want := range cases {
		p, _ := PlanFor(ctxFor(b, verdictFor(b), "br0"))
		if !hasStep(p.Steps, want) {
			t.Errorf("backend %s must reference %q in its plan: %v", b, want, p.Steps)
		}
	}

	hv, _ := PlanFor(ctxFor(detect.BackendHyperV, detect.Green, "br0"))
	if !hasStep(hv.Steps, "-Generation 2") {
		t.Errorf("Hyper-V must be a Generation-2 VM: %v", hv.Steps)
	}
}

func TestContainerBackendIsRedWithSupervisor(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendContainer, detect.Red, ""))
	if p.Degraded.Level != LevelRed {
		t.Fatalf("container backend must be RED, got %s", p.Degraded.Level)
	}
	for _, e := range p.State {
		if e.Action == state.ActFetchImage {
			t.Errorf("container must NOT fetch a qcow2 root image (it unpacks a rootfs)")
		}
	}
	if !extraHas(p, "host-side supervisor REQUIRED") {
		t.Errorf("container must REQUIRE a host-side supervisor (pn-init not PID1): %v", p.Extra)
	}
	if len(p.Degraded.UnregisteredTaskTypes) == 0 {
		t.Errorf("RED must leave device-empowerment task-types unregistered")
	}
}

func TestPreExistingBridgeProtected(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendLibvirt, detect.Green, "br-home"))
	found := false
	for _, e := range p.State {
		if e.Action == state.ActCreateBridge {
			found = true
			if !e.PreExisted {
				t.Errorf("a pre-existing bridge must be recorded PreExisted=true")
			}
		}
	}
	if !found {
		t.Error("expected a create-bridge state entry")
	}
}

func TestApplyProducesVerifiableChain(t *testing.T) {
	p, _ := PlanFor(ctxFor(detect.BackendLibvirt, detect.Green, "br0"))
	log := p.Apply(state.New())
	if err := log.Verify(); err != nil {
		t.Fatalf("applied chain must verify: %v", err)
	}
	if log.Head() == "" {
		t.Fatal("applied chain must have a head")
	}

	rev := log.ReversePlan()
	if len(rev) != len(p.State) {
		t.Errorf("reverse plan length %d != state entries %d", len(rev), len(p.State))
	}
}

func TestNoDriverForBackendNone(t *testing.T) {
	ctx := ctxFor(detect.BackendNone, detect.Green, "br0")
	ctx.Report.Backend = detect.BackendNone
	if _, err := PlanFor(ctx); err == nil {
		t.Fatal("BackendNone must have no driver and return an error")
	}
}

func extraHas(p BackendPlan, sub string) bool {
	for _, e := range p.Extra {
		if strings.Contains(e, sub) {
			return true
		}
	}
	return false
}

func stateHasReverse(p BackendPlan, sub string) bool {
	for _, e := range p.State {
		if strings.Contains(e.Reverse, sub) {
			return true
		}
	}
	return false
}

func stepContainsAll(steps []string, sub string) bool {
	for _, s := range steps {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}
