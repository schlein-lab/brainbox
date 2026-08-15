# Makefile for the pn-vmm cell guest kernel.
# Referenced by os/image/build-appliance-disk.sh ("make -C kernel").
# Real work is in build-guest-kernel.sh; see BUILD.md for the recipe and rationale.
#
#   make            # build for this host's arch (x86_64 on the dev box)
#   make x86_64     # -> vmlinux-rng.bin (uncompressed ELF)
#   make aarch64    # -> vmlinux.bin + vmlinux-rng.bin (flat arm64 Image; cross-builds on x86)
#   make all        # both arches
#   make verify     # print the version string(s) of the current artifacts
#   make clean      # remove the scratch build tree ($(BROOT))

SHELL   := /bin/bash
HOSTARCH := $(shell uname -m)
BROOT   ?= $(HOME)/kbuild
BUILDER := ./build-guest-kernel.sh

.PHONY: default all x86_64 aarch64 arm64 verify clean

default: $(HOSTARCH)

x86_64:
	BROOT=$(BROOT) $(BUILDER) x86_64

aarch64 arm64:
	BROOT=$(BROOT) $(BUILDER) aarch64

all: x86_64 aarch64

verify:
	@for f in vmlinux-rng.bin vmlinux.bin; do \
	  if [ -f $$f ]; then printf '%s: ' $$f; \
	    (strings -a $$f | grep -m1 'Linux version' || file $$f); \
	  fi; done

clean:
	rm -rf $(BROOT)
