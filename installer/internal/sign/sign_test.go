package sign

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"testing"
)

func mkKeypair(t *testing.T, keyID [8]byte) (SecretKey, string) {
	t.Helper()
	seed := make([]byte, ed25519.SeedSize)
	if _, err := rand.Read(seed); err != nil {
		t.Fatal(err)
	}
	sk, err := NewSecretKey(keyID, seed)
	if err != nil {
		t.Fatal(err)
	}
	pk := sk.PublicKey()
	body := append(append(append([]byte{}, algEd...), pk.KeyID[:]...), pk.Key...)
	pubStr := "untrusted comment: test key\n" + base64.StdEncoding.EncodeToString(body) + "\n"
	return sk, pubStr
}

func TestSignVerifyRoundTrip(t *testing.T) {
	id := [8]byte{1, 2, 3, 4, 5, 6, 7, 8}
	sk, pubStr := mkKeypair(t, id)
	pk, err := ParsePublicKey(pubStr)
	if err != nil {
		t.Fatalf("ParsePublicKey: %v", err)
	}
	msg := []byte("brainarbeit golden image v1.2.3 bytes")
	sig := sk.SignDetached(msg, "img")
	if err := Verify(pk, msg, sig); err != nil {
		t.Fatalf("valid signature must verify: %v", err)
	}
}

func TestVerifyRejectsTamperedMessage(t *testing.T) {
	id := [8]byte{9, 9, 9, 9, 9, 9, 9, 9}
	sk, pubStr := mkKeypair(t, id)
	pk, _ := ParsePublicKey(pubStr)
	sig := sk.SignDetached([]byte("original image"), "img")
	if err := Verify(pk, []byte("MALICIOUS image"), sig); err == nil {
		t.Fatal("tampered message must NOT verify")
	}
}

func TestVerifyRejectsWrongKey(t *testing.T) {

	skA, _ := mkKeypair(t, [8]byte{0xAA})
	_, pubStrB := mkKeypair(t, [8]byte{0xBB})
	pkB, _ := ParsePublicKey(pubStrB)
	sig := skA.SignDetached([]byte("img"), "img")
	if err := Verify(pkB, []byte("img"), sig); err == nil {
		t.Fatal("signature from a different key must NOT verify (key ID mismatch)")
	}
}

func TestVerifyRejectsKeyIDSpoofWithMaterialMismatch(t *testing.T) {

	id := [8]byte{0xCA, 0xFE}
	skA, _ := mkKeypair(t, id)
	_, pubStrB := mkKeypair(t, id)
	pkB, _ := ParsePublicKey(pubStrB)
	sig := skA.SignDetached([]byte("img"), "img")
	if err := Verify(pkB, []byte("img"), sig); err == nil {
		t.Fatal("matching key ID but wrong key material must NOT verify")
	}
}

func TestParsePublicKeyBadLength(t *testing.T) {
	if _, err := ParsePublicKey("dG9vc2hvcnQ="); err == nil {
		t.Fatal("short pubkey must error")
	}
	if _, err := ParsePublicKey(""); err == nil {
		t.Fatal("empty pubkey must error")
	}
}

func TestBlake2bKnownVectors(t *testing.T) {
	cases := map[string]string{

		"": "786a02f742015903c6c6fd852552d272912f4740e15847618a86e217f71f5419" +
			"d25e1031afee585313896444934eb04b903a685b1448b755d56f701afe9be2ce",

		"abc": "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1" +
			"7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923",
	}
	for in, wantHex := range cases {
		got := blake2bSum512([]byte(in))
		if toHex(got[:]) != wantHex {
			t.Errorf("blake2b(%q) = %s\n want %s", in, toHex(got[:]), wantHex)
		}
	}
}

func TestCompareVersions(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"1.0.0", "1.0.0", 0},
		{"1.0.1", "1.0.0", 1},
		{"1.0.0", "1.0.1", -1},
		{"1.10.0", "1.9.9", 1},
		{"2.0.0", "1.99.99", 1},
		{"v1.2.3", "1.2.3", 0},
		{"1.2.0", "1.2.0-rc1", 1},
		{"1.2.0-rc2", "1.2.0-rc1", 1},
		{"1.2", "1.2.0", 0},
		{"1.2.1", "1.2", 1},
		{"1.2.0-rc1", "1.2.0", -1},
	}
	for _, c := range cases {
		if got := CompareVersions(c.a, c.b); got != c.want {
			t.Errorf("CompareVersions(%q,%q)=%d want %d", c.a, c.b, got, c.want)
		}
	}
}

func TestChannelMonotonicNoDowngrade(t *testing.T) {
	ch := Channel{Name: "stable"}
	if !ch.OffersUpdate("1.0.0", "1.1.0") {
		t.Error("newer version must be offered")
	}
	if ch.OffersUpdate("1.1.0", "1.0.0") {
		t.Error("downgrade must NOT be offered over the wire")
	}
	if ch.OffersUpdate("1.0.0", "1.0.0") {
		t.Error("same version is not an update")
	}
}

func TestChannelVerifyImageEndToEnd(t *testing.T) {

	id := [8]byte{7, 7, 7, 7, 7, 7, 7, 7}
	sk, pubStr := mkKeypair(t, id)
	ch := Channel{Name: "test", BaseURL: "https://x/test", PubKey: pubStr}
	if err := ch.Parse(); err != nil {
		t.Fatalf("channel Parse: %v", err)
	}
	img := []byte("the golden image bytes")
	sig := sk.SignDetached(img, "img")
	if err := ch.VerifyImage(img, sig); err != nil {
		t.Fatalf("pinned-channel verify must pass: %v", err)
	}
	if err := ch.VerifyImage([]byte("evil"), sig); err == nil {
		t.Fatal("pinned-channel verify must reject a tampered image")
	}
}

func TestDefaultChannelsHavePlaceholderKeys(t *testing.T) {

	for name, ch := range DefaultChannels() {
		if err := (&ch).Parse(); err == nil {
			t.Errorf("channel %q placeholder key parsed — a real release must replace it, but it must not pass in this build", name)
		}
	}
}

func toHex(b []byte) string {
	const hexd = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, c := range b {
		out[i*2] = hexd[c>>4]
		out[i*2+1] = hexd[c&0xf]
	}
	return string(out)
}
