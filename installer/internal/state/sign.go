package state

import (
	"errors"

	"github.com/schlein-lab/brainarbeit/installer/internal/sign"
)

func (l *Log) SignHead(sk sign.SecretKey) (string, error) {
	head := l.Head()
	if head == "" {
		return "", errors.New("state: cannot sign an empty chain")
	}
	return sk.SignDetached([]byte(head), "brainarbeit factory-state head"), nil
}

func (l *Log) VerifyHead(pk sign.PublicKey, minisig string) error {
	if err := l.Verify(); err != nil {
		return err
	}
	head := l.Head()
	if head == "" {
		return errors.New("state: cannot verify an empty chain")
	}
	return sign.Verify(pk, []byte(head), minisig)
}
