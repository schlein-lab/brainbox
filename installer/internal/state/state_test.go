package state

import (
	"strings"
	"testing"
)

func init() {

	now = func() string { return "2026-06-29T00:00:00Z" }
}

func TestAppendChainVerifies(t *testing.T) {
	l := New()
	l.Append(ActCreateBridge, "br-brainarbeit", false, "ip link delete br-brainarbeit", nil)
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)
	if err := l.Verify(); err != nil {
		t.Fatalf("fresh chain should verify: %v", err)
	}
	if l.Head() == "" {
		t.Fatal("head should be non-empty")
	}
	recs := l.Records()
	if recs[0].PrevHash != "" {
		t.Error("first record prev_hash must be empty")
	}
	if recs[1].PrevHash != recs[0].Hash {
		t.Error("chain linkage broken")
	}
}

func TestTamperDetected(t *testing.T) {
	l := New()
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)
	l.Append(ActStartDomain, "brainarbeit", false, "virsh destroy brainarbeit", nil)

	l.records[0].Target = "evil"
	if err := l.Verify(); err == nil {
		t.Fatal("tampering should be detected")
	}
}

func TestReversePlanOrderAndSkip(t *testing.T) {
	l := New()
	l.Append(ActCreateBridge, "br0", true, "", nil)
	l.Append(ActFetchImage, "/img/root.qcow2", false, "rm -f /img/root.qcow2", nil)
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)

	plan := l.ReversePlan()
	if len(plan) != 3 {
		t.Fatalf("want 3 steps, got %d: %v", len(plan), plan)
	}

	if !strings.Contains(plan[0], "define-domain") {
		t.Errorf("reverse should start with newest (define-domain): %v", plan)
	}
	if !strings.Contains(plan[2], "SKIP") || !strings.Contains(plan[2], "br0") {
		t.Errorf("pre-existing bridge must be SKIPPED in reverse plan: %v", plan)
	}
}

func TestAppendOnlyDeterministicHash(t *testing.T) {
	mk := func() string {
		l := New()
		l.Append(ActCreateDisk, "/img/data.qcow2", false, "rm -f /img/data.qcow2", map[string]string{"fs": "btrfs"})
		return l.Head()
	}
	if mk() != mk() {
		t.Error("identical inputs should produce identical chain heads")
	}
}

func TestMarshalJSONLines(t *testing.T) {
	l := New()
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)
	var sb strings.Builder
	if err := l.Marshal(&sb); err != nil {
		t.Fatal(err)
	}
	out := sb.String()
	if !strings.HasSuffix(out, "\n") || !strings.Contains(out, `"action":"define-domain"`) {
		t.Errorf("unexpected JSONL output: %q", out)
	}
}
