package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/schlein-lab/brainarbeit/installer/internal/detect"
	"github.com/schlein-lab/brainarbeit/installer/internal/provision"
	"github.com/schlein-lab/brainarbeit/installer/internal/selfupdate"
	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
	"github.com/schlein-lab/brainarbeit/installer/internal/sizing"
	"github.com/schlein-lab/brainarbeit/installer/internal/state"
)

var version = "dev"

const usage = `pn-factory — Brainarbeit host installer / VM factory (L-1)

Commands:
  detect      read-only host/hypervisor/capacity/network verdict (green/amber/red)
  install     provision + boot the appliance VM   (THIS BUILD: DRY-RUN only)
  update      signed A/B self-update plan + state machine        (DRY-RUN/simulated)
  uninstall   tear down in reverse (keeps DATA unless --purge)   [skeleton]
  rollback    revert a failed self-update (A/B)                  [skeleton]
  console     serial escape hatch into the appliance             [skeleton]
  version     print version

Run "pn-factory <command> -h" for command flags.`

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, usage)
		os.Exit(2)
	}
	switch os.Args[1] {
	case "detect":
		os.Exit(cmdDetect(os.Args[2:]))
	case "install":
		os.Exit(cmdInstall(os.Args[2:]))
	case "update":
		os.Exit(cmdUpdate(os.Args[2:]))
	case "uninstall":
		os.Exit(cmdUninstall(os.Args[2:]))
	case "rollback":
		os.Exit(cmdRollback(os.Args[2:]))
	case "console":
		os.Exit(cmdConsole(os.Args[2:]))
	case "version", "-v", "--version":
		fmt.Println("pn-factory", version)
	case "-h", "--help", "help":
		fmt.Println(usage)
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n\n%s\n", os.Args[1], usage)
		os.Exit(2)
	}
}

func cmdDetect(args []string) int {
	fs := flag.NewFlagSet("detect", flag.ExitOnError)
	asJSON := fs.Bool("json", false, "emit the structured report as JSON")
	_ = fs.Parse(args)

	r := detect.Run()
	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(r)
		return exitForVerdict(r.Verdict)
	}
	printReport(r)
	return exitForVerdict(r.Verdict)
}

func printReport(r detect.Report) {
	fmt.Printf("== pn-factory detect (read-only) ==\n\n")
	fmt.Printf("  platform   : %s (%s, %s)\n", r.Platform, r.OSPretty, r.Arch)
	fmt.Printf("  HW virt    : /dev/kvm=%v  cpu=%s  nested=%v  in-container=%v\n",
		r.HWVirt.DevKVM, dash(r.HWVirt.CPUFlag), r.HWVirt.Nested, r.HWVirt.InContainer)
	fmt.Printf("  mgmt layer : libvirt=%v  qemu=%v  qemu-img=%v  → backend=%s\n",
		r.Mgmt.Libvirt, r.Mgmt.QemuPresent, r.Mgmt.QemuImgPresent, r.Mgmt.Backend)
	fmt.Printf("  capacity   : host %d MiB / %d cores → vRAM=%d MiB  vCPU=%d  root=%dG  DATA=%dG\n",
		r.Capacity.RAMMiB, r.Capacity.Cores, r.Capacity.VRAMMiB, r.Capacity.VCPU,
		r.Capacity.RootGiB, r.Capacity.DataGiB)
	fmt.Printf("  network    : iface=%s ip=%s gw=%s dhcp=%v bridge=%s bridgeable=%v\n",
		dash(r.Network.PrimaryIface), dash(r.Network.PrimaryIP), dash(r.Network.DefaultGW),
		r.Network.DHCP, dash(r.Network.BridgePresent), r.Network.Bridgeable)
	fmt.Printf("\n  VERDICT    : %s\n", verdictBanner(r.Verdict))
	for _, s := range r.Reasons {
		fmt.Printf("    reason  : %s\n", s)
	}
	for _, s := range r.Warnings {
		fmt.Printf("    warn    : %s\n", s)
	}
	for _, s := range r.Blockers {
		fmt.Printf("    BLOCKER : %s\n", s)
	}
}

func cmdInstall(args []string) int {
	fs := flag.NewFlagSet("install", flag.ExitOnError)
	asJSON := fs.Bool("json", false, "emit the generated artifacts as JSON")
	name := fs.String("name", "brainarbeit", "appliance VM / node name")
	dataDir := fs.String("data-dir", "/var/lib/libvirt/images", "where disk images would be created")
	force := fs.Bool("force", false, "(ignored in this build; install is always DRY-RUN)")
	_ = fs.Parse(args)
	_ = force

	r := detect.Run()

	if r.Backend == detect.BackendNone {
		fmt.Fprintf(os.Stderr, "WARNING: backend=none — KVM is present but neither libvirt nor qemu-system-* is installed.\n")
		fmt.Fprintf(os.Stderr, "         A real install would ABORT here. Showing what detect found anyway.\n\n")
	}

	ctx := provision.NewContext(*name, *dataDir, r, sizingFrom(r))
	bp, perr := provision.PlanFor(ctx)
	firstBoot := provision.FirstBootPlan(*name)

	log := bp.Apply(state.New())
	headSig := signStateHead(log)

	if *asJSON {
		out := map[string]any{
			"dry_run":         true,
			"verdict":         r.Verdict,
			"backend":         r.Backend,
			"backend_plan":    bp,
			"network_plan":    ctx.Net,
			"image_plan":      ctx.Image,
			"first_boot_plan": firstBoot,
			"factory_state":   log.Records(),
			"state_head":      log.Head(),
			"state_head_sig":  headSig,
		}
		if perr != nil {
			out["error"] = perr.Error()
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(out)
		return installExit(r, bp)
	}

	fmt.Printf("== pn-factory install  (DRY-RUN — NOTHING IS DEFINED OR STARTED) ==\n\n")
	fmt.Printf("verdict=%s backend=%s name=%s arch=%s\n\n", r.Verdict, r.Backend, *name, r.Arch)
	fmt.Print(provision.Summarize("[1] image fetch + verify (minisign) + convert:", ctx.Image.Steps))
	fmt.Println()
	fmt.Print(provision.Summarize(fmt.Sprintf("[2] network plan (%s, mDNS %s):", ctx.Net.Mode, ctx.Net.MDNSName), ctx.Net.Steps))
	for _, n := range ctx.Net.Notes {
		fmt.Printf("  note: %s\n", n)
	}
	fmt.Println()
	fmt.Printf("[3] backend provisioning plan:\n\n")
	fmt.Print(indent(provision.SummarizePlan(bp), "  "))
	if perr != nil {
		fmt.Printf("  (note: %v)\n", perr)
	}
	fmt.Println()
	fmt.Print(provision.Summarize("[4] first boot + watchdog self-test:", firstBoot))
	fmt.Println()
	fmt.Printf("[5] factory-state log (append-only, hash-chained; head=%s):\n", short(log.Head()))
	for _, rec := range log.Records() {
		pre := ""
		if rec.PreExisted {
			pre = "  [pre-existed: uninstall will NOT touch]"
		}
		fmt.Printf("    #%d %-18s %s%s\n", rec.Seq, rec.Action, rec.Target, pre)
	}
	fmt.Printf("    head signed (minisign over chain head): %s...\n", short(firstLine(headSig)))
	fmt.Printf("\nDRY-RUN complete. No VM, disk, bridge, or network was created.\n")
	return installExit(r, bp)
}

func installExit(r detect.Report, bp provision.BackendPlan) int {
	if r.Backend == detect.BackendNone {
		return 1
	}
	return 0
}

func cmdUpdate(args []string) int {
	fs := flag.NewFlagSet("update", flag.ExitOnError)
	asJSON := fs.Bool("json", false, "emit the state-machine transcript as JSON")
	channelName := fs.String("channel", sign.ChannelStable, "pinned update channel (stable|beta|edge)")
	from := fs.String("from", "1.0.0", "running image version")
	to := fs.String("to", "1.1.0", "candidate image version")
	activeSlot := fs.String("active-slot", "a", "currently-booted slot (a|b)")
	failCanary := fs.Bool("fail-canary", false, "simulate a failed greenboot canary → auto-revert")
	noConsent := fs.Bool("no-consent", false, "simulate activation WITHOUT a human consent nonce (must be refused)")
	pubKeyFile := fs.String("pubkey-file", "", "pin this minisign PUBLIC key file for the channel (e.g. ops/release-public/kit/minisign.pub); without it the channel key is an unusable placeholder and every update is REFUSED")
	selfTest := fs.Bool("selftest-trustgate", false, "prove the verification gate with a throwaway key: mint an EPHEMERAL minisign keypair, really sign the image bytes, pin the derived key, and run the machine through the REAL Ed25519 verifier")
	_ = fs.Parse(args)

	chs := sign.DefaultChannels()
	ch, ok := chs[*channelName]
	if !ok {
		fmt.Fprintf(os.Stderr, "unknown channel %q (have stable|beta|edge)\n", *channelName)
		return 2
	}

	if *pubKeyFile != "" {
		b, err := os.ReadFile(*pubKeyFile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "cannot read pinned public key %q: %v\n", *pubKeyFile, err)
			return 2
		}
		ch.PubKey = string(b)
	}
	keyState := "NO USABLE PINNED KEY — placeholder; every update will be REFUSED (pass --pubkey-file to pin a real one)"
	havePinnedKey := false
	if err := ch.Parse(); err == nil {
		havePinnedKey = true
		keyState = fmt.Sprintf("pinned minisign key %X (real trust anchor)", ch.Key().KeyID)
	} else if *pubKeyFile != "" {
		fmt.Fprintf(os.Stderr, "pinned key file %q is unusable: %v\n", *pubKeyFile, err)
		return 2
	}

	var selfTestKey *sign.SecretKey
	if *selfTest {
		sk, err := mintEphemeralKey()
		if err != nil {
			fmt.Fprintf(os.Stderr, "selftest key: %v\n", err)
			return 2
		}
		selfTestKey = &sk
		ch.PubKey = encodePubKey(sk.PublicKey())
		if err := ch.Parse(); err != nil {
			fmt.Fprintf(os.Stderr, "selftest key unusable: %v\n", err)
			return 2
		}
		havePinnedKey = true
		keyState = fmt.Sprintf("EPHEMERAL SELF-TEST key %X — proves the verification gate only; NO release image can verify against it", ch.Key().KeyID)
	}

	plan := selfupdate.Plan{
		Channel:     ch,
		FromVersion: *from,
		ToVersion:   *to,
		Arch:        detect.Run().Arch,
		ActiveSlot:  selfupdate.Slot(*activeSlot),
	}
	m := selfupdate.New(plan)

	m.AllowUnwiredEffects = true

	if err := m.Queue(); err != nil {
		fmt.Fprintf(os.Stderr, "queue refused: %v\n", err)
		return emitUpdate(m, *asJSON, ch, keyState, 1)
	}

	consent := selfupdate.NewNonceConsent()
	rc := 0
	if *noConsent {

		if err := m.Activate("brain-cannot-mint-this", consent); err == nil {
			fmt.Fprintln(os.Stderr, "SECURITY BUG: activation succeeded without a human nonce")
			rc = 1
		} else {
			fmt.Fprintf(os.Stderr, "(expected) activation refused without human consent: %v\n", err)
			rc = 0
		}
		return emitUpdate(m, *asJSON, ch, keyState, rc)
	}

	const nonce = "HUMAN-HOLD-TO-CONFIRM-0xCAFE"
	consent.Mint(nonce, *to)
	if err := m.Activate(nonce, consent); err != nil {
		fmt.Fprintf(os.Stderr, "activation refused: %v\n", err)
		return emitUpdate(m, *asJSON, ch, keyState, 1)
	}

	inner := selfupdate.NewDryRunDriver()
	if *failCanary {
		inner.CanaryHealthy = false
	}
	var drv selfupdate.Driver = inner
	switch {
	case selfTestKey != nil:

		drv = selfupdate.VerifyingDriver{
			Driver:  signedFetchDriver{DryRunDriver: inner, key: *selfTestKey},
			Channel: ch,
		}
	case havePinnedKey:
		drv = selfupdate.VerifyingDriver{Driver: inner, Channel: ch}
	}
	final, err := m.Run(drv)
	if err != nil && final != selfupdate.StateReverted {

		rc = 1
	}
	if final == selfupdate.StateReverted {
		rc = 0
	}

	if !*asJSON {
		fmt.Print("\n[driver steps — TODO(wire) = needs real btrfs/grub wiring; verification is NOT simulated]\n")
		for _, l := range inner.Log {
			fmt.Printf("    %s\n", l)
		}
		if !havePinnedKey {
			fmt.Print("\nNO PINNED KEY: this build cannot verify an update, so it REFUSES to install one.\n" +
				"    That refusal is the correct behaviour, not a bug. To make updates possible a release must\n" +
				"    (a) embed a real pinned key per channel in sign.DefaultChannels(), and\n" +
				"    (b) publish a detached .minisig next to every image the channel serves.\n")
		}
	}
	return emitUpdate(m, *asJSON, ch, keyState, rc)
}

type signedFetchDriver struct {
	*selfupdate.DryRunDriver
	key sign.SecretKey
}

func (d signedFetchDriver) Fetch(p selfupdate.Plan) ([]byte, string, error) {
	img, _, err := d.DryRunDriver.Fetch(p)
	if err != nil {
		return nil, "", err
	}
	return img, d.key.SignDetached(img, "selftest image "+p.ToVersion), nil
}

func mintEphemeralKey() (sign.SecretKey, error) {
	var id [8]byte
	seed := make([]byte, ed25519.SeedSize)
	if _, err := rand.Read(seed); err != nil {
		return sign.SecretKey{}, err
	}
	if _, err := rand.Read(id[:]); err != nil {
		return sign.SecretKey{}, err
	}
	return sign.NewSecretKey(id, seed)
}

func encodePubKey(pk sign.PublicKey) string {
	body := make([]byte, 0, 42)
	body = append(body, 'E', 'd')
	body = append(body, pk.KeyID[:]...)
	body = append(body, pk.Key...)
	return "untrusted comment: ephemeral pn-factory selftest key\n" +
		base64.StdEncoding.EncodeToString(body) + "\n"
}

func emitUpdate(m *selfupdate.Machine, asJSON bool, ch sign.Channel, keyState string, rc int) int {
	if asJSON {
		out := map[string]any{
			"dry_run":                true,
			"verification_simulated": false,
			"effects_wired":          false,
			"channel":                ch.Name,
			"pinned_key":             keyState,
			"final_state":            m.State,
			"terminal":               m.State.Terminal(),
			"plan":                   m.Plan,
			"transitions":            m.Steps,
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		_ = enc.Encode(out)
		return rc
	}
	fmt.Printf("== pn-factory update  (PLAN PREVIEW — NO IMAGE IS SWAPPED) ==\n")
	fmt.Printf("   signature verification is REAL (never simulated); the disk/bootloader steps are stubs.\n\n")
	fmt.Printf("channel=%s  pinned key: %s\n", ch.Name, keyState)
	fmt.Printf("plan: %s→%s  active-slot=%s → target-slot=%s  (no-downgrade gate enforced)\n\n",
		m.Plan.FromVersion, m.Plan.ToVersion, m.Plan.ActiveSlot, m.Plan.TargetSlot)
	fmt.Printf("state machine transitions:\n")
	for _, s := range m.Steps {
		arrow := "->"
		if s.From == s.To {
			arrow = "  "
		}
		fmt.Printf("    %-22s --%-12s%s %-22s  %s\n", s.From, s.Event, arrow, s.To, s.Detail)
	}
	fmt.Printf("\nFINAL: %s%s\n", m.State, terminalNote(m.State))
	return rc
}

func terminalNote(s selfupdate.State) string {
	switch s {
	case selfupdate.StateCommitted:
		return "  (new slot committed; forward _migrate ran; snapshot retained)"
	case selfupdate.StateReverted:
		return "  (canary failed → AUTO-REVERTED to previous-good slot + restored DATA; box healthy)"
	case selfupdate.StateFailed:
		return "  (pre-disk failure; running slot untouched)"
	case selfupdate.StateQueued:
		return "  (awaiting human activation nonce — the brain cannot mint it)"
	}
	return ""
}

func cmdUninstall(args []string) int {
	fs := flag.NewFlagSet("uninstall", flag.ExitOnError)
	purge := fs.Bool("purge", false, "also delete the DATA disk (DESTRUCTIVE)")
	_ = fs.Parse(args)
	fmt.Println("== pn-factory uninstall (skeleton) ==")
	fmt.Println("Would replay the signed factory-state log in REVERSE:")
	fmt.Println("  - verify the hash-chain (refuse if tampered)")
	fmt.Println("  - undo each recorded artifact newest→oldest")
	fmt.Println("  - SKIP any artifact marked pre-existed (never touch a pre-existing bridge)")
	if *purge {
		fmt.Println("  - --purge: ALSO delete the DATA disk (otherwise DATA is kept)")
	} else {
		fmt.Println("  - DATA disk is KEPT (pass --purge to delete it)")
	}
	fmt.Println("\nNot wired to live teardown in this build (P7 dry-run guardrail).")
	return 0
}

func cmdRollback(args []string) int {
	fmt.Println("== pn-factory rollback (skeleton) ==")
	fmt.Println("Manual A/B revert (the same recovery the self-update canary triggers automatically):")
	fmt.Println("  - flip grubenv default back to the previous-good root slot (RevertGrub)")
	fmt.Println("  - restore the mandatory pre-update btrfs DATA snapshot (RestoreSnapshot)")
	fmt.Println("  - greenboot canary confirms health before keeping the flip")
	fmt.Println()
	fmt.Println("The auto-revert path is implemented + tested in internal/selfupdate (see")
	fmt.Println("`pn-factory update --fail-canary`). This `rollback` command is the manual entry")
	fmt.Println("point that invokes the same Driver.RevertGrub + RestoreSnapshot steps; the live")
	fmt.Println("grubenv/btrfs wiring of those Driver methods is the remaining TODO(wire).")
	return 0
}

func cmdConsole(args []string) int {
	fmt.Println("== pn-factory console (skeleton) ==")
	fmt.Println("Would attach to the appliance serial console (the escape hatch):")
	fmt.Println("  - libvirt:   virsh console brainarbeit")
	fmt.Println("  - raw-qemu:  connect to the supervised -serial pty")
	fmt.Println("\nNot wired in this build (no live VM exists).")
	return 0
}

func signStateHead(log *state.Log) string {
	if log.Head() == "" {
		return ""
	}
	var seed [32]byte
	copy(seed[:], "brainarbeit-factory-state-demoKEY")
	sk, err := sign.NewSecretKey([8]byte{0xBA, 0x1A, 0xBE, 0x17, 0xF5, 0x70, 0x00, 0x01}, seed[:])
	if err != nil {
		return ""
	}
	sig, err := log.SignHead(sk)
	if err != nil {
		return ""
	}
	return sig
}

func firstLine(s string) string {
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			return s[:i]
		}
	}
	return s
}

func sizingFrom(r detect.Report) sizing.Spec {
	return sizing.Spec{
		VRAMMiB:    r.Capacity.VRAMMiB,
		VCPU:       r.Capacity.VCPU,
		RootGiB:    r.Capacity.RootGiB,
		DataGiB:    r.Capacity.DataGiB,
		HostRAMMiB: r.Capacity.RAMMiB,
		HostCores:  r.Capacity.Cores,
	}
}

func exitForVerdict(v detect.Verdict) int {
	switch v {
	case detect.Green:
		return 0
	case detect.Amber:
		return 0
	default:
		return 3
	}
}

func verdictBanner(v detect.Verdict) string {
	switch v {
	case detect.Green:
		return "GREEN  (KVM + bridge → real VM with its own LAN IP)"
	case detect.Amber:
		return "AMBER  (KVM, no bridge → NAT+forward; discovery degraded)"
	default:
		return "RED    (no KVM → bare namespaced container; HW-reset/L0 lost)"
	}
}

func dash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func short(s string) string {
	if len(s) > 12 {
		return s[:12]
	}
	return s
}

func indent(s, pad string) string {
	out := ""
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out += pad + s[start:i+1]
			start = i + 1
		}
	}
	if start < len(s) {
		out += pad + s[start:]
	}
	return out
}
