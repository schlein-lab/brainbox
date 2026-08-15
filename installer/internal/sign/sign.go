package sign

import (
	"bufio"
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
)

var (
	algEd       = []byte("Ed")
	algEdHashed = []byte("ED")
)

type PublicKey struct {
	KeyID [8]byte
	Key   ed25519.PublicKey
}

func ParsePublicKey(s string) (PublicKey, error) {
	var pk PublicKey
	line := lastNonCommentLine(s)
	if line == "" {
		return pk, errors.New("minisign: empty public key")
	}
	raw, err := base64.StdEncoding.DecodeString(line)
	if err != nil {
		return pk, fmt.Errorf("minisign: pubkey base64: %w", err)
	}
	if len(raw) != 2+8+32 {
		return pk, fmt.Errorf("minisign: pubkey wrong length %d (want 42)", len(raw))
	}
	if !bytes.Equal(raw[:2], algEd) {
		return pk, fmt.Errorf("minisign: unexpected pubkey algorithm %q", raw[:2])
	}
	copy(pk.KeyID[:], raw[2:10])
	pk.Key = ed25519.PublicKey(append([]byte(nil), raw[10:]...))
	return pk, nil
}

type signature struct {
	alg   []byte
	keyID [8]byte
	sig   []byte
}

func parseSignature(s string) (signature, error) {
	var sg signature
	line := firstNonCommentLine(s)
	if line == "" {
		return sg, errors.New("minisign: empty signature")
	}
	raw, err := base64.StdEncoding.DecodeString(line)
	if err != nil {
		return sg, fmt.Errorf("minisign: sig base64: %w", err)
	}
	if len(raw) != 2+8+64 {
		return sg, fmt.Errorf("minisign: sig wrong length %d (want 74)", len(raw))
	}
	sg.alg = append([]byte(nil), raw[:2]...)
	copy(sg.keyID[:], raw[2:10])
	sg.sig = append([]byte(nil), raw[10:]...)
	return sg, nil
}

func Verify(pk PublicKey, message []byte, minisig string) error {
	sg, err := parseSignature(minisig)
	if err != nil {
		return err
	}
	if sg.keyID != pk.KeyID {
		return fmt.Errorf("minisign: key ID mismatch (sig=%x pinned=%x) — NOT signed by the pinned key", sg.keyID, pk.KeyID)
	}
	var signed []byte
	switch {
	case bytes.Equal(sg.alg, algEdHashed):
		h := blake2bSum512(message)
		signed = h[:]
	case bytes.Equal(sg.alg, algEd):
		signed = message
	default:
		return fmt.Errorf("minisign: unsupported signature algorithm %q", sg.alg)
	}
	if !ed25519.Verify(pk.Key, signed, sg.sig) {
		return errors.New("minisign: SIGNATURE VERIFICATION FAILED — refuse to trust these bytes")
	}
	return nil
}

type SecretKey struct {
	KeyID [8]byte
	Key   ed25519.PrivateKey
}

func NewSecretKey(keyID [8]byte, seed []byte) (SecretKey, error) {
	if len(seed) != ed25519.SeedSize {
		return SecretKey{}, fmt.Errorf("minisign: seed must be %d bytes", ed25519.SeedSize)
	}
	return SecretKey{KeyID: keyID, Key: ed25519.NewKeyFromSeed(seed)}, nil
}

func (sk SecretKey) PublicKey() PublicKey {
	pub := sk.Key.Public().(ed25519.PublicKey)
	return PublicKey{KeyID: sk.KeyID, Key: append([]byte(nil), pub...)}
}

func (sk SecretKey) SignDetached(message []byte, comment string) string {
	h := blake2bSum512(message)
	sig := ed25519.Sign(sk.Key, h[:])
	body := make([]byte, 0, 2+8+64)
	body = append(body, algEdHashed...)
	body = append(body, sk.KeyID[:]...)
	body = append(body, sig...)
	if comment == "" {
		comment = "brainarbeit factory-state head"
	}
	var b strings.Builder
	b.WriteString("untrusted comment: " + comment + "\n")
	b.WriteString(base64.StdEncoding.EncodeToString(body))
	b.WriteString("\n")
	return b.String()
}

func firstNonCommentLine(s string) string {
	sc := bufio.NewScanner(strings.NewReader(s))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "untrusted comment:") || strings.HasPrefix(line, "trusted comment:") {
			continue
		}
		return line
	}
	return ""
}

func lastNonCommentLine(s string) string {
	var last string
	sc := bufio.NewScanner(strings.NewReader(s))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "untrusted comment:") || strings.HasPrefix(line, "trusted comment:") {
			continue
		}
		last = line
	}
	return last
}
