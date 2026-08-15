
from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PN_SECRETS_ALLOW_INSECURE", "1")

from pnlib.backup import engine, restore, policy, health

def _mktree(root: str) -> None:
    os.makedirs(os.path.join(root, "sub", "deep"), exist_ok=True)
    with open(os.path.join(root, "a.txt"), "wb") as f:
        f.write(b"hello world\n" * 3)
    with open(os.path.join(root, "sub", "b.bin"), "wb") as f:
        f.write(bytes(range(256)) * 4)
    with open(os.path.join(root, "sub", "deep", "c.txt"), "w") as f:
        f.write("deep content\n")
    os.chmod(os.path.join(root, "a.txt"), 0o640)

def _dir_snapshot(root: str) -> dict:

    out = {}
    for dp, _dn, fns in os.walk(root):
        for n in fns:
            ab = os.path.join(dp, n)
            rel = os.path.relpath(ab, root)
            with open(ab, "rb") as f:
                out[rel.replace(os.sep, "/")] = (f.read(), oct(os.stat(ab).st_mode & 0o777))
    return out

def test_roundtrip_byte_identical():
    tmp = tempfile.mkdtemp(prefix="pnbk-rt-")
    try:
        src = os.path.join(tmp, "src")
        _mktree(src)
        original = _dir_snapshot(src)

        t_local = engine.MemoryTarget(id="local-ssd", media="ssd", offbox=False)
        t_off = engine.LocalDirTarget(os.path.join(tmp, "nas"), id="nas-b",
                                      media="nas", offbox=True)
        res = engine.backup([src], [t_local, t_off])
        assert res.targets_written == ["local-ssd", "nas-b"], res.targets_written
        assert res.manifest.content_id and res.manifest.cipher_digest

        dest = os.path.join(tmp, "restored")
        rep = restore.restore(res.manifest, t_off, dest)
        assert rep.ok, rep.to_dict()
        assert rep.files_restored == 3, rep.files_restored

        base = os.path.basename(src)
        restored = _dir_snapshot(os.path.join(dest, base))
        assert restored == original, (
            f"round-trip differs\n original={sorted(original)}\n restored={sorted(restored)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_integrity_detects_corruption():
    tmp = tempfile.mkdtemp(prefix="pnbk-int-")
    try:
        src = os.path.join(tmp, "src")
        _mktree(src)
        t = engine.MemoryTarget(id="mem", media="memory", offbox=True)
        res = engine.backup([src], [t])

        assert restore.verify_integrity(res.manifest, t).ok

        t.corrupt(res.manifest.content_id, b"\x00garbage-not-the-archive")
        ir = restore.verify_integrity(res.manifest, t)
        assert not ir.ok and not ir.cipher_ok, ir.to_dict()
        assert any("mismatch" in d for d in ir.detail), ir.detail

        dest = os.path.join(tmp, "out")
        rr = restore.restore(res.manifest, t, dest)
        assert not rr.ok, "restore should refuse a corrupted archive"
        assert not os.path.isdir(dest) or not os.listdir(dest), "no files should be written"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_missing_object_detected():
    tmp = tempfile.mkdtemp(prefix="pnbk-miss-")
    try:
        src = os.path.join(tmp, "src")
        _mktree(src)
        t = engine.MemoryTarget(id="mem", offbox=True)
        res = engine.backup([src], [t])
        t.delete(res.manifest.content_id)
        ir = restore.verify_integrity(res.manifest, t)
        assert not ir.ok and not ir.present, ir.to_dict()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_321_flags_no_offbox():

    copies = [
        policy.Copy("local-ssd", "ssd", offbox=False),
        policy.Copy("local-hdd", "hdd", offbox=False),
        policy.Copy("local-usb", "usb", offbox=False),
    ]
    rep = policy.check_3_2_1(copies)
    assert not rep.ok, rep.to_dict()
    assert rep.n_offbox == 0
    assert any("off-box" in v for v in rep.violations), rep.violations

    ok = policy.check_3_2_1([
        policy.Copy("local-ssd", "ssd", offbox=False),
        policy.Copy("nas", "nas", offbox=False),
        policy.Copy("cloud", "cloud", offbox=True),
    ])
    assert ok.ok, ok.to_dict()

def test_321_flags_too_few_copies_and_media():
    rep = policy.check_3_2_1([
        policy.Copy("a", "ssd", offbox=True),
        policy.Copy("b", "ssd", offbox=False),
    ])
    assert not rep.ok
    assert any("distinct copies" in v for v in rep.violations)
    assert any("distinct media" in v for v in rep.violations)

def test_dying_disk_smart_warning():
    reader = health.MockSmartReader({
        "/dev/mmcblk0": {"reallocated_sector_ct": 512, "current_pending_sector": 8},
        "/dev/sda": {"reallocated_sector_ct": 0},
    })
    dying = health.assess_disk_via(reader, "/dev/mmcblk0")
    assert not dying.healthy
    codes = {w.code for w in dying.warnings}
    assert "disk.reallocated.crit" in codes, codes
    assert any(w.severity == health.CRITICAL for w in dying.warnings)

    good = health.assess_disk_via(reader, "/dev/sda")
    assert good.healthy and not good.warnings

    flash = health.assess_disk("/dev/nvme0", {"nvme_percentage_used": 97, "nvme_available_spare": 8})
    assert not flash.healthy
    fc = {w.code for w in flash.warnings}
    assert "flash.endurance.crit" in fc and "flash.spare.crit" in fc, fc

def test_backup_freshness_and_collect():
    now = _dt.datetime(2026, 7, 5, 12, 0, tzinfo=_dt.timezone.utc)
    fresh = health.backup_freshness(now - _dt.timedelta(hours=2),
                                    _dt.timedelta(hours=24), now=now)
    assert fresh is None
    stale = health.backup_freshness(now - _dt.timedelta(hours=40),
                                    _dt.timedelta(hours=24), now=now)
    assert stale is not None and stale.code == "backup.stale"
    missing = health.backup_freshness(None, _dt.timedelta(hours=24), now=now)
    assert missing.code == "backup.missing" and missing.severity == health.CRITICAL

    rep = health.collect_warnings(
        disk_reports=[health.assess_disk("/dev/sda", {"reallocated_sector_ct": 3})],
        freshness=stale)
    assert not rep.ok
    assert rep.worst in (health.WARNING, health.CRITICAL)
    assert len(rep.warnings) == 2

def test_gfs_retention_prunes():

    base = _dt.datetime(2026, 6, 1, 3, 0, tzinfo=_dt.timezone.utc)
    snaps = [policy.Snapshot(id=f"s{i:03d}", when=base + _dt.timedelta(days=i)) for i in range(40)]
    cfg = policy.Retention(daily=7, weekly=4, monthly=2, yearly=1, min_keep=1)
    now = base + _dt.timedelta(days=40)
    plan = policy.gfs_plan(snaps, cfg, now=now)

    newest7 = {f"s{i:03d}" for i in range(33, 40)}
    assert newest7.issubset(set(plan.keep)), (newest7 - set(plan.keep))

    assert plan.prune, "expected old snapshots to be pruned"
    assert not (set(plan.keep) & set(plan.prune))
    assert set(plan.keep) | set(plan.prune) == {s.id for s in snaps}

    assert len(plan.keep) < len(snaps)

    actions = policy.rotate(snaps, cfg, now=now)
    kept = {a.snapshot_id for a in actions if a.op == "keep"}
    pruned = {a.snapshot_id for a in actions if a.op == "prune"}
    assert kept == set(plan.keep) and pruned == set(plan.prune)

def test_gfs_empty_and_min_keep():
    assert policy.gfs_plan([], policy.Retention()).keep == []
    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    snaps = [policy.Snapshot(f"x{i}", base + _dt.timedelta(days=i)) for i in range(3)]

    plan = policy.gfs_plan(snaps, policy.Retention(daily=0, weekly=0, monthly=0, yearly=0, min_keep=2))
    assert set(plan.keep) == {"x1", "x2"}, plan.keep

def test_nobackup_excluded():
    tmp = tempfile.mkdtemp(prefix="pnbk-nb-")
    try:
        src = os.path.join(tmp, "src")
        _mktree(src)
        secretdir = os.path.join(src, "secrets")
        os.makedirs(secretdir)
        with open(os.path.join(secretdir, ".nobackup"), "w") as f:
            f.write("exclude me\n")
        with open(os.path.join(secretdir, "brain.key"), "wb") as f:
            f.write(b"TOP-SECRET-SHOULD-NOT-BE-BACKED-UP")

        t = engine.MemoryTarget(id="mem", offbox=True)
        res = engine.backup([src], [t])
        paths = {fe.path for fe in res.manifest.files}
        assert not any("brain.key" in p for p in paths), paths
        assert not any("secrets/" in p for p in paths), paths
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_deterministic_content_id():

    tmp = tempfile.mkdtemp(prefix="pnbk-det-")
    try:
        import hashlib
        a = os.path.join(tmp, "p1", "same")
        b = os.path.join(tmp, "p2", "same")
        _mktree(a)
        _mktree(b)
        p1, _ = engine.build_archive([a])
        p2, _ = engine.build_archive([b])
        assert hashlib.sha256(p1).hexdigest() == hashlib.sha256(p2).hexdigest(), \
            "identical trees must yield identical content ids"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_bare_metal_descriptor():
    tmp = tempfile.mkdtemp(prefix="pnbk-bm-")
    try:
        src = os.path.join(tmp, "src"); _mktree(src)
        t = engine.MemoryTarget(id="mem", offbox=True)
        res = engine.backup([src], [t])
        d = restore.bare_metal_descriptor(res.manifest, off_box_source="usb:/mnt/rescue")
        assert d["content_id"] == res.manifest.content_id
        assert d["cipher_digest"] == res.manifest.cipher_digest
        assert d["manifest_digest"] == res.manifest.digest()
        assert d["disk"]["partitions"] and d["steps"] and d["boot"] == "pn-init"

        assert any("secrets" in n.lower() for n in d["notes"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def test_manifest_roundtrip_serialization():
    tmp = tempfile.mkdtemp(prefix="pnbk-mf-")
    try:
        src = os.path.join(tmp, "src"); _mktree(src)
        t = engine.MemoryTarget(id="mem", offbox=True)
        res = engine.backup([src], [t])
        js = res.manifest.to_json()
        m2 = engine.Manifest.from_json(js)
        assert m2.content_id == res.manifest.content_id
        assert m2.digest() == res.manifest.digest()
        assert len(m2.files) == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

_TESTS = [
    test_roundtrip_byte_identical,
    test_integrity_detects_corruption,
    test_missing_object_detected,
    test_321_flags_no_offbox,
    test_321_flags_too_few_copies_and_media,
    test_dying_disk_smart_warning,
    test_backup_freshness_and_collect,
    test_gfs_retention_prunes,
    test_gfs_empty_and_min_keep,
    test_nobackup_excluded,
    test_deterministic_content_id,
    test_bare_metal_descriptor,
    test_manifest_roundtrip_serialization,
]

def main() -> int:
    p = f = 0
    for t in _TESTS:
        try:
            t()
            p += 1
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            f += 1
            import traceback
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {p} passed, {f} failed ===")
    return 1 if f else 0

if __name__ == "__main__":
    sys.exit(main())
