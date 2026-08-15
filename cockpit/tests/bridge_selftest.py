#!/usr/bin/env python3

import os, sys, subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
SHELL = os.path.normpath(os.path.join(HERE, "..", "native", "pn-cockpit"))

def main():
    r = subprocess.run([sys.executable, SHELL, "--selftest"], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    ok = r.returncode == 0 and "BRIDGE SELFTEST PASS" in r.stdout
    print(f"\n=== bridge selftest {'PASS' if ok else 'FAIL'} ===")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
