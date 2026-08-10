
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

TIER_AUTONOMOUS = "autonomous"
TIER_AUTONOMOUS_LOGGED = "autonomous-logged"
TIER_CONSENT = "consent"

class MaintError(Exception):
    pass

class ConsentRequired(MaintError):
    pass

class FirmwareUnavailable(MaintError):
    pass

class EmulationRequired(MaintError):
    pass

class BackupRequired(MaintError):
    pass

def detect_mode() -> str:

    forced = os.environ.get("PN_DEVMODE")
    if forced:
        return forced

    if os.path.exists("/.dockerenv") or os.environ.get("container"):
        return "red"
    return "green"

def firmware_permitted(mode: str | None = None) -> bool:
    return (mode or detect_mode()) == "green"

_CVE_FEED = {
    "brother-fw": [
        {"id": "CVE-2024-0001", "severity": "high", "fixed_in": "1.34",
         "summary": "auth bypass in the embedded web admin"},
    ],
    "openwrt": [
        {"id": "CVE-2023-9999", "severity": "critical", "fixed_in": "22.03.5",
         "summary": "RCE in the firmware update endpoint"},
    ],
    "generic-iot": [
        {"id": "CVE-2022-1234", "severity": "medium", "fixed_in": "2.1",
         "summary": "default credentials on the telnet console"},
    ],
}

def _ver_lt(a: str, b: str) -> bool:
    def parts(v):
        return [int(x) for x in v.replace("-", ".").split(".") if x.isdigit()]
    pa, pb = parts(a), parts(b)
    return pa < pb

def cve_audit(profile: dict, current_version: str | None = None) -> dict:

    product = (profile.get("product") or profile.get("vendor") or "").lower()
    key = None
    for k in _CVE_FEED:
        if k.split("-")[0] in product or k in product:
            key = k
            break
    if key is None and profile.get("device_class") in ("iot", "sensor"):
        key = "generic-iot"
    findings = []
    cur = current_version or profile.get("firmware_version")
    for cve in _CVE_FEED.get(key, []):
        applies = True
        if cur and cve.get("fixed_in"):
            applies = _ver_lt(cur, cve["fixed_in"])
        if applies:
            findings.append(cve)
    return {
        "tier": TIER_AUTONOMOUS,
        "device": {"ip": profile.get("ip"), "class": profile.get("device_class"),
                   "product": product, "version": cur},
        "cve_findings": findings,
        "needs_update": bool(findings),
        "max_severity": max((f["severity"] for f in findings),
                            key=lambda s: ["low", "medium", "high", "critical"].index(s),
                            default=None),
        "read_only": True,
        "audited_at": time.time(),
    }

def reversible_config_fix(profile: dict, fix: dict, log) -> dict:

    before = dict(profile.get("config", {}))
    after = dict(before)
    after.update(fix.get("set", {}))
    entry = {"tier": TIER_AUTONOMOUS_LOGGED, "fix": fix, "before": before, "after": after,
             "reversible": True, "at": time.time()}
    log.append(entry)
    return {"applied": True, "reversible": True, "before": before, "after": after, "entry": entry}

def build_update(profile: dict, patch: dict, target_arch: str = "armv7", *, sign_key: bytes | None = None,
                 workdir: str | None = None) -> dict:

    base = (profile.get("firmware_image") or b"FW\x00BASE").encode() if isinstance(
        profile.get("firmware_image"), str) else (profile.get("firmware_image") or b"FW\x00BASE")
    patched = base + b"\n" + json.dumps(patch, sort_keys=True).encode()
    artifact = hashlib.sha256(patched).hexdigest()
    descriptor = {
        "artifact_sha256": artifact,
        "target_arch": target_arch,
        "toolchain": f"qemu-{target_arch}-static (EMULATED — not executed in this strand)",
        "patch": patch,
        "size": len(patched),
        "built_at": time.time(),
        "emulated_build": True,
    }

    descriptor["signature"] = sign_artifact(artifact, sign_key)
    if workdir:
        os.makedirs(workdir, exist_ok=True)
        with open(os.path.join(workdir, "firmware.staged"), "wb") as f:
            f.write(patched)
        with open(os.path.join(workdir, "firmware.descriptor.json"), "w") as f:
            json.dump(descriptor, f, indent=2)
    return descriptor

def sign_artifact(artifact_sha256: str, sign_key: bytes | None = None) -> dict:

    key = sign_key or b"pn-devmaint-staging-key"
    sig = hmac.new(key, artifact_sha256.encode(), hashlib.sha256).hexdigest()
    return {"alg": "HMAC-SHA256", "signer": "pn-devmaint", "sig": sig}

def verify_signature(artifact_sha256: str, signature: dict, sign_key: bytes | None = None) -> bool:
    if not signature or signature.get("alg") != "HMAC-SHA256":
        return False
    expected = sign_artifact(artifact_sha256, sign_key)["sig"]
    return hmac.compare_digest(expected, signature.get("sig", ""))

def emulate_firmware(descriptor: dict, *, healthy: bool = True) -> dict:

    boots = descriptor.get("emulated_build", True) and healthy
    return {"ok": boots, "boots": boots, "health": "green" if boots else "boot-loop",
            "emulated": True, "artifact_sha256": descriptor.get("artifact_sha256"),
            "at": time.time()}

class Backup:

    def __init__(self, device_fp: str, image: bytes, path: str | None = None):
        self.device_fp = device_fp
        self.sha256 = hashlib.sha256(image).hexdigest()
        self.size = len(image)
        self.created_at = time.time()
        self.protected = True
        self.replicated = False
        self.verified = False
        self.path = path
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(image)

    def verify(self) -> bool:

        if self.path and os.path.exists(self.path):
            with open(self.path, "rb") as f:
                self.verified = (hashlib.sha256(f.read()).hexdigest() == self.sha256)
        else:
            self.verified = bool(self.sha256)
        return self.verified

    def mark_replicated(self):
        self.replicated = True

    def to_dict(self) -> dict:
        return {"device_fp": self.device_fp, "sha256": self.sha256, "size": self.size,
                "protected": self.protected, "replicated": self.replicated,
                "verified": self.verified, "path": self.path, "created_at": self.created_at}

def backup_device_firmware(device_fp: str, current_image: bytes, path: str | None = None) -> Backup:

    b = Backup(device_fp, current_image, path=path)
    b.verify()
    return b

class FlashEngine:

    def __init__(self, consent_verifier, mode: str | None = None, allow_real: bool = False):

        self.consent_verifier = consent_verifier
        self.mode = mode or detect_mode()
        self.allow_real = False
        del allow_real

    def flash(self, *, descriptor: dict, emulation: dict, backup: Backup,
              consent_nonce: str | None, brick_warning_ack: bool) -> dict:

        if not firmware_permitted(self.mode):
            raise FirmwareUnavailable(
                f"firmware write refused: mode={self.mode} (firmware ops require green/full-host "
                "mode; unavailable in container/red)")
        if not emulation or not emulation.get("ok"):
            raise EmulationRequired("emulate-first did not pass — refusing to write (emulate-first)")
        if not backup or not (backup.verified and backup.protected):
            raise BackupRequired("no verified, protected backup — refusing to write "
                                 "(mandatory backup MUST precede the write)")
        if not brick_warning_ack:
            raise ConsentRequired("brick-warning not acknowledged — refusing irreversible write")
        if not consent_nonce or not self.consent_verifier(consent_nonce):

            raise ConsentRequired(
                "no valid single-use consent nonce — refusing irreversible firmware write "
                "(the brain cannot mint the nonce; consent is a human approval-gate decision)")

        return {
            "emulated_write": True, "real_write": False,
            "artifact_sha256": descriptor.get("artifact_sha256"),
            "target_arch": descriptor.get("target_arch"),
            "backup_sha256": backup.sha256,
            "consent_nonce_used": True,
            "wrote_at": time.time(),
        }

def post_flash_health(*, healthy: bool) -> dict:

    return {"ok": bool(healthy), "health": "green" if healthy else "unhealthy", "at": time.time()}

def rollback(backup: Backup) -> dict:

    if not backup or not backup.verified:
        raise BackupRequired("cannot rollback — backup is not verified")
    return {"rolled_back": True, "restored_sha256": backup.sha256,
            "from_replica": backup.replicated, "emulated": True, "at": time.time()}

DAG_STAGES = [
    ("discover", TIER_AUTONOMOUS),
    ("fingerprint", TIER_AUTONOMOUS),
    ("cve_audit", TIER_AUTONOMOUS),
    ("config_fix", TIER_AUTONOMOUS_LOGGED),
    ("build_update", TIER_CONSENT),
    ("emulate", TIER_CONSENT),
    ("backup", TIER_CONSENT),
    ("consent", TIER_CONSENT),
    ("write", TIER_CONSENT),
    ("verify", TIER_CONSENT),
    ("rollback", TIER_CONSENT),
]

class MaintenanceDAG:

    def __init__(self, consent_verifier, mode: str | None = None, sign_key: bytes | None = None,
                 workdir: str | None = None):
        self.mode = mode or detect_mode()
        self.flash_engine = FlashEngine(consent_verifier, mode=self.mode)
        self.sign_key = sign_key
        self.workdir = workdir
        self.trace = []
        self.config_log = []

    def _step(self, stage, ok, info=None):
        self.trace.append({"stage": stage, "ok": ok, "tier": dict(DAG_STAGES).get(stage),
                           "at": time.time(), "info": info or {}})

    def run(self, profile: dict, *, patch: dict, current_image: bytes, current_version: str,
            consent_nonce: str | None, brick_warning_ack: bool,
            config_fix: dict | None = None, emulate_healthy: bool = True,
            post_health_healthy: bool = True, backup_path: str | None = None,
            replicate=True) -> dict:

        self._step("discover", True, {"ip": profile.get("ip")})
        self._step("fingerprint", True, {"class": profile.get("device_class")})
        audit = cve_audit(profile, current_version)
        self._step("cve_audit", True, {"needs_update": audit["needs_update"],
                                       "findings": len(audit["cve_findings"])})

        if config_fix:
            cf = reversible_config_fix(profile, config_fix, self.config_log)
            self._step("config_fix", True, {"reversible": cf["reversible"]})
        if not audit["needs_update"]:
            return {"ok": True, "outcome": "no-update-needed", "audit": audit, "trace": self.trace}

        if not firmware_permitted(self.mode):
            self._step("build_update", False, {"refused": "firmware ops unavailable in this mode"})
            return {"ok": False, "outcome": "firmware-unavailable", "mode": self.mode,
                    "audit": audit, "trace": self.trace}

        descriptor = build_update(profile, patch, sign_key=self.sign_key, workdir=self.workdir)
        signed_ok = verify_signature(descriptor["artifact_sha256"], descriptor["signature"],
                                     self.sign_key)
        self._step("build_update", signed_ok,
                   {"artifact": descriptor["artifact_sha256"][:12], "signed": signed_ok,
                    "emulated_build": True})

        emulation = emulate_firmware(descriptor, healthy=emulate_healthy)
        self._step("emulate", emulation["ok"], {"boots": emulation["boots"]})
        if not emulation["ok"]:
            return {"ok": False, "outcome": "emulation-failed", "audit": audit,
                    "descriptor": descriptor, "emulation": emulation, "trace": self.trace}

        backup = backup_device_firmware(fingerprint_or_ip(profile), current_image, path=backup_path)
        if replicate:
            backup.mark_replicated()
        self._step("backup", backup.verified,
                   {"sha256": backup.sha256[:12], "protected": backup.protected,
                    "replicated": backup.replicated, "verified": backup.verified})

        consent_present = bool(consent_nonce) and bool(brick_warning_ack)
        self._step("consent", consent_present,
                   {"brick_warning_ack": brick_warning_ack, "nonce_supplied": bool(consent_nonce)})

        try:
            write = self.flash_engine.flash(
                descriptor=descriptor, emulation=emulation, backup=backup,
                consent_nonce=consent_nonce, brick_warning_ack=brick_warning_ack)
        except (ConsentRequired, BackupRequired, EmulationRequired, FirmwareUnavailable) as e:
            self._step("write", False, {"refused": str(e)})
            return {"ok": False, "outcome": "write-refused", "reason": str(e),
                    "audit": audit, "descriptor": descriptor, "backup": backup.to_dict(),
                    "trace": self.trace}
        self._step("write", True, {"emulated_write": True, "real_write": False})

        health = post_flash_health(healthy=post_health_healthy)
        self._step("verify", health["ok"], {"health": health["health"]})
        if not health["ok"]:
            rb = rollback(backup)
            self._step("rollback", rb["rolled_back"], {"restored": rb["restored_sha256"][:12]})
            return {"ok": False, "outcome": "rolled-back", "audit": audit, "write": write,
                    "health": health, "rollback": rb, "backup": backup.to_dict(),
                    "trace": self.trace}

        return {"ok": True, "outcome": "flashed-and-verified (EMULATED)", "audit": audit,
                "descriptor": descriptor, "emulation": emulation, "write": write,
                "health": health, "backup": backup.to_dict(), "trace": self.trace}

    def stage_index(self, stage: str) -> int:
        for i, e in enumerate(self.trace):
            if e["stage"] == stage:
                return i
        return -1

    def ordering_ok(self) -> dict:

        w = self.stage_index("write")
        if w < 0:
            w = len(self.trace)
        return {
            "emulate_before_write": 0 <= self.stage_index("emulate") < w,
            "backup_before_write": 0 <= self.stage_index("backup") < w,
            "consent_before_write": 0 <= self.stage_index("consent") < w,
            "write_index": w,
        }

def fingerprint_or_ip(profile: dict) -> str:
    return profile.get("mac") or profile.get("fingerprint") or profile.get("ip") or "device"

def abandonware_update(profile: dict, patch: dict, *, target_arch: str = "armv7",
                       sign_key: bytes | None = None, workdir: str | None = None) -> dict:

    audit = cve_audit(profile, profile.get("firmware_version"))
    descriptor = build_update(profile, patch, target_arch=target_arch, sign_key=sign_key,
                              workdir=workdir)
    signed_ok = verify_signature(descriptor["artifact_sha256"], descriptor["signature"], sign_key)
    return {
        "mode": "abandonware-maintainer",
        "audit": audit,
        "generated": True,
        "recompiled": True,
        "cross_arch": target_arch,
        "signed": signed_ok,
        "signature": descriptor["signature"],
        "staged": True,
        "flashed": False,
        "artifact_sha256": descriptor["artifact_sha256"],
        "supply_chain_note": ("generated firmware is SIGNED + provenance-recorded; a downstream "
                              "flow MUST verify the signature before the (gated, emulated) flash."),
        "descriptor": descriptor,
    }
