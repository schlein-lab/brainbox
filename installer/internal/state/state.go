package state

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"
)

type Action string

const (
	ActCreateBridge Action = "create-bridge"
	ActCreateNATNet Action = "create-nat-network"
	ActFetchImage   Action = "fetch-image"
	ActCreateDisk   Action = "create-disk"
	ActDefineDomain Action = "define-domain"
	ActStartDomain  Action = "start-domain"
	ActPortForward  Action = "port-forward"
	ActDHCPReserve  Action = "dhcp-reservation"
)

type Record struct {
	Seq        int               `json:"seq"`
	Time       string            `json:"time"`
	Action     Action            `json:"action"`
	Target     string            `json:"target"`
	PreExisted bool              `json:"pre_existed"`
	Reverse    string            `json:"reverse"`
	Meta       map[string]string `json:"meta,omitempty"`
	PrevHash   string            `json:"prev_hash"`
	Hash       string            `json:"hash"`
}

type Log struct {
	records []Record
}

func New() *Log { return &Log{} }

var now = func() string { return time.Now().UTC().Format(time.RFC3339) }

func (l *Log) Append(a Action, target string, preExisted bool, reverse string, meta map[string]string) Record {
	prev := ""
	if n := len(l.records); n > 0 {
		prev = l.records[n-1].Hash
	}
	r := Record{
		Seq:        len(l.records),
		Time:       now(),
		Action:     a,
		Target:     target,
		PreExisted: preExisted,
		Reverse:    reverse,
		Meta:       meta,
		PrevHash:   prev,
	}
	r.Hash = hashRecord(prev, r)
	l.records = append(l.records, r)
	return r
}

func (l *Log) Records() []Record {
	out := make([]Record, len(l.records))
	copy(out, l.records)
	return out
}

func (l *Log) Head() string {
	if n := len(l.records); n > 0 {
		return l.records[n-1].Hash
	}
	return ""
}

func (l *Log) Verify() error {
	prev := ""
	for i, r := range l.records {
		if r.PrevHash != prev {
			return fmt.Errorf("record %d: prev_hash mismatch", i)
		}
		want := hashRecord(prev, r)
		if r.Hash != want {
			return fmt.Errorf("record %d: hash mismatch (tampered)", i)
		}
		prev = r.Hash
	}
	return nil
}

func (l *Log) ReversePlan() []string {
	var steps []string
	for i := len(l.records) - 1; i >= 0; i-- {
		r := l.records[i]
		if r.PreExisted {
			steps = append(steps, fmt.Sprintf("SKIP %s %q (pre-existed — never touched)", r.Action, r.Target))
			continue
		}
		rev := r.Reverse
		if rev == "" {
			rev = "(no inverse recorded)"
		}
		steps = append(steps, fmt.Sprintf("%s %q → %s", r.Action, r.Target, rev))
	}
	return steps
}

func (l *Log) Marshal(w io.Writer) error {
	for _, r := range l.records {
		b, err := json.Marshal(r)
		if err != nil {
			return err
		}
		if _, err := w.Write(append(b, '\n')); err != nil {
			return err
		}
	}
	return nil
}

func hashRecord(prev string, r Record) string {

	h := sha256.New()
	io.WriteString(h, prev)
	io.WriteString(h, "\x00")
	io.WriteString(h, fmt.Sprintf("%d\x00%s\x00%s\x00%s\x00%v\x00%s\x00%s",
		r.Seq, r.Time, r.Action, r.Target, r.PreExisted, r.Reverse, metaKey(r.Meta)))
	return hex.EncodeToString(h.Sum(nil))
}

func metaKey(m map[string]string) string {
	if len(m) == 0 {
		return ""
	}

	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	for i := 0; i < len(keys); i++ {
		for j := i + 1; j < len(keys); j++ {
			if keys[j] < keys[i] {
				keys[i], keys[j] = keys[j], keys[i]
			}
		}
	}
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k + "=" + m[k] + ";")
	}
	return b.String()
}
