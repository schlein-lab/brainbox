# pn-init + pndstub + memhog — statically linked, tiny, dependency-free.
#
#   make          glibc-static (host gcc)        — always works (NSS user= needs glibc shared libs at runtime)
#   make musl     musl-static (musl-gcc)         — tiny dependency-free PID1, ~80 KB (v2 full-system)
#
# musl-gcc ships its own libc headers but not the kernel headers; we add the kernel-header
# dirs with -idirafter so they only fill the linux/* + asm/* gaps and never shadow musl's libc.
CC      ?= gcc
CFLAGS  ?= -static -Os -Wall -Wextra -s

MULTIARCH   ?= $(shell gcc -dumpmachine 2>/dev/null)
MUSL_CC     ?= musl-gcc
MUSL_CFLAGS ?= -static -Os -Wall -Wextra -s -idirafter /usr/include -idirafter /usr/include/$(MULTIARCH)

all: pn-init pndstub memhog

pn-init: pn-init.c
	$(CC) $(CFLAGS) -o $@ $<

pndstub: pndstub.c
	$(CC) $(CFLAGS) -o $@ $<

# memhog: P2 tier-containment test hog (anonymous RSS grower). Tiny, static, libc-only.
memhog: memhog.c
	$(CC) $(CFLAGS) -o $@ $<

musl:
	$(MUSL_CC) $(MUSL_CFLAGS) -o pn-init  pn-init.c
	$(MUSL_CC) $(MUSL_CFLAGS) -o pndstub pndstub.c
	$(MUSL_CC) $(MUSL_CFLAGS) -o memhog  memhog.c

# Host unit test for the /etc/pn-init.conf parser (env= space preservation regression).
# Compiles pn-init.c with -DPN_INIT_TEST (swaps in a test main that drives load_conf()) and
# -DCONF_PATH pointing at a throwaway temp conf. Runs entirely on the build host — no VM/KVM.
TEST_CONF ?= /tmp/pn-init.test.conf
# Auch die zweite Schalterquelle (/etc/pn-init.cmdline) zeigt im Test auf eine Wegwerf-Datei:
# der Test SCHREIBT sie, und ein Testlauf darf weder root brauchen noch die echte Box anfassen.
TEST_CMDLINE ?= /tmp/pn-init.test.cmdline
# Und das Verzeichnis, in das PID 1 seine Lage veroeffentlicht: im Test eine Wegwerf-Kopie,
# damit ein Testlauf nie die Wahrheit einer laufenden Box ueberschreibt.
TEST_RUNDIR  ?= /tmp/pn-init.test.run
pn-init-test: pn-init.c
	$(CC) -Wall -Wextra -O0 -g -DPN_INIT_TEST -DCONF_PATH='"$(TEST_CONF)"' -DCMDLINE_EXTRA_PATH='"$(TEST_CMDLINE)"' -DPNRUN_DIR='"$(TEST_RUNDIR)"' -o $@ pn-init.c

test: pn-init-test
	./pn-init-test

clean:
	rm -f pn-init pndstub memhog pn-init-test

.PHONY: all musl test clean
