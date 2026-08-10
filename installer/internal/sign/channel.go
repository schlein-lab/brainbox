package sign

import (
	"errors"
	"fmt"
	"strings"
)

type Channel struct {
	Name      string
	BaseURL   string
	PubKey    string
	pubParsed PublicKey
}

const (
	ChannelStable = "stable"
	ChannelBeta   = "beta"
	ChannelEdge   = "edge"
)

func DefaultChannels() map[string]Channel {
	return map[string]Channel{
		ChannelStable: {Name: ChannelStable, BaseURL: "https://images.brainarbeit.dev/stable", PubKey: "RWQ<PINNED-STABLE-MINISIGN-PUBKEY>"},
		ChannelBeta:   {Name: ChannelBeta, BaseURL: "https://images.brainarbeit.dev/beta", PubKey: "RWQ<PINNED-BETA-MINISIGN-PUBKEY>"},
		ChannelEdge:   {Name: ChannelEdge, BaseURL: "https://images.brainarbeit.dev/edge", PubKey: "RWQ<PINNED-EDGE-MINISIGN-PUBKEY>"},
	}
}

func (c *Channel) Parse() error {
	pk, err := ParsePublicKey(c.PubKey)
	if err != nil {
		return fmt.Errorf("channel %q has an unusable pinned key: %w", c.Name, err)
	}
	c.pubParsed = pk
	return nil
}

func (c Channel) Key() PublicKey { return c.pubParsed }

func (c Channel) ImageURL(version, arch string) string {
	return strings.TrimRight(c.BaseURL, "/") + "/brainarbeit-" + version + "-" + arch + ".raw.zst"
}

func (c Channel) SigURL(version, arch string) string {
	return c.ImageURL(version, arch) + ".minisig"
}

func (c Channel) VerifyImage(imageBytes []byte, minisig string) error {
	if c.pubParsed.Key == nil {
		return errors.New("channel key not parsed (call Parse first)")
	}
	return Verify(c.pubParsed, imageBytes, minisig)
}

func (c Channel) OffersUpdate(running, candidate string) bool {
	return CompareVersions(candidate, running) > 0
}

func CompareVersions(a, b string) int {
	as, bs := splitVersion(a), splitVersion(b)
	n := len(as)
	if len(bs) > n {
		n = len(bs)
	}
	for i := 0; i < n; i++ {
		x := verComp{absent: true}
		y := verComp{absent: true}
		if i < len(as) {
			x = as[i]
		}
		if i < len(bs) {
			y = bs[i]
		}
		if c := x.cmp(y); c != 0 {
			return c
		}
	}
	return 0
}

type verComp struct {
	num    int
	isNum  bool
	suffix string
	absent bool
}

func (x verComp) cmp(y verComp) int {

	switch {
	case x.absent && y.absent:
		return 0
	case x.absent && y.isNum:

		return -signOf(y.num)
	case x.isNum && y.absent:
		return signOf(x.num)
	case x.absent && !y.isNum:
		return 1
	case !x.isNum && y.absent:
		return -1
	case x.isNum && y.isNum:
		switch {
		case x.num < y.num:
			return -1
		case x.num > y.num:
			return 1
		default:
			return 0
		}
	case x.isNum && !y.isNum:
		return 1
	case !x.isNum && y.isNum:
		return -1
	default:
		return strings.Compare(x.suffix, y.suffix)
	}
}

func signOf(n int) int {
	switch {
	case n < 0:
		return -1
	case n > 0:
		return 1
	default:
		return 0
	}
}

func splitVersion(v string) []verComp {
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	if v == "" {
		return nil
	}

	fields := strings.FieldsFunc(v, func(r rune) bool { return r == '.' || r == '-' })
	out := make([]verComp, 0, len(fields))
	for _, f := range fields {
		if n, ok := atoiStrict(f); ok {
			out = append(out, verComp{num: n, isNum: true})
		} else {
			out = append(out, verComp{suffix: f})
		}
	}
	return out
}

func atoiStrict(s string) (int, bool) {
	if s == "" {
		return 0, false
	}
	n := 0
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0, false
		}
		n = n*10 + int(r-'0')
	}
	return n, true
}
