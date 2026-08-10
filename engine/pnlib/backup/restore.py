
from __future__ import annotations

import hashlib
import io
import os
import tarfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pnlib import secrets as _secrets
from pnlib.backup.engine import Manifest, Target

@dataclass
class IntegrityReport:
    ok: bool
    content_id: str
    present: bool
    cipher_ok: bool
    detail: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "content_id": self.content_id, "present": self.present,
                "cipher_ok": self.cipher_ok, "detail": list(self.detail)}

def verify_integrity(manifest: Manifest, target: Target) -> IntegrityReport:

    detail: List[str] = []
    if not target.exists(manifest.content_id):
        detail.append(f"object {manifest.content_id[:12]} absent on target {target.id}")
        return IntegrityReport(ok=False, content_id=manifest.content_id, present=False,
                               cipher_ok=False, detail=detail)
    blob = target.get(manifest.content_id)
    got = hashlib.sha256(blob).hexdigest()
    cipher_ok = (got == manifest.cipher_digest)
    if not cipher_ok:
        detail.append(f"cipher digest mismatch on {target.id}: "
                      f"expected {manifest.cipher_digest[:12]} got {got[:12]} "
                      f"(corruption/tampering)")
    if len(blob) != manifest.cipher_size:
        detail.append(f"cipher size mismatch: expected {manifest.cipher_size} got {len(blob)}")
    return IntegrityReport(ok=cipher_ok and not detail, content_id=manifest.content_id,
                           present=True, cipher_ok=cipher_ok, detail=detail)

@dataclass
class RestoreReport:
    ok: bool
    dest: str
    files_restored: int
    integrity: dict
    plaintext_ok: bool
    file_mismatches: List[str] = field(default_factory=list)
    detail: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "dest": self.dest, "files_restored": self.files_restored,
                "integrity": self.integrity, "plaintext_ok": self.plaintext_ok,
                "file_mismatches": list(self.file_mismatches), "detail": list(self.detail)}

def _safe_extract_path(dest: str, name: str) -> str:

    dest_abs = os.path.abspath(dest)
    target = os.path.abspath(os.path.join(dest_abs, name))
    if target != dest_abs and not target.startswith(dest_abs + os.sep):
        raise ValueError(f"unsafe archive member path: {name!r}")
    return target

def restore(manifest: Manifest, target: Target, dest_dir: str) -> RestoreReport:

    detail: List[str] = []

    integ = verify_integrity(manifest, target)
    if not integ.ok:
        return RestoreReport(ok=False, dest=dest_dir, files_restored=0,
                             integrity=integ.to_dict(), plaintext_ok=False,
                             detail=["aborted: stored object failed integrity check"])

    blob = target.get(manifest.content_id)
    plaintext = _secrets.unseal(blob, manifest.seal_meta)

    plaintext_ok = (hashlib.sha256(plaintext).hexdigest() == manifest.content_id)
    if not plaintext_ok:
        detail.append("decrypted archive digest != manifest content_id (wrong key or corruption)")
        return RestoreReport(ok=False, dest=dest_dir, files_restored=0,
                             integrity=integ.to_dict(), plaintext_ok=False, detail=detail)

    os.makedirs(dest_dir, exist_ok=True)
    expected = {f.path: f for f in manifest.files}
    restored = 0
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r") as tar:
        for ti in tar.getmembers():
            if not ti.isfile():
                continue
            out_path = _safe_extract_path(dest_dir, ti.name)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            data = tar.extractfile(ti).read()
            with open(out_path, "wb") as f:
                f.write(data)
            os.chmod(out_path, ti.mode)
            restored += 1

    mismatches: List[str] = []
    for path, fe in expected.items():
        out_path = os.path.join(dest_dir, path)
        if not os.path.exists(out_path):
            mismatches.append(f"{path}: missing after restore")
            continue
        with open(out_path, "rb") as f:
            data = f.read()
        if len(data) != fe.size:
            mismatches.append(f"{path}: size {len(data)} != {fe.size}")
        elif hashlib.sha256(data).hexdigest() != fe.sha256:
            mismatches.append(f"{path}: sha256 mismatch")

    ok = plaintext_ok and not mismatches
    return RestoreReport(ok=ok, dest=dest_dir, files_restored=restored,
                         integrity=integ.to_dict(), plaintext_ok=plaintext_ok,
                         file_mismatches=mismatches, detail=detail)

@dataclass
class DiskLayout:

    device: str = "/dev/sda"
    table: str = "gpt"
    partitions: List[dict] = field(default_factory=lambda: [
        {"name": "esp", "size": "512MiB", "type": "efi", "fs": "vfat", "mount": "/boot/efi"},
        {"name": "boot", "size": "1GiB", "type": "linux", "fs": "ext4", "mount": "/boot"},
        {"name": "root", "size": "rest", "type": "linux", "fs": "ext4", "mount": "/"},
    ])

def bare_metal_descriptor(manifest: Manifest,
                          disk: Optional[DiskLayout] = None,
                          boot: str = "pn-init",
                          off_box_source: Optional[str] = None) -> dict:

    disk = disk or DiskLayout()
    return {
        "descriptor": "pn-bare-metal-restore",
        "version": 1,
        "manifest_id": manifest.manifest_id,
        "manifest_digest": manifest.digest(),
        "content_id": manifest.content_id,
        "cipher_digest": manifest.cipher_digest,
        "backend": manifest.backend,
        "off_box_source": off_box_source or "(mount an off-box copy holding the content object)",
        "disk": {"device": disk.device, "table": disk.table, "partitions": disk.partitions},
        "boot": boot,
        "steps": [
            "1. Boot the appliance rescue image on the blank hardware.",
            f"2. Partition {disk.device} as {disk.table}; create filesystems per 'disk.partitions'.",
            "3. Mount the off-box copy read-only; fetch the content object by content_id.",
            "4. verify_integrity() the object against this descriptor's cipher_digest BEFORE trusting it.",
            "5. Restore the archive to the mounted root via pnlib.backup.restore.restore().",
            f"6. Reinstall the bootloader for '{boot}' and regenerate machine identity.",
            "7. FIRST BOOT: re-seal secrets (pnlib.secrets) with the new host/TPM binding — the "
            "secrets/ dir was .nobackup-excluded and is NOT in this archive; re-enter the brain "
            "credential via the setcred socket verb.",
            "8. Re-register with the fleet / re-issue leases; run pnlib.backup.restore verification "
            "and pnlib.ledger self-check to confirm a sound restore.",
        ],
        "notes": [
            "The sealed secrets bundle is deliberately EXCLUDED from backups (.nobackup); bare-metal "
            "restore re-establishes credentials rather than restoring them.",
            "This descriptor never touches a real disk; it is data for a deliberate, audited apply.",
        ],
    }
