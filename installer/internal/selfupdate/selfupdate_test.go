package selfupdate

import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

func basePlan() Plan {
	return Plan{
		Channel:     sign.Channel{Name: "stable"},
		FromVersion: "1.0.0",
		ToVersion:   "1.1.0",
		Arch:        "amd64",
		ActiveSlot:  SlotA,
	}
}

func mintedConsent(nonce, version string) *NonceConsent {
	c := NewNonceConsent()
	c.Mint(nonce, version)
	return c
}

func drive(t *testing.T, m *Machine, drv Driver, nonce string) State {
	t.Helper()
	m.AllowUnwiredEffects = true
	if err := m.Queue(); err != nil {
		t.Fatalf("Queue: %v", err)
	}
	if err := m.Activate(nonce, mintedConsent(nonce, m.Plan.ToVersion)); err != nil {
		t.Fatalf("Activate: %v", err)
	}
	final, _ := m.Run(drv)
	return final
}

func testKey(t *testing.T, n byte) sign.SecretKey {
	t.Helper()
	id := [8]byte{n, n, n, n, n, n, n, n}
	seed := make([]byte, ed25519.SeedSize)
	for i := range seed {
		seed[i] = byte(i+1) * n
	}
	sk, err := sign.NewSecretKey(id, seed)
	if err != nil {
		t.Fatalf("NewSecretKey: %v", err)
	}
	return sk
}

func channelFor(t *testing.T, sk sign.SecretKey) sign.Channel {
	t.Helper()
	ch := sign.Channel{Name: "test", BaseURL: "https://x", PubKey: encodePubKey(sk.PublicKey())}
	if err := ch.Parse(); err != nil {
		t.Fatalf("channel parse: %v", err)
	}
	return ch
}

func signedRig(t *testing.T) (Plan, *DryRunDriver, Driver) {
	t.Helper()
	sk := testKey(t, 1)
	ch := channelFor(t, sk)
	p := basePlan()
	p.Channel = ch
	inner := NewDryRunDriver()
	img := []byte("<test image bytes for " + p.ToVersion + ">")
	fetch := signingFetchDriver{DryRunDriver: inner, img: img, sig: sk.SignDetached(img, "img")}
	return p, inner, VerifyingDriver{Driver: fetch, Channel: ch}
}

func TestHappyPathCommits(t *testing.T) {
	p, _, drv := signedRig(t)
	m := New(p)
	final := drive(t, m, drv, "n1")
	if final != StateCommitted {
		t.Fatalf("happy path should commit, got %s", final)
	}
	if m.Plan.TargetSlot != SlotB {
		t.Errorf("target slot should be B (other of A), got %s", m.Plan.TargetSlot)
	}

	wantOrder := []State{
		StateQueued, StateFetching, StateVerifying, StateSnapshotting,
		StateWriting, StateFlipping, StateCanary, StateCommitted,
	}
	gotTo := make([]State, 0, len(m.Steps))
	for _, s := range m.Steps {
		if s.From != s.To {
			gotTo = append(gotTo, s.To)
		}
	}
	if len(gotTo) != len(wantOrder) {
		t.Fatalf("transition count %d want %d: %v", len(gotTo), len(wantOrder), gotTo)
	}
	for i := range wantOrder {
		if gotTo[i] != wantOrder[i] {
			t.Errorf("transition %d = %s want %s", i, gotTo[i], wantOrder[i])
		}
	}

	snapIdx, writeIdx := stepIndex(m, StateSnapshotting), stepIndex(m, StateWriting)
	if snapIdx < 0 || writeIdx < 0 || snapIdx > writeIdx {
		t.Errorf("MANDATORY snapshot must precede slot write (snap@%d write@%d)", snapIdx, writeIdx)
	}
}

func TestCanaryFailAutoReverts(t *testing.T) {
	p, inner, drv := signedRig(t)
	m := New(p)
	inner.CanaryHealthy = false
	final := drive(t, m, drv, "n1")
	if final != StateReverted {
		t.Fatalf("canary fail must AUTO-REVERT to reverted, got %s", final)
	}

	failCount := 0
	for _, s := range m.Steps {
		if s.Event == EvCanaryFail && s.From == StateCanary && s.To == StateCanary {
			failCount++
		}
	}
	if failCount != m.Plan.MaxBootTries {
		t.Errorf("expected %d failed trial boots, got %d", m.Plan.MaxBootTries, failCount)
	}
	if !sawTransition(m, StateReverting, StateReverted) {
		t.Error("must transition Reverting→Reverted (GRUB revert + snapshot restore)")
	}

	if m.State != StateReverted {
		t.Error("final state must be Reverted")
	}
	if !logHas(inner, "AUTO-REVERT") {
		t.Error("driver must record the GRUB auto-revert")
	}
	if !logHas(inner, "restore the pre-update DATA") {
		t.Error("driver must restore the DATA snapshot on revert")
	}
}

func TestFlakyCanaryRecoversWithinTries(t *testing.T) {
	p, inner, drv := signedRig(t)
	m := New(p)
	inner.FailCanaryUntil = 2
	final := drive(t, m, drv, "n1")
	if final != StateCommitted {
		t.Fatalf("a boot that recovers within MaxBootTries must COMMIT, got %s", final)
	}
}

type failVerifyDriver struct{ *DryRunDriver }

func (d failVerifyDriver) Verify(p Plan, image []byte, minisig string) error {
	return errors.New("minisign: bad signature")
}

func TestVerifyFailureAbortsBeforeDisk(t *testing.T) {
	m := New(basePlan())
	drv := failVerifyDriver{NewDryRunDriver()}
	final := drive(t, m, drv, "n1")
	if final != StateFailed {
		t.Fatalf("verify failure must end in Failed, got %s", final)
	}

	if sawState(m, StateSnapshotting) || sawState(m, StateWriting) {
		t.Error("verify failure must abort BEFORE snapshot/write")
	}
	if !sawTransition(m, StateVerifying, StateFailed) {
		t.Error("must transition Verifying→Failed")
	}
}

type failSnapshotDriver struct{ Driver }

func (d failSnapshotDriver) SnapshotData(p Plan) (string, error) {
	return "", errors.New("btrfs: no space for snapshot")
}

func TestSnapshotFailureRefusesWrite(t *testing.T) {
	p, _, base := signedRig(t)
	m := New(p)
	drv := failSnapshotDriver{base}
	final := drive(t, m, drv, "n1")
	if final != StateFailed {
		t.Fatalf("snapshot failure must end in Failed, got %s", final)
	}
	if sawState(m, StateWriting) {
		t.Error("must NOT write a slot without the mandatory DATA snapshot (rollback floor)")
	}
}

func TestBrainCannotMintConsent(t *testing.T) {
	m := New(basePlan())
	if err := m.Queue(); err != nil {
		t.Fatalf("Queue: %v", err)
	}

	brain := NewNonceConsent()
	if err := m.Activate("brain-guessed-nonce", brain); err == nil {
		t.Fatal("activation with a nonce the brain minted (i.e. none) MUST be refused")
	}
	if m.State != StateQueued {
		t.Errorf("a refused activation must stay Queued, got %s", m.State)
	}
}

func TestConsentNonceIsSingleUse(t *testing.T) {
	c := mintedConsent("once", "1.1.0")
	if err := c.VerifyAndBurn("once", "1.1.0"); err != nil {
		t.Fatalf("first use must succeed: %v", err)
	}
	if err := c.VerifyAndBurn("once", "1.1.0"); err == nil {
		t.Fatal("a burned nonce must NOT verify again (single-use)")
	}
}

func TestConsentNonceVersionBound(t *testing.T) {
	c := mintedConsent("n", "1.1.0")
	if err := c.VerifyAndBurn("n", "9.9.9"); err == nil {
		t.Fatal("a nonce bound to 1.1.0 must not authorize 9.9.9 (replay defence)")
	}
}

func TestActivateRequiresConsentVerifier(t *testing.T) {
	m := New(basePlan())
	_ = m.Queue()
	if err := m.Activate("x", nil); err == nil {
		t.Fatal("activation with no consent verifier must be refused")
	}
}

func TestQueueRefusesDowngrade(t *testing.T) {
	p := basePlan()
	p.FromVersion, p.ToVersion = "2.0.0", "1.9.9"
	m := New(p)
	if err := m.Queue(); err == nil {
		t.Fatal("queuing a downgrade must be refused")
	}
	if m.State != StateFailed {
		t.Errorf("a refused downgrade ends Failed, got %s", m.State)
	}
}

func TestRunBeforeActivateIsInvalid(t *testing.T) {
	m := New(basePlan())
	if _, err := m.Run(NewDryRunDriver()); err == nil {
		t.Fatal("Run before Activate must be invalid")
	}
}

func TestActivateBeforeQueueIsInvalid(t *testing.T) {
	m := New(basePlan())
	if err := m.Activate("n", mintedConsent("n", "1.1.0")); err == nil {
		t.Fatal("Activate before Queue must be invalid")
	}
}

func TestRealMinisignVerificationInMachine(t *testing.T) {

	id := [8]byte{1, 1, 1, 1, 1, 1, 1, 1}
	seed := make([]byte, ed25519.SeedSize)
	for i := range seed {
		seed[i] = byte(i + 1)
	}
	sk, _ := sign.NewSecretKey(id, seed)
	pk := sk.PublicKey()

	pubWire := encodePubKey(pk)
	ch := sign.Channel{Name: "test", BaseURL: "https://x", PubKey: pubWire}
	if err := ch.Parse(); err != nil {
		t.Fatalf("channel parse: %v", err)
	}

	p := basePlan()
	p.Channel = ch
	m := New(p)

	img := []byte("<DRY-RUN placeholder image bytes for 1.1.0>")
	good := signingFetchDriver{DryRunDriver: NewDryRunDriver(), img: img, sig: sk.SignDetached(img, "img")}
	vd := VerifyingDriver{Driver: good, Channel: ch}

	final := drive(t, m, vd, "n1")
	if final != StateCommitted {
		t.Fatalf("a genuinely-signed image must commit, got %s; steps=%v", final, m.Steps)
	}

	m2 := New(p)
	bad := signingFetchDriver{DryRunDriver: NewDryRunDriver(), img: img, sig: sk.SignDetached([]byte("DIFFERENT"), "img")}
	vd2 := VerifyingDriver{Driver: bad, Channel: ch}
	final2 := drive(t, m2, vd2, "n2")
	if final2 != StateFailed {
		t.Fatalf("a mis-signed image must FAIL verify, got %s", final2)
	}
	if sawState(m2, StateWriting) {
		t.Fatal("a verify failure must not reach the slot write")
	}
}

type signingFetchDriver struct {
	*DryRunDriver
	img []byte
	sig string
}

func (d signingFetchDriver) Fetch(p Plan) ([]byte, string, error) {
	return d.img, d.sig, nil
}

func encodePubKey(pk sign.PublicKey) string {

	body := make([]byte, 0, 42)
	body = append(body, 'E', 'd')
	body = append(body, pk.KeyID[:]...)
	body = append(body, pk.Key...)
	return "untrusted comment: test\n" + base64.StdEncoding.EncodeToString(body) + "\n"
}

func sawState(m *Machine, s State) bool {
	for _, st := range m.Steps {
		if st.To == s || st.From == s {
			return true
		}
	}
	return false
}

func sawTransition(m *Machine, from, to State) bool {
	for _, st := range m.Steps {
		if st.From == from && st.To == to {
			return true
		}
	}
	return false
}

func stepIndex(m *Machine, to State) int {
	for i, st := range m.Steps {
		if st.To == to && st.From != st.To {
			return i
		}
	}
	return -1
}

func logHas(d *DryRunDriver, sub string) bool {
	for _, l := range d.Log {
		if strings.Contains(l, sub) {
			return true
		}
	}
	return false
}
