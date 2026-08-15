

import base64
import hashlib
import os
import re
import struct

RE_SSH_KEY = re.compile(r"^(?P<type>[A-Za-z0-9@._-]{1,40})[ \t]+"
                        r"(?P<blob>[A-Za-z0-9+/]{32,}={0,3})"
                        r"(?:[ \t]+(?P<comment>.*))?$")
SSH_KEY_TYPES = ("ssh-ed25519", "ssh-rsa",
                 "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                 "ecdsa-sha2-nistp521",
                 "sk-ssh-ed25519@openssh.com",
                 "sk-ecdsa-sha2-nistp256@openssh.com")
SSH_KEYS_MAX = 10
SSH_PASTE_MAX = 16384
SSH_COMMENT_MAX = 120

def parse(text):

    raw = (text or "").strip()
    if not raw:
        return True, []
    if len(raw) > SSH_PASTE_MAX:
        return False, "ssh_too_long"
    out, seen = [], set()
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = RE_SSH_KEY.match(line)
        if not m:
            return False, "ssh_bad_line|%d" % lineno
        ktype = m.group("type")
        if ktype not in SSH_KEY_TYPES:

            return False, "ssh_bad_type|%d" % lineno
        blob = m.group("blob")
        try:
            data = base64.b64decode(blob, validate=True)
        except Exception:
            return False, "ssh_bad_b64|%d" % lineno

        try:
            fields, off, ln = [], 0, len(data)
            while off < ln:
                if ln - off < 4:
                    raise ValueError("dangling bytes")
                fld = struct.unpack(">I", data[off:off + 4])[0]
                off += 4
                if fld > ln - off:
                    raise ValueError("truncated field")
                fields.append(data[off:off + fld])
                off += fld
                if len(fields) > 8:
                    raise ValueError("implausible")
        except Exception:
            return False, "ssh_bad_body|%d" % lineno
        if len(fields) < 2 or fields[0].decode("ascii", "replace") != ktype:
            return False, "ssh_bad_body|%d" % lineno
        comment = "".join(c for c in (m.group("comment") or "").strip()
                          if c.isprintable())[:SSH_COMMENT_MAX]
        if blob in seen:
            continue
        seen.add(blob)
        out.append(ktype + " " + blob + ((" " + comment) if comment else ""))
        if len(out) > SSH_KEYS_MAX:
            return False, "ssh_too_many"
    return True, out

def fingerprint(line):

    parts = (line or "").split()
    if len(parts) < 2:
        return ""
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except Exception:
        return ""
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")

def body(line):

    p = (line or "").split()
    return p[1] if len(p) > 1 else (line or "")

def path_for(home):
    return os.path.join(home, ".ssh", "authorized_keys")

def read(home):

    out = []
    try:
        with open(path_for(home), "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                m = RE_SSH_KEY.match(s)
                if m and m.group("type") in SSH_KEY_TYPES:
                    out.append({"index": i, "parsed": True,
                                "type": m.group("type"),
                                "comment": (m.group("comment") or "").strip()[:SSH_COMMENT_MAX],
                                "fp": fingerprint(s)})
                else:
                    out.append({"index": i, "parsed": False, "type": "",
                                "comment": s[:60], "fp": ""})
    except OSError:
        pass
    return out

def _atomic_write(path, text, uid=None, gid=None):
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, ".authorized_keys.tmp.%d" % os.getpid())
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    if uid is not None and os.geteuid() == 0:
        try:
            os.chown(tmp, uid, gid if gid is not None else uid)
        except OSError:
            pass
    os.replace(tmp, path)

def add(home, keys, uid=None, gid=None):

    p = path_for(home)
    d = os.path.dirname(p)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
        if uid is not None and os.geteuid() == 0:
            os.chown(d, uid, gid if gid is not None else uid)
    except OSError:
        pass
    existing = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            existing = [ln.rstrip("\n") for ln in f]
    except OSError:
        pass
    have = set(body(ln) for ln in existing
               if ln.strip() and not ln.lstrip().startswith("#"))
    added = [k for k in keys if body(k) not in have]
    if added:
        text = "\n".join(ln for ln in (existing + added) if ln.strip()) + "\n"
        _atomic_write(p, text, uid, gid)
    total = len([x for x in read(home) if x["parsed"]])
    return len(added), total

def remove(home, fp, uid=None, gid=None):

    p = path_for(home)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError:
        return False, 0
    keep, hit = [], False
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and fingerprint(s) == fp and fp:
            hit = True
            continue
        keep.append(ln)
    if hit:
        text = "\n".join(ln for ln in keep if ln.strip())
        _atomic_write(p, (text + "\n") if text else "", uid, gid)
    return hit, len([x for x in read(home) if x["parsed"]])

def password_auth():

    try:
        import subprocess
        r = subprocess.run(["sshd", "-T"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=10)
        if r.returncode == 0:
            for line in r.stdout.decode("utf-8", "replace").splitlines():
                s = line.strip().lower()
                if s.startswith("passwordauthentication"):
                    return s.split()[-1] == "yes"
    except Exception:
        pass

    for f in ("/etc/ssh/sshd_config.d/99-brainbox-hardening.conf",
              "/etc/ssh/sshd_config"):
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    s = line.strip().lower()
                    if s.startswith("passwordauthentication"):
                        return s.split()[-1] == "yes"
        except OSError:
            continue
    return None
