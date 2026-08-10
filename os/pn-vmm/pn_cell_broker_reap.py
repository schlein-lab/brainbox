#!/usr/bin/env python3

import os
import sys

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: pn_cell_broker_reap.py <run_dir>\n")
        return 2
    prefix = sys.argv[1].rstrip("/") + "/"
    if not prefix.startswith("/tmp/pn-cells/") or ".." in prefix:
        sys.stderr.write("verweigert: kein Zell-Laufverzeichnis\n")
        return 2
    me, mine, n = os.getpid(), os.getuid(), 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != mine:
                continue
            cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "--unix-mux" in cmd and prefix in cmd:
            try:
                os.kill(int(pid), 9)
                n += 1
            except OSError:
                pass
    print("eingesammelt: %d" % n)
    return 0

if __name__ == "__main__":
    sys.exit(main())
