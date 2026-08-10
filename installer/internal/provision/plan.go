package provision

import (
	"fmt"
	"strings"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
)

type NetworkPlan struct {
	Mode       string
	Bridge     string
	NATNetwork string
	MDNSName   string
	DHCP       bool
	Steps      []string
	Notes      []string
}

func PlanNetwork(r detect.Report) NetworkPlan {
	p := NetworkPlan{MDNSName: r.Network.MDNSName, DHCP: r.Network.DHCP}
	if r.Network.BridgePresent != "" {
		p.Mode = "bridged"
		p.Bridge = r.Network.BridgePresent
		p.Steps = []string{
			fmt.Sprintf("attach VM NIC to existing host bridge %q (do NOT modify the bridge)", p.Bridge),
			"request a DHCP lease on the bridged NIC; persist last-known-good IP to DATA/config",
			"advertise " + p.MDNSName + " via mDNS responder in-guest",
		}
		p.Notes = append(p.Notes, "pre-existing bridge will never be reconfigured or torn down by uninstall")
	} else if r.Verdict == detect.Green {
		p.Mode = "bridged"
		p.Bridge = "br-brainarbeit"
		p.Steps = []string{
			"create host bridge br-brainarbeit enslaving the primary iface " + r.Network.PrimaryIface + " (RECORDED for reversal)",
			"attach VM NIC to br-brainarbeit",
			"request DHCP lease; persist last-known-good IP",
			"advertise " + p.MDNSName + " via mDNS",
		}
		p.Notes = append(p.Notes, "this bridge is factory-created → uninstall reverses it")
	} else {
		p.Mode = "nat"
		p.NATNetwork = "default"
		p.Steps = []string{
			"use libvirt NAT network \"default\" (or create it if absent — RECORDED)",
			"port-forward host:22000→guest:22 (sshd-sacred) and host:8443→guest:443 (portal)",
			"mDNS discovery degraded behind NAT; 127.0.0.1 desktop shell is the escape hatch",
		}
		p.Notes = append(p.Notes, "AMBER: no LAN bridge → the node does not get its own LAN IP")
	}
	return p
}

type ImagePlan struct {
	ImageURL  string
	SigURL    string
	PubKeyHex string
	RootImg   string
	DataImg   string
	Steps     []string
}

func PlanImage(name, dataDir, arch string, spec sizing.Spec) ImagePlan {
	root := dataDir + "/" + name + "-root-a.qcow2"
	data := dataDir + "/" + name + "-data.qcow2"
	imgURL := "https://images.brainarbeit.dev/golden-" + arch + ".raw.zst"
	return ImagePlan{
		ImageURL:  imgURL,
		SigURL:    imgURL + ".minisig",
		PubKeyHex: "RWQ<PINNED-MINISIGN-PUBKEY>",
		RootImg:   root,
		DataImg:   data,
		Steps: []string{
			"fetch " + imgURL,
			"fetch " + imgURL + ".minisig",
			"minisign -Vm <image> -P <pinned-pubkey>   # ABORT install on verify failure",
			"decompress + convert to qcow2: qemu-img convert -O qcow2 <image> " + root,
			fmt.Sprintf("create separate DATA disk: qemu-img create -f qcow2 %s %dG  (mkfs.btrfs in-guest first boot)", data, spec.DataGiB),
			fmt.Sprintf("root slot sized %dG, vRAM %dMiB, vCPU %d", spec.RootGiB, spec.VRAMMiB, spec.VCPU),
		},
	}
}

func FirstBootPlan(name string) []string {
	return []string{
		"start the domain (DRY-RUN: not started)",
		"attach to serial console; wait for pn-init PID1 banner",
		"WATCHDOG SELF-TEST: confirm the host actually hard-resets the guest via i6300esb (auto-softdog fallback if the guest driver is absent)",
		"verify sacred sshd → pnd → portal → pn-llmd service order came up",
		"open cockpit onboarding: paste the LLM credential (claude setup-token by default), name the node, confirm network, seed SAFE backlog",
		"register " + name + " in the factory-state log as active",
	}
}

func BackendStub(b detect.Backend) (string, bool) {
	d, ok := DriverFor(b)
	if !ok {
		return "", false
	}
	p := d.Plan(probeContext(b))
	if !p.IsStub() {
		return "", false
	}
	return "TODO(SDK): " + p.Title + " — needs a vendor SDK/API call (see plan steps).", true
}

func probeContext(b detect.Backend) BackendContext {
	r := detect.Report{Arch: "amd64", Backend: b, Verdict: detect.Green}
	r.Network.MDNSName = "portioneer.local"
	if b == detect.BackendContainer {
		r.Verdict = detect.Red
	}
	spec := sizing.Compute(16*1024, 4)
	return NewContext("brainarbeit", "/var/lib/brainarbeit", r, spec)
}

func Summarize(title string, steps []string) string {
	var b strings.Builder
	b.WriteString(title + "\n")
	for _, s := range steps {
		b.WriteString("  - " + s + "\n")
	}
	return b.String()
}
