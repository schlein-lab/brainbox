package selfupdate

import (
	"errors"
	"fmt"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

type Driver interface {

	Fetch(p Plan) (image []byte, minisig string, err error)

	Verify(p Plan, image []byte, minisig string) error

	SnapshotData(p Plan) (snapshotID string, err error)

	WriteInactiveSlot(p Plan, image []byte) error

	FlipGrubTrial(p Plan) error

	RunCanary(p Plan, attempt int) (ok bool, detail string)

	Commit(p Plan, snapshotID string) error

	RevertGrub(p Plan) error

	RestoreSnapshot(p Plan, snapshotID string) error
}

type ConsentVerifier interface {

	VerifyAndBurn(nonce, forVersion string) error
}

type EffectStubDriver interface {

	UnwiredEffects() string
}

var ErrNotVerified = errors.New("self-update: REFUSED — this driver cannot verify the image signature; an update that cannot be checked is not installed")

type DryRunDriver struct {

	Log []string

	CanaryHealthy bool

	FailCanaryUntil int
}

func NewDryRunDriver() *DryRunDriver { return &DryRunDriver{CanaryHealthy: true} }

func (d *DryRunDriver) log(f string, a ...any) { d.Log = append(d.Log, fmt.Sprintf(f, a...)) }

func (d *DryRunDriver) Fetch(p Plan) ([]byte, string, error) {
	url := p.Channel.ImageURL(p.ToVersion, p.Arch)
	d.log("TODO(wire): GET %s  + GET %s (resumable, content-length checked)", url, p.Channel.SigURL(p.ToVersion, p.Arch))

	return []byte("<DRY-RUN placeholder image bytes for " + p.ToVersion + ">"), "<DRY-RUN .minisig>", nil
}

func (d *DryRunDriver) Verify(p Plan, image []byte, minisig string) error {
	d.log("REFUSED: no minisign verification is wired in this driver — %d image bytes for channel %q are NOT trusted (pin a real key and use VerifyingDriver)", len(image), p.Channel.Name)
	return ErrNotVerified
}

func (d *DryRunDriver) UnwiredEffects() string {
	return "DryRunDriver: snapshot, inactive-slot write, grubenv flip, canary, commit and auto-revert are all TODO(wire) stubs — nothing is written and NOTHING CAN BE ROLLED BACK"
}

func (d *DryRunDriver) SnapshotData(p Plan) (string, error) {
	id := "data@pre-update-" + p.ToVersion
	d.log("TODO(wire): btrfs subvolume snapshot -r /data /data/.snapshots/%s   (MANDATORY rollback floor)", id)
	return id, nil
}

func (d *DryRunDriver) WriteInactiveSlot(p Plan, image []byte) error {
	d.log("TODO(wire): dd/convert verified image → INACTIVE root slot %s (active slot %s never touched)", p.TargetSlot, p.ActiveSlot)
	return nil
}

func (d *DryRunDriver) FlipGrubTrial(p Plan) error {
	d.log("TODO(wire): grub2-editenv grubenv set boot_once=true boot_target=%s boot_counter=%d", p.TargetSlot, p.MaxBootTries)
	return nil
}

func (d *DryRunDriver) RunCanary(p Plan, attempt int) (bool, string) {
	if attempt <= d.FailCanaryUntil {
		d.log("TODO(wire): greenboot canary on trial slot %s — attempt %d simulated UNHEALTHY", p.TargetSlot, attempt)
		return false, "simulated unhealthy (attempt within FailCanaryUntil)"
	}
	if !d.CanaryHealthy {
		d.log("TODO(wire): greenboot canary on trial slot %s — simulated UNHEALTHY", p.TargetSlot)
		return false, "simulated unhealthy (CanaryHealthy=false)"
	}
	d.log("TODO(wire): greenboot canary on trial slot %s — sacred sshd up → pnd ping=pong → canary job ok", p.TargetSlot)
	return true, "sshd+pnd+canary green"
}

func (d *DryRunDriver) Commit(p Plan, snapshotID string) error {
	d.log("TODO(wire): grub2-editenv grubenv set boot_target=%s boot_once=false boot_counter=0 (mark slot good)", p.TargetSlot)
	d.log("TODO(wire): run forward-only `pnd _migrate` ALTER in-guest; retain snapshot %s as the rollback floor", snapshotID)
	return nil
}

func (d *DryRunDriver) RevertGrub(p Plan) error {
	d.log("TODO(wire): grub2-editenv grubenv set boot_target=%s boot_once=false (AUTO-REVERT to previous-good)", p.ActiveSlot)
	return nil
}

func (d *DryRunDriver) RestoreSnapshot(p Plan, snapshotID string) error {
	d.log("TODO(wire): btrfs subvolume snapshot %s → /data (restore the pre-update DATA)", snapshotID)
	return nil
}

type VerifyingDriver struct {
	Driver
	Channel sign.Channel
}

func (v VerifyingDriver) Verify(p Plan, image []byte, minisig string) error {
	if v.Channel.Key().Key == nil {
		return fmt.Errorf("%w: channel %q has no usable pinned key (call Channel.Parse; a placeholder key never verifies)", ErrNotVerified, v.Channel.Name)
	}
	if len(image) == 0 {
		return fmt.Errorf("%w: refusing to verify zero image bytes on channel %q", ErrNotVerified, v.Channel.Name)
	}
	if minisig == "" {
		return fmt.Errorf("%w: no detached .minisig accompanied the image on channel %q — an UNSIGNED artifact is refused", ErrNotVerified, v.Channel.Name)
	}
	if err := v.Channel.VerifyImage(image, minisig); err != nil {
		return err
	}
	return nil
}

func (v VerifyingDriver) UnwiredEffects() string {
	if u, ok := v.Driver.(EffectStubDriver); ok {
		return u.UnwiredEffects()
	}
	return ""
}

type NonceConsent struct {

	outstanding map[string]string
}

func NewNonceConsent() *NonceConsent {
	return &NonceConsent{outstanding: map[string]string{}}
}

func (c *NonceConsent) Mint(nonce, forVersion string) {
	c.outstanding[nonce] = forVersion
}

func (c *NonceConsent) VerifyAndBurn(nonce, forVersion string) error {
	if nonce == "" {
		return errors.New("empty consent nonce")
	}
	want, ok := c.outstanding[nonce]
	if !ok {
		return errors.New("unknown or already-burned consent nonce (the brain cannot mint these)")
	}
	if want != forVersion {
		return fmt.Errorf("consent nonce bound to %q, not the target %q", want, forVersion)
	}
	delete(c.outstanding, nonce)
	return nil
}
