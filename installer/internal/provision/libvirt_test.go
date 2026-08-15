package provision

import (
	"encoding/xml"
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
)

func TestDomainXMLWellFormed(t *testing.T) {
	p := DomainParamsFrom("brainarbeit", "amd64", "br0", sizing.Compute(7939, 6), "/var/lib/libvirt/images")
	out, err := DomainXML(p)
	if err != nil {
		t.Fatal(err)
	}

	var dump any
	if err := xml.Unmarshal([]byte(out), &dump); err != nil {
		t.Fatalf("generated domain XML is not well-formed: %v\n%s", err, out)
	}
}

func TestDomainXMLLoadBearingElements(t *testing.T) {
	p := DomainParamsFrom("brainarbeit", "amd64", "br0", sizing.Compute(64*1024, 8), "/img")
	out, _ := DomainXML(p)
	must := []string{
		`<watchdog model='i6300esb' action='reset'/>`,
		`bus='virtio'`,
		`<source bridge='br0'/>`,
		`<memory unit='MiB'>16384</memory>`,
		`<vcpu placement='static'>4</vcpu>`,
		`brainarbeit-data`,
		`<serial>brainarbeit-data</serial>`,
	}
	for _, m := range must {
		if !strings.Contains(out, m) {
			t.Errorf("domain XML missing %q\n---\n%s", m, out)
		}
	}

	if n := strings.Count(out, "device='disk'"); n != 2 {
		t.Errorf("want 2 disks (root+DATA), got %d", n)
	}
}

func TestDomainXMLNATWhenNoBridge(t *testing.T) {
	p := DomainParamsFrom("brainarbeit", "amd64", "", sizing.Compute(16*1024, 4), "/img")
	out, _ := DomainXML(p)
	if strings.Contains(out, "type='bridge'") {
		t.Error("expected NAT interface when no bridge given")
	}
	if !strings.Contains(out, `<source network='default'/>`) {
		t.Errorf("expected NAT 'default' network\n%s", out)
	}
}

func TestArm64Machine(t *testing.T) {
	p := DomainParamsFrom("brainarbeit", "arm64", "br0", sizing.Compute(16*1024, 4), "/img")
	if p.Arch != "aarch64" || p.Machine != "virt" {
		t.Fatalf("arm64 arch/machine wrong: %s/%s", p.Arch, p.Machine)
	}
	out, _ := DomainXML(p)
	if !strings.Contains(out, "machine='virt'") {
		t.Errorf("arm64 should use 'virt' machine\n%s", out)
	}
}

func TestDeterministicUUID(t *testing.T) {
	a := deterministicUUID("brainarbeit")
	b := deterministicUUID("brainarbeit")
	c := deterministicUUID("other")
	if a != b {
		t.Errorf("UUID not deterministic: %s != %s", a, b)
	}
	if a == c {
		t.Errorf("different names produced same UUID")
	}
	if len(a) != 36 {
		t.Errorf("UUID wrong shape: %q", a)
	}
}
