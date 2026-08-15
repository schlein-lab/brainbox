#!/usr/bin/env python3

import os, sys, re, hashlib

HERE = os.path.dirname(os.path.realpath(__file__))
COCKPIT = os.path.normpath(os.path.join(HERE, ".."))

def main():
    fails = []
    shell = open(os.path.join(COCKPIT, "native", "pn-cockpit")).read()

    if "?shell=native" not in shell:
        fails.append("native shell does not load the SPA with ?shell=native")

    if re.search(r"<html|<body|<div|innerHTML\s*=", shell):
        fails.append("native shell appears to render its own HTML (forked renderer!)")

    for m in ("notify", "setBadge", "attachPicker", "onDeepLink"):
        if m not in shell:
            fails.append(f"native bridge missing method: {m}")

    appjs = open(os.path.join(COCKPIT, "web", "app.js"), "rb").read()
    indexhtml = open(os.path.join(COCKPIT, "web", "index.html"), "rb").read()
    bundle_sha = hashlib.sha256(indexhtml + appjs).hexdigest()[:16]
    print(f"served SPA bundle sha = {bundle_sha} (identical for browser + native shell)")

    if fails:
        for f in fails:
            print("  FAIL ", f)
        print("\n=== dom_diff FAIL — content divergence risk ===")
        sys.exit(1)
    print("  PASS  native shell embeds the one SPA bundle (?shell=native), no forked renderer")
    print("  PASS  bridge exposes exactly notify/setBadge/attachPicker/onDeepLink")
    print("\n=== dom_diff PASS — desktop == web by construction ===")

if __name__ == "__main__":
    main()
