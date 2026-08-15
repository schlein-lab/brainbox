#!/usr/bin/env python3

import sys, time, mmap

mb = int(sys.argv[1]) if len(sys.argv) > 1 else 100
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
n = mb * 1024 * 1024
buf = mmap.mmap(-1, n)
for i in range(0, n, 4096):
    buf[i] = 1
print(f"hog {mb}MiB resident for {secs}s", flush=True)
time.sleep(secs)
print("hog done", flush=True)
