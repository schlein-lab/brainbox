
from __future__ import annotations
import os, json, hmac, hashlib, secrets as _secrets, subprocess, shutil, stat, tempfile

SECRETS_DIR = os.environ.get("PN_SECRETS_DIR", "/var/lib/portioneer/secrets")
KEY_FILE    = "brain.key"
KIND_FILE   = "brain.kind"
META_FILE   = "seal.meta"
NOBACKUP    = ".nobackup"

VALID_KINDS = ("max-token", "api-key", "codex")

MAGIC = b"PNSEAL01"

def _age_available() -> bool:
    return bool(shutil.which("age") and shutil.which("age-keygen"))

def _cryptography_available() -> bool:
    try:
        import cryptography
        return True
    except Exception:
        return False

def _tpm_available() -> bool:

    if any(os.path.exists(p) for p in ("/dev/tpm0", "/dev/tpmrm0")):
        return True
    return False

def best_backend() -> str:
    if _age_available():
        return "age"
    if _cryptography_available():
        return "aesgcm"
    return "stdlib-insecure"

def _scrypt(passphrase: bytes, salt: bytes, n=2**15, r=8, p=1, dklen=32) -> bytes:
    return hashlib.scrypt(passphrase, salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=128 * 1024 * 1024)

def _passphrase() -> bytes:

    pw = os.environ.get("PN_SECRETS_PASSPHRASE")
    if pw:
        return pw.encode()

    host = (os.uname().nodename + ":" + str(os.getuid())).encode()
    return hashlib.sha256(b"pn-secrets-firstboot:" + host).digest()

def _age_identity_path() -> str:
    return os.path.join(SECRETS_DIR, ".age-identity")

def _seal_age(plaintext: bytes) -> tuple[bytes, dict]:
    idp = _age_identity_path()
    if not os.path.exists(idp):

        out = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True).stdout
        with open(idp, "w") as f:
            f.write(out)
        os.chmod(idp, 0o600)

    recip = subprocess.run(["age-keygen", "-y", idp], capture_output=True, text=True, check=True).stdout.strip()
    enc = subprocess.run(["age", "-r", recip, "-o", "-"], input=plaintext,
                         capture_output=True, check=True).stdout
    binding = "tpm" if _tpm_available() else "passphrase-NEEDS-TPM"
    return enc, {"backend": "age", "binding": binding, "recipient": recip}

def _unseal_age(blob: bytes, meta: dict) -> bytes:
    idp = _age_identity_path()
    dec = subprocess.run(["age", "-d", "-i", idp, "-o", "-"], input=blob,
                         capture_output=True, check=True).stdout
    return dec

def _seal_aesgcm(plaintext: bytes) -> tuple[bytes, dict]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = _secrets.token_bytes(16)
    nonce = _secrets.token_bytes(12)
    key = _scrypt(_passphrase(), salt)
    ct = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    blob = b"|".join((b"aesgcm", salt.hex().encode(), nonce.hex().encode(), ct.hex().encode()))
    binding = "tpm" if _tpm_available() else "first-boot-passphrase"
    return blob, {"backend": "aesgcm", "binding": binding, "kdf": "scrypt-n32768-r8-p1"}

def _unseal_aesgcm(blob: bytes, meta: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    tag, salt_h, nonce_h, ct_h = blob.split(b"|", 3)
    assert tag == b"aesgcm"
    key = _scrypt(_passphrase(), bytes.fromhex(salt_h.decode()))
    return AESGCM(key).decrypt(bytes.fromhex(nonce_h.decode()), bytes.fromhex(ct_h.decode()), MAGIC)

def _seal_stdlib(plaintext: bytes) -> tuple[bytes, dict]:
    if os.environ.get("PN_SECRETS_ALLOW_INSECURE") != "1":
        raise RuntimeError(
            "no real sealing backend (age/cryptography) available and stdlib backend is "
            "INSECURE. Install `age` or python-cryptography, or set "
            "PN_SECRETS_ALLOW_INSECURE=1 to use the stdlib stub (tests only).")
    salt = _secrets.token_bytes(16)
    key = _scrypt(_passphrase(), salt)

    ks = b"".join(hashlib.sha256(key + i.to_bytes(8, "big")).digest()
                  for i in range((len(plaintext) // 32) + 1))
    ct = bytes(a ^ b for a, b in zip(plaintext, ks))
    mac = hmac.new(key, MAGIC + salt + ct, hashlib.sha256).digest()
    blob = b"|".join((b"stdlib", salt.hex().encode(), ct.hex().encode(), mac.hex().encode()))
    return blob, {"backend": "stdlib-insecure", "binding": "first-boot-passphrase",
                  "WARNING": "INSECURE-STUB-NOT-FOR-PRODUCTION"}

def _unseal_stdlib(blob: bytes, meta: dict) -> bytes:
    tag, salt_h, ct_h, mac_h = blob.split(b"|", 3)
    assert tag == b"stdlib"
    salt = bytes.fromhex(salt_h.decode()); ct = bytes.fromhex(ct_h.decode())
    key = _scrypt(_passphrase(), salt)
    if not hmac.compare_digest(hmac.new(key, MAGIC + salt + ct, hashlib.sha256).digest(),
                               bytes.fromhex(mac_h.decode())):
        raise ValueError("seal integrity check failed (tampered or wrong key)")
    ks = b"".join(hashlib.sha256(key + i.to_bytes(8, "big")).digest()
                  for i in range((len(ct) // 32) + 1))
    return bytes(a ^ b for a, b in zip(ct, ks))

_SEALERS = {"age": _seal_age, "aesgcm": _seal_aesgcm, "stdlib-insecure": _seal_stdlib}
_UNSEALERS = {"age": _unseal_age, "aesgcm": _unseal_aesgcm, "stdlib-insecure": _unseal_stdlib}

def ensure_dir() -> None:

    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)
    nb = os.path.join(SECRETS_DIR, NOBACKUP)
    if not os.path.exists(nb):
        with open(nb, "w") as f:
            f.write("portioneer secrets — excluded from all off-box backups\n")
        os.chmod(nb, 0o600)

def seal(value: bytes) -> tuple[bytes, dict]:

    backend = best_backend()
    blob, meta = _SEALERS[backend](value)
    return MAGIC + blob, meta

def unseal(blob: bytes, meta: dict) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a portioneer sealed bundle")
    body = blob[len(MAGIC):]
    backend = meta.get("backend") or best_backend()
    return _UNSEALERS[backend](body, meta)

def _atomic_write(path: str, data: bytes, mode: int) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        os.write(fd, data); os.fchmod(fd, mode); os.fsync(fd); os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def write_cred(value: str, kind: str) -> dict:

    if kind not in VALID_KINDS:
        raise ValueError(f"invalid brain.kind {kind!r}; expected one of {VALID_KINDS}")
    if not value:
        raise ValueError("empty credential")
    ensure_dir()
    blob, meta = seal(value.encode())
    meta = {"version": 1, "kind": kind, **meta}
    _atomic_write(os.path.join(SECRETS_DIR, KEY_FILE), blob, 0o600)
    _atomic_write(os.path.join(SECRETS_DIR, KIND_FILE), (kind + "\n").encode(), 0o644)
    _atomic_write(os.path.join(SECRETS_DIR, META_FILE),
                  (json.dumps(meta, indent=2) + "\n").encode(), 0o600)

    fp = hashlib.sha256(value.encode()).hexdigest()[:12]
    return {"kind": kind, "backend": meta["backend"], "binding": meta["binding"],
            "sealed_bytes": len(blob), "fingerprint": fp, "path": SECRETS_DIR}

def read_kind() -> str | None:
    try:
        with open(os.path.join(SECRETS_DIR, KIND_FILE)) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def read_cred() -> str | None:

    kp = os.path.join(SECRETS_DIR, KEY_FILE)
    mp = os.path.join(SECRETS_DIR, META_FILE)
    if not os.path.exists(kp):
        return None
    with open(kp, "rb") as f:
        blob = f.read()
    meta = {}
    if os.path.exists(mp):
        with open(mp) as f:
            meta = json.load(f)
    return unseal(blob, meta).decode()

def status() -> dict:

    kp = os.path.join(SECRETS_DIR, KEY_FILE)
    present = os.path.exists(kp)
    st = {"present": present, "kind": read_kind(), "dir": SECRETS_DIR,
          "backend_available": best_backend(), "tpm": _tpm_available()}
    if present:
        st["dir_mode"] = oct(stat.S_IMODE(os.stat(SECRETS_DIR).st_mode))
        st["key_mode"] = oct(stat.S_IMODE(os.stat(kp).st_mode))
    mp = os.path.join(SECRETS_DIR, META_FILE)
    if os.path.exists(mp):
        with open(mp) as f:
            m = json.load(f)
        st["sealed_with"] = m.get("backend"); st["binding"] = m.get("binding")
    return st
