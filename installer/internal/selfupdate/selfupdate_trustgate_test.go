package selfupdate

import (
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

func runNoPreview(t *testing.T, m *Machine, drv Driver, nonce string) (State, error) {
	t.Helper()
	if err := m.Queue(); err != nil {
		t.Fatalf("Queue: %v", err)
	}
	if err := m.Activate(nonce, mintedConsent(nonce, m.Plan.ToVersion)); err != nil {
		t.Fatalf("Activate: %v", err)
	}
	return m.Run(drv)
}

func auditClaimsVerified(m *Machine) bool {
	for _, s := range m.Steps {
		if s.Event == EvVerified || s.To == StateSnapshotting {
			return true
		}
		if strings.Contains(strings.ToLower(s.Detail), "verified against the pinned") {
			return true
		}
	}
	return false
}

func assertRefusedBeforeDisk(t *testing.T, m *Machine, final State, err error) {
	t.Helper()
	if final != StateFailed {
		t.Fatalf("an unverifiable update must end in %s, got %s (steps=%+v)", StateFailed, final, m.Steps)
	}
	if err == nil {
		t.Fatal("a refused update must return a non-nil error")
	}
	if sawState(m, StateSnapshotting) || sawState(m, StateWriting) || sawState(m, StateFlipping) {
		t.Errorf("a refused update must abort BEFORE snapshot/slot-write/grub-flip; steps=%+v", m.Steps)
	}
	if sawState(m, StateCommitted) {
		t.Error("a refused update must never reach StateCommitted")
	}
	if auditClaimsVerified(m) {
		t.Errorf("a refused update must leave NO 'verified' claim in the audit trail; steps=%+v", m.Steps)
	}
}

func TestDryRunDriverRefusesInsteadOfSimulatingAPass(t *testing.T) {
	m := New(basePlan())
	drv := NewDryRunDriver()
	final, err := runNoPreview(t, m, drv, "n1")
	assertRefusedBeforeDisk(t, m, final, err)
	if !errors.Is(err, ErrNotVerified) {
		t.Errorf("refusal must be ErrNotVerified, got %v", err)
	}
	for _, l := range drv.Log {
		if strings.Contains(strings.ToUpper(l), "PASS") {
			t.Errorf("the driver log must not claim a PASS: %q", l)
		}
	}
}

func TestDryRunDriverRefusesEvenAGenuinelySignedImage(t *testing.T) {
	sk := testKey(t, 3)
	img := []byte("genuinely signed image")
	sig := sk.SignDetached(img, "img")

	p := basePlan()
	p.Channel = channelFor(t, sk)
	if err := (VerifyingDriver{Driver: NewDryRunDriver(), Channel: p.Channel}).Verify(p, img, sig); err != nil {
		t.Fatalf("precondition: the real verifier must accept these bytes: %v", err)
	}
	if err := NewDryRunDriver().Verify(p, img, sig); err == nil {
		t.Fatal("a driver that does not verify must REFUSE, even when the bytes happen to be valid")
	}
}

type fetchDriver struct {
	*DryRunDriver
	img []byte
	sig string
}

func (d fetchDriver) Fetch(p Plan) ([]byte, string, error) { return d.img, d.sig, nil }

func verifyingRig(t *testing.T, sk sign.SecretKey, img []byte, sig string) (Plan, Driver) {
	t.Helper()
	ch := channelFor(t, sk)
	p := basePlan()
	p.Channel = ch
	return p, VerifyingDriver{Driver: fetchDriver{DryRunDriver: NewDryRunDriver(), img: img, sig: sig}, Channel: ch}
}

func TestUnsignedArtifactIsRefused(t *testing.T) {
	sk := testKey(t, 2)
	p, drv := verifyingRig(t, sk, []byte("a perfectly good image, but nobody signed it"), "")
	m := New(p)
	final, err := runNoPreview(t, m, drv, "n1")
	assertRefusedBeforeDisk(t, m, final, err)
	if !errors.Is(err, ErrNotVerified) {
		t.Errorf("an unsigned artifact must be refused with ErrNotVerified, got %v", err)
	}
}

func TestGarbageSignatureIsRefused(t *testing.T) {
	sk := testKey(t, 2)
	p, drv := verifyingRig(t, sk, []byte("image"), "untrusted comment: x\nnot-a-signature\n")
	m := New(p)
	final, err := runNoPreview(t, m, drv, "n1")
	assertRefusedBeforeDisk(t, m, final, err)
}

func TestTamperedImageIsRefused(t *testing.T) {
	sk := testKey(t, 2)
	img := []byte("the image that was actually signed")
	sig := sk.SignDetached(img, "img")

	tampered := append([]byte(nil), img...)
	tampered[0] ^= 0x01

	p, drv := verifyingRig(t, sk, tampered, sig)
	m := New(p)
	final, err := runNoPreview(t, m, drv, "n1")
	assertRefusedBeforeDisk(t, m, final, err)
}

func TestSignatureFromAnotherKeyIsRefused(t *testing.T) {
	ours := testKey(t, 2)
	theirs := testKey(t, 9)
	img := []byte("attacker-built image")
	sig := theirs.SignDetached(img, "img")

	p, drv := verifyingRig(t, ours, img, sig)
	m := New(p)
	final, err := runNoPreview(t, m, drv, "n1")
	assertRefusedBeforeDisk(t, m, final, err)
	if !strings.Contains(err.Error(), "key ID mismatch") {
		t.Errorf("refusal should name the key-ID interlock, got %v", err)
	}
}

func TestShippedPlaceholderChannelsCannotVerify(t *testing.T) {
	sk := testKey(t, 2)
	img := []byte("image")
	sig := sk.SignDetached(img, "img")

	for name, ch := range sign.DefaultChannels() {
		if err := ch.Parse(); err == nil {
			t.Errorf("channel %q parses a real key in this build — update this test when release keys land", name)
			continue
		}
		p := basePlan()
		p.Channel = ch
		drv := VerifyingDriver{Driver: fetchDriver{DryRunDriver: NewDryRunDriver(), img: img, sig: sig}, Channel: ch}
		m := New(p)
		final, err := runNoPreview(t, m, drv, "n1")
		assertRefusedBeforeDisk(t, m, final, err)
		if !errors.Is(err, ErrNotVerified) {
			t.Errorf("channel %q: unpinned build must refuse with ErrNotVerified, got %v", name, err)
		}
	}
}

func TestStubEffectsCannotReachCommitted(t *testing.T) {
	p, _, drv := signedRig(t)
	m := New(p)

	if err := drv.Verify(p, []byte("<test image bytes for "+p.ToVersion+">"), signatureFor(t, p)); err != nil {
		t.Fatalf("precondition: signature must verify: %v", err)
	}

	final, err := runNoPreview(t, m, drv, "n1")
	if final != StateFailed {
		t.Fatalf("stub effects must not produce a committed update, got %s (steps=%+v)", final, m.Steps)
	}
	if err == nil {
		t.Fatal("refusing an unwired driver must return an error")
	}
	if sawState(m, StateWriting) || sawState(m, StateFlipping) || sawState(m, StateCommitted) {
		t.Errorf("no disk/bootloader step may be recorded on an unwired driver; steps=%+v", m.Steps)
	}

	if !sawTransition(m, StateVerifying, StateSnapshotting) {
		t.Error("verification should have passed (it is real); the effects gate is what must refuse")
	}
	last := m.Steps[len(m.Steps)-1]
	if !strings.Contains(last.Detail, "not wired") {
		t.Errorf("the refusal must say the effects are not wired, got %q", last.Detail)
	}
}

func signatureFor(t *testing.T, p Plan) string {
	t.Helper()
	sk := testKey(t, 1)
	return sk.SignDetached([]byte("<test image bytes for "+p.ToVersion+">"), "img")
}

func TestPlanPreviewMayRunStubEffects(t *testing.T) {
	p, _, drv := signedRig(t)
	m := New(p)
	m.AllowUnwiredEffects = true
	if err := m.Queue(); err != nil {
		t.Fatalf("Queue: %v", err)
	}
	if err := m.Activate("n1", mintedConsent("n1", p.ToVersion)); err != nil {
		t.Fatalf("Activate: %v", err)
	}
	final, err := m.Run(drv)
	if final != StateCommitted || err != nil {
		t.Fatalf("plan preview over a genuinely signed image should reach %s, got %s (%v)", StateCommitted, final, err)
	}
}

func TestNoDriverSimulatesVerification(t *testing.T) {
	banned := []string{
		"verification simulated",
		"simulated as PASS",
	}
	for _, f := range []string{"driver.go", "selfupdate.go"} {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("read %s: %v", f, err)
		}
		src := string(b)
		for _, bad := range banned {

			for _, line := range strings.Split(src, "\n") {
				trimmed := strings.TrimSpace(line)
				if strings.HasPrefix(trimmed, "//") {
					continue
				}
				if strings.Contains(line, bad) {
					t.Errorf("%s contains %q outside a comment — no driver may simulate a verification verdict:\n    %s", f, bad, trimmed)
				}
			}
		}
	}
}
