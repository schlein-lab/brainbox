package provision

import (
	"fmt"
	"sort"
	"strings"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
	"github.com/schlein-lab/brainarbeit/installer/internal/state"
)

type Maturity string

const (

	MaturityDryRun Maturity = "dry-run"

	MaturityStub Maturity = "stub-needs-sdk"
)

type BackendContext struct {
	Name    string
	DataDir string
	Arch    string
	Spec    sizing.Spec
	Report  detect.Report
	Net     NetworkPlan
	Image   ImagePlan
}

type BackendPlan struct {
	Backend  detect.Backend
	Title    string
	Maturity Maturity
	Steps    []string

	State []StateEntry

	Degraded DegradedBanner

	Extra []string
}

func (p BackendPlan) IsStub() bool { return p.Maturity == MaturityStub }

type StateEntry struct {
	Action     state.Action
	Target     string
	PreExisted bool
	Reverse    string
	Meta       map[string]string
}

func (p BackendPlan) Apply(log *state.Log) *state.Log {
	for _, e := range p.State {
		log.Append(e.Action, e.Target, e.PreExisted, e.Reverse, e.Meta)
	}
	return log
}

type Driver interface {

	Backend() detect.Backend

	Plan(ctx BackendContext) BackendPlan
}

var registry = map[detect.Backend]Driver{}

func register(d Driver) { registry[d.Backend()] = d }

func DriverFor(b detect.Backend) (Driver, bool) {
	d, ok := registry[b]
	return d, ok
}

func Backends() []detect.Backend {
	out := make([]detect.Backend, 0, len(registry))
	for b := range registry {
		out = append(out, b)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

func NewContext(name, dataDir string, r detect.Report, spec sizing.Spec) BackendContext {
	return BackendContext{
		Name:    name,
		DataDir: dataDir,
		Arch:    r.Arch,
		Spec:    spec,
		Report:  r,
		Net:     PlanNetwork(r),
		Image:   PlanImage(name, dataDir, r.Arch, spec),
	}
}

func PlanFor(ctx BackendContext) (BackendPlan, error) {
	b := ctx.Report.Backend
	d, ok := DriverFor(b)
	if !ok {
		return BackendPlan{
			Backend:  b,
			Title:    "no provisioning driver",
			Maturity: MaturityStub,
			Steps: []string{
				fmt.Sprintf("no driver registered for backend %q", b),
				"a real install would ABORT; detect's blockers explain why",
			},
		}, fmt.Errorf("no driver for backend %q", b)
	}
	return d.Plan(ctx), nil
}

func imageStateEntries(ctx BackendContext) []StateEntry {
	return []StateEntry{
		{
			Action: state.ActFetchImage, Target: ctx.Image.RootImg, PreExisted: false,
			Reverse: "rm -f " + ctx.Image.RootImg,
			Meta:    map[string]string{"src": ctx.Image.ImageURL},
		},
		{
			Action: state.ActCreateDisk, Target: ctx.Image.DataImg, PreExisted: false,
			Reverse: "rm -f " + ctx.Image.DataImg + " (DATA kept unless --purge)",
		},
	}
}

func networkStateEntry(ctx BackendContext) StateEntry {
	np := ctx.Net
	switch {
	case np.Mode == "bridged" && ctx.Report.Network.BridgePresent != "":
		return StateEntry{Action: state.ActCreateBridge, Target: np.Bridge, PreExisted: true}
	case np.Mode == "bridged":
		return StateEntry{Action: state.ActCreateBridge, Target: np.Bridge, PreExisted: false, Reverse: "ip link delete " + np.Bridge}
	default:
		return StateEntry{Action: state.ActCreateNATNet, Target: np.NATNetwork, PreExisted: true}
	}
}

func SummarizePlan(p BackendPlan) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s  [%s, maturity=%s]\n", p.Title, p.Backend, p.Maturity)
	for _, s := range p.Steps {
		b.WriteString("  - " + s + "\n")
	}
	for _, e := range p.Extra {
		b.WriteString("  + " + e + "\n")
	}
	if p.Degraded.Level != "" {
		b.WriteString(p.Degraded.Render())
	}
	return b.String()
}
