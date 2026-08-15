package state

import (
	"crypto/ed25519"
	"testing"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

func mkKey(t *testing.T) sign.SecretKey {
	t.Helper()
	seed := make([]byte, ed25519.SeedSize)
	for i := range seed {
		seed[i] = byte(i * 7)
	}
	sk, err := sign.NewSecretKey([8]byte{0xBA, 0x1A, 0xBE, 0x17, 1, 2, 3, 4}, seed)
	if err != nil {
		t.Fatal(err)
	}
	return sk
}

func TestSignAndVerifyHead(t *testing.T) {
	sk := mkKey(t)
	pk := sk.PublicKey()

	l := New()
	l.Append(ActCreateBridge, "br-brainarbeit", false, "ip link delete br-brainarbeit", nil)
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)

	sig, err := l.SignHead(sk)
	if err != nil {
		t.Fatalf("SignHead: %v", err)
	}
	if err := l.VerifyHead(pk, sig); err != nil {
		t.Fatalf("a freshly-signed head must verify: %v", err)
	}
}

func TestVerifyHeadRejectsTamperedChain(t *testing.T) {
	sk := mkKey(t)
	pk := sk.PublicKey()

	l := New()
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)
	l.Append(ActStartDomain, "brainarbeit", false, "virsh destroy brainarbeit", nil)
	sig, _ := l.SignHead(sk)

	l.records[0].Target = "evil"
	if err := l.VerifyHead(pk, sig); err == nil {
		t.Fatal("a tampered chain must fail VerifyHead (fail-closed before the sig check)")
	}
}

func TestVerifyHeadRejectsWrongSignature(t *testing.T) {
	sk := mkKey(t)
	pk := sk.PublicKey()

	l := New()
	l.Append(ActDefineDomain, "brainarbeit", false, "virsh undefine brainarbeit", nil)

	other := New()
	other.Append(ActStartDomain, "other", false, "virsh destroy other", nil)
	otherSig, _ := other.SignHead(sk)

	if err := l.VerifyHead(pk, otherSig); err == nil {
		t.Fatal("a signature over a different head must NOT verify")
	}
}

func TestSignEmptyChainErrors(t *testing.T) {
	sk := mkKey(t)
	if _, err := New().SignHead(sk); err == nil {
		t.Fatal("signing an empty chain must error")
	}
}
