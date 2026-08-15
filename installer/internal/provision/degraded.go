package provision

import (
	"strings"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
)

type DegradedLevel string

const (
	LevelGreen DegradedLevel = ""
	LevelAmber DegradedLevel = "amber"
	LevelRed   DegradedLevel = "red"
)

type DegradedBanner struct {
	Level DegradedLevel

	Headline string

	Losses []string

	Substitutes []string

	UnregisteredTaskTypes []string
}

func DegradeFor(r detect.Report) DegradedBanner {
	switch r.Verdict {
	case detect.Green:
		return DegradedBanner{Level: LevelGreen}

	case detect.Amber:
		b := DegradedBanner{
			Level:    LevelAmber,
			Headline: "AMBER — running, but degraded (no LAN bridge / no nested-virt).",
		}

		if r.HWVirt.InContainer && !r.HWVirt.Nested {

			b.Headline = "AMBER — nested micro-VM inside a container (no nested-virt)."
			b.Losses = []string{
				"per-user CELL-VM isolation (§16) falls back to namespaced cells (no L1 KVM-in-KVM)",
				"the i6300esb hardware watchdog may be a silent no-op → softdog fallback is load-bearing",
			}
			b.Substitutes = []string{
				"run the appliance as a micro-VM under the container's user-mode KVM where present",
				"auto-softdog watchdog substitute; first-boot self-test confirms which path is real",
			}
		} else {

			b.Losses = []string{
				"the node does NOT get its own LAN IP (lives behind host NAT)",
				"mDNS/zeroconf LAN discovery is degraded — peers cannot find portioneer.local directly",
			}
			b.Substitutes = []string{
				"NAT + port-forward host:22000→guest:22 (sacred sshd) and host:8443→guest:443 (portal)",
				"the 127.0.0.1 cockpit desktop shell is the always-available escape hatch",
			}
		}
		return b

	default:
		return DegradedBanner{
			Level:    LevelRed,
			Headline: "RED — NO KVM: bare namespaced container. NOT a real appliance.",
			Losses: []string{
				"pn-init is NOT PID1 — the host PID1 owns the namespace; the L0 supervision tree is emulated",
				"the i6300esb hardware watchdog and L0 hard-reset / crash-loop HW recovery are UNAVAILABLE",
				"no real isolation boundary for per-user CELL-VMs (§16) — namespaced cells only",
				"device-empowerment and firmware task-types are UNREGISTERED (no device.bind/device.flash/cap.acquire)",
			},
			Substitutes: []string{
				"a HOST-SIDE supervisor (systemd unit / container restart=on-failure) substitutes for pn-init's watchdog",
				"a soft, in-process liveness loop pets nothing real — it can only restart the container, not reset hardware",
				"the loud self-reporting banner below is emitted at every boot and surfaced in the cockpit health badge",
			},
			UnregisteredTaskTypes: []string{
				"device.probe", "device.bind", "device.flash", "cap.acquire", "firmware.write",
			},
		}
	}
}

func (b DegradedBanner) Render() string {
	if b.Level == LevelGreen {
		return ""
	}
	var sb strings.Builder
	if b.Level == LevelRed {
		const bar = "    ============================================================\n"
		sb.WriteString(bar)
		sb.WriteString("    !!  " + b.Headline + "\n")
		sb.WriteString(bar)
	} else {
		sb.WriteString("    -- " + b.Headline + "\n")
	}
	for _, l := range b.Losses {
		sb.WriteString("    LOST : " + l + "\n")
	}
	for _, s := range b.Substitutes {
		sb.WriteString("    sub  : " + s + "\n")
	}
	for _, t := range b.UnregisteredTaskTypes {
		sb.WriteString("    no-reg task_type: " + t + "\n")
	}
	return sb.String()
}
