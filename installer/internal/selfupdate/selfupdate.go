package selfupdate

import (
	"errors"
	"fmt"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

type State string

const (

	StateIdle State = "idle"

	StateQueued State = "queued"

	StateFetching State = "fetching"

	StateVerifying State = "verifying"

	StateSnapshotting State = "snapshotting"

	StateWriting State = "writing-inactive-slot"

	StateFlipping State = "flipping-grubenv"

	StateCanary State = "canary"

	StateCommitted State = "committed"

	StateReverting State = "reverting"

	StateReverted State = "reverted"

	StateFailed State = "failed"
)

func (s State) Terminal() bool {
	switch s {
	case StateCommitted, StateReverted, StateFailed:
		return true
	}
	return false
}

type Event string

const (
	EvQueue       Event = "queue"
	EvActivate    Event = "activate"
	EvFetched     Event = "fetched"
	EvVerified    Event = "verified"
	EvSnapshotted Event = "snapshotted"
	EvWritten     Event = "written"
	EvFlipped     Event = "flipped"
	EvCanaryPass  Event = "canary-pass"
	EvCanaryFail  Event = "canary-fail"
	EvReverted    Event = "reverted"
	EvAbort       Event = "abort"
)

type Slot string

const (
	SlotA Slot = "a"
	SlotB Slot = "b"
)

func (s Slot) Other() Slot {
	if s == SlotA {
		return SlotB
	}
	return SlotA
}

type Plan struct {
	Channel      sign.Channel
	FromVersion  string
	ToVersion    string
	Arch         string
	ActiveSlot   Slot
	TargetSlot   Slot
	MaxBootTries int
}

type Machine struct {
	State State
	Plan  Plan

	AllowUnwiredEffects bool

	consumedNonce string
	bootTries     int

	Steps []Step
}

type Step struct {
	From   State
	Event  Event
	To     State
	Detail string
}

func New(p Plan) *Machine {
	if p.MaxBootTries <= 0 {
		p.MaxBootTries = 3
	}
	if p.TargetSlot == "" {
		p.TargetSlot = p.ActiveSlot.Other()
	}
	return &Machine{State: StateIdle, Plan: p}
}

func unwiredEffects(d Driver) string {
	if u, ok := d.(EffectStubDriver); ok {
		return u.UnwiredEffects()
	}
	return ""
}

func errInvalid(s State, e Event) error {
	return fmt.Errorf("self-update: event %q invalid in state %q", e, s)
}

func (m *Machine) record(e Event, to State, detail string) {
	m.Steps = append(m.Steps, Step{From: m.State, Event: e, To: to, Detail: detail})
	m.State = to
}

func (m *Machine) Queue() error {
	if m.State != StateIdle {
		return errInvalid(m.State, EvQueue)
	}
	if m.Plan.ToVersion == "" {
		return errors.New("self-update: queue requires a target version")
	}

	if !m.Plan.Channel.OffersUpdate(m.Plan.FromVersion, m.Plan.ToVersion) {
		m.record(EvAbort, StateFailed, fmt.Sprintf("refused: %s is not newer than running %s (no downgrades over the wire)", m.Plan.ToVersion, m.Plan.FromVersion))
		return fmt.Errorf("self-update: %s is not newer than %s", m.Plan.ToVersion, m.Plan.FromVersion)
	}
	m.record(EvQueue, StateQueued, fmt.Sprintf("queued %s→%s on channel %q (awaiting human activation nonce)", m.Plan.FromVersion, m.Plan.ToVersion, m.Plan.Channel.Name))
	return nil
}

func (m *Machine) Activate(nonce string, consent ConsentVerifier) error {
	if m.State != StateQueued {
		return errInvalid(m.State, EvActivate)
	}
	if consent == nil {
		return errors.New("self-update: no consent verifier — activation REFUSED")
	}
	if err := consent.VerifyAndBurn(nonce, m.Plan.ToVersion); err != nil {

		m.Steps = append(m.Steps, Step{From: m.State, Event: EvActivate, To: m.State, Detail: "activation REFUSED: " + err.Error()})
		return fmt.Errorf("self-update: activation refused: %w", err)
	}
	m.consumedNonce = nonce
	m.record(EvActivate, StateFetching, "human consent nonce verified + burned → fetching signed image")
	return nil
}

func (m *Machine) Run(d Driver) (State, error) {
	if m.State != StateFetching {
		return m.State, errInvalid(m.State, EvFetched)
	}

	img, sig, err := d.Fetch(m.Plan)
	if err != nil {
		m.record(EvAbort, StateFailed, "fetch failed: "+err.Error()+" (nothing written; running slot untouched)")
		return m.State, err
	}
	m.record(EvFetched, StateVerifying, fmt.Sprintf("fetched %d image bytes + detached .minisig", len(img)))

	if err := d.Verify(m.Plan, img, sig); err != nil {
		m.record(EvAbort, StateFailed, "minisign VERIFY FAILED: "+err.Error()+" (REFUSED — nothing written)")
		return m.State, err
	}
	m.record(EvVerified, StateSnapshotting, "minisign verified against the pinned channel key")

	if reason := unwiredEffects(d); reason != "" && !m.AllowUnwiredEffects {
		m.record(EvAbort, StateFailed, "REFUSED: driver side effects are not wired — "+reason+" (verification passed, but an update that cannot be written and cannot be reverted does not happen; set AllowUnwiredEffects only for a plan preview)")
		return m.State, fmt.Errorf("self-update: refusing to run an unwired driver: %s", reason)
	}

	snapID, err := d.SnapshotData(m.Plan)
	if err != nil {
		m.record(EvAbort, StateFailed, "DATA snapshot failed: "+err.Error()+" (REFUSE to proceed without a rollback floor)")
		return m.State, err
	}
	m.record(EvSnapshotted, StateWriting, "MANDATORY btrfs DATA snapshot taken: "+snapID)

	if err := d.WriteInactiveSlot(m.Plan, img); err != nil {

		m.record(EvAbort, StateFailed, fmt.Sprintf("inactive-slot %s write failed: %s (snapshot %s retained; active slot %s untouched)", m.Plan.TargetSlot, err.Error(), snapID, m.Plan.ActiveSlot))
		return m.State, err
	}
	m.record(EvWritten, StateFlipping, fmt.Sprintf("wrote verified image to INACTIVE slot %s (active %s untouched)", m.Plan.TargetSlot, m.Plan.ActiveSlot))

	if err := d.FlipGrubTrial(m.Plan); err != nil {
		m.record(EvAbort, StateFailed, "grubenv flip failed: "+err.Error()+" (still booting active slot "+string(m.Plan.ActiveSlot)+")")
		return m.State, err
	}
	m.record(EvFlipped, StateCanary, fmt.Sprintf("grubenv set to trial-boot slot %s once (boot-counter armed, max %d tries)", m.Plan.TargetSlot, m.Plan.MaxBootTries))

	return m.runCanary(d, snapID)
}

func (m *Machine) runCanary(d Driver, snapID string) (State, error) {
	for m.bootTries < m.Plan.MaxBootTries {
		m.bootTries++
		ok, detail := d.RunCanary(m.Plan, m.bootTries)
		if ok {
			m.record(EvCanaryPass, StateCommitted, fmt.Sprintf("greenboot canary PASSED on try %d/%d: %s", m.bootTries, m.Plan.MaxBootTries, detail))
			if err := d.Commit(m.Plan, snapID); err != nil {

				m.State = StateCanary
				return m.autoRevert(d, snapID, "commit failed after healthy canary: "+err.Error())
			}
			return m.State, nil
		}

		m.Steps = append(m.Steps, Step{From: StateCanary, Event: EvCanaryFail, To: StateCanary,
			Detail: fmt.Sprintf("canary try %d/%d FAILED: %s", m.bootTries, m.Plan.MaxBootTries, detail)})
	}
	return m.autoRevert(d, snapID, fmt.Sprintf("greenboot exhausted %d trial boots", m.Plan.MaxBootTries))
}

func (m *Machine) autoRevert(d Driver, snapID, why string) (State, error) {
	m.record(EvCanaryFail, StateReverting, "AUTO-REVERT: "+why)
	if err := d.RevertGrub(m.Plan); err != nil {

		m.record(EvAbort, StateFailed, "GRUB revert FAILED: "+err.Error()+" — crash-loop boot-counter must force the previous slot")
		return m.State, err
	}
	if err := d.RestoreSnapshot(m.Plan, snapID); err != nil {
		m.record(EvAbort, StateFailed, "snapshot restore FAILED: "+err.Error()+" (booted previous slot; DATA may be from the trial — investigate)")
		return m.State, err
	}
	m.record(EvReverted, StateReverted, fmt.Sprintf("reverted to previous-good slot %s + restored DATA snapshot %s (box healthy; update abandoned)", m.Plan.ActiveSlot, snapID))
	return m.State, nil
}
