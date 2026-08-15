#!/usr/bin/env python3

import os, sys, json, hashlib, hmac, time, subprocess, tempfile, shutil, re

STATE = os.environ.get("PN_FX_STATE", "/tmp/pn-effects")
OUTBOX = os.path.join(STATE, "pending"); CAS = os.path.join(STATE, "cas")
PROV = os.path.join(STATE, "provenance.log"); HOSTKEY = os.path.join(STATE, "host.key")
ALLOWED_KINDS = {"artifact"}
MAX_BODY = 8 * 1024 * 1024
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')

def _init():
    for d in (STATE, OUTBOX, CAS):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(HOSTKEY):
        open(HOSTKEY, "wb").write(os.urandom(32)); os.chmod(HOSTKEY, 0o600)

def _hostkey():
    return open(HOSTKEY, "rb").read()

def _mint_id(cell_id):
    return "fx_%s_%s" % (cell_id, os.urandom(6).hex())

def _valid_name(n):
    return bool(NAME_RE.match(n)) and n not in (".", "..")

def _sign(record):
    return hmac.new(_hostkey(), json.dumps(record, sort_keys=True).encode(), hashlib.sha256).hexdigest()

def _write_row(row):
    json.dump(row, open(os.path.join(OUTBOX, row["id"] + ".json"), "w"), indent=1)

def _rows():
    return [json.load(open(os.path.join(OUTBOX, f))) for f in sorted(os.listdir(OUTBOX)) if f.endswith(".json")]

def ingest(vol, cell_id, grant_out_dir):
    _init()
    tmp = tempfile.mkdtemp(prefix="pn-fx-harvest-")
    cp = vol + ".fxharvest"
    subprocess.run(["cp", "--reflink=auto", vol, cp], check=True)
    subprocess.run(["debugfs", "-R", "rdump /outbox %s" % tmp, cp], capture_output=True, text=True)
    os.remove(cp)
    ob = os.path.join(tmp, "outbox")
    n_pending, n_rejected = 0, 0
    if os.path.isdir(ob):
        for f in sorted(os.listdir(ob)):
            if not f.endswith(".effect.json"):
                continue
            try:
                man = json.load(open(os.path.join(ob, f)))
            except Exception:
                n_rejected += 1; continue
            kind = str(man.get("kind", "")); target = str(man.get("target_name", ""))
            summary = str(man.get("summary", ""))[:500]
            body_path = os.path.join(ob, os.path.basename(str(man.get("body", ""))))
            reason = None
            if kind not in ALLOWED_KINDS:
                reason = "kind-not-allowed:%s" % kind
            elif not _valid_name(target):
                reason = "unsafe-target-name"
            elif not os.path.isfile(body_path):
                reason = "body-missing"
            elif os.path.getsize(body_path) > MAX_BODY:
                reason = "body-too-large"
            if reason:
                _write_row({"id": _mint_id(cell_id), "cell_id": cell_id, "kind": kind, "target_name": target,
                            "summary": summary, "state": "REJECTED", "reason": reason, "ts": time.time()})
                n_rejected += 1; continue
            data = open(body_path, "rb").read()
            host_sha = hashlib.sha256(data).hexdigest()
            open(os.path.join(CAS, host_sha), "wb").write(data)
            _write_row({"id": _mint_id(cell_id), "cell_id": cell_id, "kind": kind, "target_name": target,
                        "summary": summary, "host_sha256": host_sha, "claimed_sha256": str(man.get("claimed_sha256", "")),
                        "size": len(data), "grant_out_dir": os.path.abspath(grant_out_dir),
                        "state": "PENDING", "ts": time.time()})
            n_pending += 1
    shutil.rmtree(tmp, ignore_errors=True)
    print("PN_FX_INGEST pending=%d rejected=%d" % (n_pending, n_rejected))

def list_(*_):
    _init()
    for r in _rows():
        print("%-30s %-8s kind=%s target=%s host_sha=%s claimed=%s%s" % (
            r["id"], r["state"], r.get("kind"), r.get("target_name"),
            r.get("host_sha256", "")[:12], (r.get("claimed_sha256", "")[:12] or "-"),
            (" reason=" + r["reason"] if r.get("reason") else "")))
        if r.get("summary"):
            print("    summary: %s" % r["summary"][:200])

def approve(fid, *_):
    _init()
    path = os.path.join(OUTBOX, fid + ".json")
    if not os.path.exists(path):
        print("PN_FX_NO_SUCH_ID"); return
    r = json.load(open(path))
    if r["state"] == "APPLIED":
        print("PN_FX_ALREADY_APPLIED %s (idempotent)" % fid); return
    if r["state"] != "PENDING":
        print("PN_FX_NOT_PENDING %s state=%s" % (fid, r["state"])); return

    token = _sign({"effect_id": fid, "host_sha256": r["host_sha256"], "expiry": time.time() + 300})
    grant = os.path.realpath(r["grant_out_dir"]); os.makedirs(grant, exist_ok=True)
    dest = os.path.join(grant, r["target_name"]); real = os.path.realpath(dest)
    if not (real == grant or real.startswith(grant + os.sep)):
        print("PN_FX_APPLY_REFUSED escapes-grant"); return
    data = open(os.path.join(CAS, r["host_sha256"]), "rb").read()
    if hashlib.sha256(data).hexdigest() != r["host_sha256"]:
        print("PN_FX_APPLY_REFUSED cas-hash-mismatch"); return
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    os.write(fd, data); os.close(fd)
    prov = {"effect_id": fid, "cell_id": r["cell_id"], "kind": r["kind"], "target_name": r["target_name"],
            "host_sha256": r["host_sha256"], "applied_path": dest, "approver": "human-cli",
            "token": token, "ts": time.time(), "digitalSourceType": "AI"}
    prov["host_sig"] = _sign(prov)
    open(PROV, "a").write(json.dumps(prov) + "\n")
    r["state"] = "APPLIED"; r["applied_path"] = dest; r["applied_ts"] = time.time()
    _write_row(r)
    print("PN_FX_APPLIED %s -> %s (provenance signed)" % (fid, dest))

def reject(fid, *_):
    _init()
    path = os.path.join(OUTBOX, fid + ".json")
    if not os.path.exists(path):
        print("PN_FX_NO_SUCH_ID"); return
    r = json.load(open(path)); r["state"] = "REJECTED"; r["reason"] = "human-reject"; _write_row(r)
    print("PN_FX_REJECTED %s" % fid)

if __name__ == "__main__":
    {"ingest": ingest, "list": list_, "approve": approve, "reject": reject}[sys.argv[1]](*sys.argv[2:])
