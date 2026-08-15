#!/usr/bin/env python3

import sys, os, json, argparse
REPO = os.environ.get("BBX_REPO", os.path.expanduser("~/brainarbeit"))
sys.path.insert(0, REPO)
from relaylib import deaddrop
from relaylib.keys import ApplianceKeys

ap = argparse.ArgumentParser()
ap.add_argument("--cell-x-pub", required=True)
ap.add_argument("--cell-id-pub", required=True)
ap.add_argument("--device-x-pub", required=True)
ap.add_argument("--letterbox-url", default="https://drop.brainarbeit.com")
ap.add_argument("--out", required=True)
a = ap.parse_args()

k = ApplianceKeys()
dx_pub = bytes.fromhex(a.device_x_pub)

def info(name, default):
    v = getattr(deaddrop, name, None)
    return v.decode() if isinstance(v, (bytes, bytearray)) else (v or default)

inbox_topic = deaddrop.inbox_topic(k.sx_pub)
approvals_topic = deaddrop.approvals_topic(dx_pub)
grants_topic = deaddrop.grants_topic(k.sx_pub)

bundle = {
    "box_id_pub":      k.id_pub.hex(),
    "box_x_pub":       k.sx_pub.hex(),
    "box_topic":       inbox_topic,
    "inbox_topic":     inbox_topic,
    "approvals_topic": approvals_topic,
    "grants_topic":    grants_topic,
    "letterbox_url":   a.letterbox_url,
    "protocol":        "v2-cellsealed",
    "req_info":        info("REQ_INFO", "brainarbeit/deaddrop/req/v1"),
    "res_info":        info("RES_INFO", "brainarbeit/deaddrop/res/v1"),
    "approq_info":     info("APPROQ_INFO", "brainarbeit/deaddrop/approq/v1"),
    "apgrant_info":    info("APGRANT_INFO", "brainarbeit/deaddrop/apgrant/v1"),
    "cellreq_info":    info("CELLREQ_INFO", "brainarbeit/cellseal/req/v1"),
    "cellres_info":    info("CELLRES_INFO", "brainarbeit/cellseal/res/v1"),
    "cell_x_pub":      a.cell_x_pub,
    "cell_id_pub":     a.cell_id_pub,
}

EXPECT = {
    "box_id_pub": "fd4a012d6be6ff721d225a7d4c2d1decaf2e6b712020fd42a8c61fedbf4b65cc",
    "box_x_pub":  "a44fc373aad4a6fcf56406b438e90debd58ce6d12330032d40ae2c9ff5c8256a",
    "box_topic":  "f9a3b8b2a2c0cf5bf857798d38102d48",
    "approvals_topic": "60d1eec588c836675d74bce9bef72fc8",
    "grants_topic":    "593dc6909faebdc98fae2e71f0afcf21",
}
print("=== byte-verification vs published SERVER-CLIENT stable fields ===")
allok = True
for kf, exp in EXPECT.items():
    got = bundle.get(kf)
    ok = (got == exp)
    allok = allok and ok
    print("  [%s] %-16s got=%s%s" % ("OK" if ok else "MISMATCH", kf, got,
                                      "" if ok else " expected=%s" % exp))
if not allok:
    sys.exit("!! derived fields do not match the published bundle — refusing to write")

json.dump(bundle, open(a.out, "w"), indent=2)
print("\n=== FINAL pairing-bundle.json (all public, safe OOB) ===")
print(json.dumps(bundle, indent=2))
print("\nwrote:", a.out)
