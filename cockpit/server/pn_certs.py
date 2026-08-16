#!/usr/bin/env python3

import os, socket, subprocess, sys, tempfile, hashlib, fcntl, contextlib

HOME = os.path.expanduser("~")
CFG_DIR = os.environ.get("PN_CERT_DIR") or os.path.join(HOME, ".config", "brainbox-portal")

LEAF_DAYS = 397
CA_DAYS = 3650
RENEW_BEFORE_DAYS = 30

_PRIVATE_CIDRS = ["10.0.0.0/255.0.0.0", "172.16.0.0/255.240.0.0", "192.168.0.0/255.255.0.0",
                  "127.0.0.1/255.255.255.255", "169.254.0.0/255.255.0.0"]

def _ca_name_constraints(cfg=None):

    cfg = cfg or {}
    if cfg.get("cert_ca_unconstrained"):
        return ""
    dns, _ips, _entries = collect_sans(cfg)
    perm = ["permitted;DNS:.local", "permitted;DNS:localhost"]
    for d in dns:
        if d in ("localhost",) or d.endswith(".local"):
            continue
        perm.append("permitted;DNS:" + d)
    perm += ["permitted;IP:" + c for c in _PRIVATE_CIDRS]
    return ",".join(_dedup(perm))

@contextlib.contextmanager
def _certs_lock(cfg_dir=None):

    d = cfg_dir or CFG_DIR
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    f = open(os.path.join(d, ".pn_certs.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()

def _is_virtual_iface(name):
    n = (name or "").lower()
    return n == "lo" or n.startswith(("docker", "veth", "br-", "virbr", "tun", "tap", "wg",
                                      "tailscale", "zt", "cni", "flannel", "kube", "cali"))

def _is_private4(ip):
    try:
        o = [int(x) for x in ip.split(".")]
    except Exception:
        return False
    return len(o) == 4 and (o[0] == 10 or (o[0] == 172 and 16 <= o[1] <= 31)
                            or (o[0] == 192 and o[1] == 168))

def _paths(cfg_dir=None):
    d = cfg_dir or CFG_DIR
    return {
        "cfg": d,
        "ca_dir": os.path.join(d, "ca"),
        "ca_cert": os.path.join(d, "ca", "brainbox-ca.pem"),
        "ca_key": os.path.join(d, "ca", "brainbox-ca.key"),
        "leaf_cert": os.path.join(d, "cert.pem"),
        "leaf_key": os.path.join(d, "key.pem"),
        "stamp": os.path.join(d, "cert.sans"),
    }

def _run(args):
    return subprocess.run(args, capture_output=True, text=True)

def _have_openssl():
    try:
        return _run(["openssl", "version"]).returncode == 0
    except Exception:
        return False

def _dedup(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def _valid_label(name):

    import re
    n = (name or "").strip().split(".")[0]
    if not n or n.lower() in ("none", "(none)", "localhost", "unknown"):
        return ""
    return n if re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", n) else ""

def box_hostname():
    return _valid_label(socket.gethostname()) or "brainbox"

def primary_ipv4():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return ""

def _same_24(a, b):
    try:
        return a.split(".")[:3] == b.split(".")[:3]
    except Exception:
        return False

def lan_ipv4s():

    primary = primary_ipv4()
    out = set()
    if primary and _is_private4(primary):
        out.add(primary)
    try:
        for ln in _run(["ip", "-4", "-o", "addr"]).stdout.splitlines():
            parts = ln.split()
            iface = parts[1] if len(parts) > 1 else ""
            if _is_virtual_iface(iface):
                continue
            for i, p in enumerate(parts):
                if p == "inet" and i + 1 < len(parts):
                    ip = parts[i + 1].split("/")[0]
                    if not ip or ip.startswith("169.254."):
                        continue
                    if primary:
                        if _same_24(ip, primary):
                            out.add(ip)
                    elif _is_private4(ip):

                        out.add(ip)
    except Exception:
        pass
    return sorted(out)

def _mdns_names():

    out = []
    try:
        with open("/etc/avahi/avahi-daemon.conf") as f:
            for ln in f:
                s = ln.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                key, _, val = s.partition("=")
                if key.strip() != "host-name":
                    continue
                name = _valid_label(val)
                if name:
                    out.append(name + ".local")
    except OSError:
        pass
    return out

def keepalived_vips(path="/etc/keepalived/keepalived.conf"):

    out = []
    try:
        with open(path) as f:
            block = False
            for ln in f:
                s = ln.split("#", 1)[0].strip()
                if not s:
                    continue
                if s.startswith("virtual_ipaddress"):
                    block = "}" not in s
                    s = s.split("{", 1)[1] if "{" in s else ""
                elif not block:
                    continue
                if "}" in s:
                    block = False
                    s = s.split("}", 1)[0]
                for tok in s.replace("{", " ").split():
                    ip = tok.split("/")[0]
                    if _is_private4(ip):
                        out.append(ip)
                        break
    except OSError:
        pass
    return _dedup(out)

def _remembered_sans(cfg_dir=None):

    try:
        with open(os.path.join(cfg_dir or CFG_DIR, "cert.sans.union")) as f:
            return [l.strip() for l in f if l.strip()][:32]
    except OSError:
        return []

def _remember_sans(entries, cfg_dir=None):
    try:
        p = os.path.join(cfg_dir or CFG_DIR, "cert.sans.union")
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(_dedup(list(entries))[:32]) + "\n")
        os.replace(tmp, p)
    except OSError:
        pass

def collect_sans(cfg=None, cfg_dir=None):
    cfg = cfg or {}
    host = box_hostname()
    dns = ["localhost", host, host + ".local"]
    dns += _mdns_names()
    for n in (cfg.get("cert_names") or []):
        dns.append(str(n))

    extra = cfg.get("cert_extra_names")
    if extra is None:
        extra = []
    for n in extra:
        dns.append(str(n))
    ips = set(lan_ipv4s()); ips.add("127.0.0.1")
    ips.update(keepalived_vips())
    for k in ("lan_ip", "vip", "vip_ip"):
        if cfg.get(k):
            ips.add(str(cfg[k]))
    for ip in (cfg.get("cert_ips") or []):
        ips.add(str(ip))

    for e in _remembered_sans(cfg_dir):
        if e.startswith("DNS:"):
            dns.append(e[4:])
        elif e.startswith("IP:"):
            ip = e[3:]
            if ip == "127.0.0.1" or _is_private4(ip):
                ips.add(ip)
    dns = _dedup([d for d in dns if d]); ips = sorted(ips)
    entries = ["DNS:" + d for d in dns] + ["IP:" + i for i in ips]
    return dns, ips, entries

def _nonempty(path):
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _ensure_ca_locked(cfg_dir=None, cfg=None):

    P = _paths(cfg_dir)
    os.makedirs(P["ca_dir"], exist_ok=True)
    try:
        os.chmod(P["ca_dir"], 0o700)
    except Exception:
        pass
    if _nonempty(P["ca_cert"]) and _nonempty(P["ca_key"]):
        return P["ca_cert"], P["ca_key"]
    host = box_hostname()
    subj = "/O=Brainbox/OU=Appliance/CN=Brainbox Root CA (%s)" % host
    tmp_cert = P["ca_cert"] + ".tmp"; tmp_key = P["ca_key"] + ".tmp"
    args = ["openssl", "req", "-x509", "-newkey", "rsa:4096", "-nodes",
            "-keyout", tmp_key, "-out", tmp_cert, "-days", str(CA_DAYS), "-subj", subj,
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    nc = _ca_name_constraints(cfg)
    if nc:
        args += ["-addext", "nameConstraints=critical," + nc]
    r = _run(args)
    if r.returncode != 0:
        for t in (tmp_cert, tmp_key):
            try: os.remove(t)
            except Exception: pass
        raise RuntimeError("CA gen failed: " + (r.stderr or "")[:200])
    try:
        os.chmod(tmp_key, 0o600)
    except Exception:
        pass

    os.replace(tmp_key, P["ca_key"]); os.replace(tmp_cert, P["ca_cert"])
    return P["ca_cert"], P["ca_key"]

def ensure_ca(cfg_dir=None, cfg=None):

    P = _paths(cfg_dir)
    if _nonempty(P["ca_cert"]) and _nonempty(P["ca_key"]):
        return P["ca_cert"], P["ca_key"]
    with _certs_lock(cfg_dir):
        return _ensure_ca_locked(cfg_dir, cfg)

def _atomic_write(path, data, mode=None):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    if mode is not None:
        try:
            os.chmod(tmp, mode)
        except Exception:
            pass
    os.replace(tmp, path)

def issue_leaf(cfg=None, cfg_dir=None, force=False):
    cfg = cfg or {}
    P = _paths(cfg_dir)
    dns, ips, entries = collect_sans(cfg, cfg_dir)
    san = ",".join(entries)
    stamp = hashlib.sha256(san.encode()).hexdigest()
    def _cached_ok():

        if force or not all(_nonempty(P[k]) for k in ("leaf_cert", "leaf_key", "stamp")):
            return False
        try:
            if open(P["stamp"]).read().strip() != stamp:
                return False

            ck = _run(["openssl", "x509", "-in", P["leaf_cert"], "-noout",
                       "-checkend", str(RENEW_BEFORE_DAYS * 86400)])
            if ck.returncode != 0:
                return False
            _assert_loadable(open(P["leaf_cert"]).read(), open(P["leaf_key"]).read())
            return True
        except Exception:
            return False
    if _cached_ok():
        return P["leaf_cert"], P["leaf_key"], False
    with _certs_lock(cfg_dir):

        if _cached_ok():
            return P["leaf_cert"], P["leaf_key"], False
        ca_cert, ca_key = _ensure_ca_locked(cfg_dir, cfg)
        primary = dns[1] if len(dns) > 1 else dns[0]
        with tempfile.TemporaryDirectory() as td:
            key = os.path.join(td, "leaf.key"); csr = os.path.join(td, "leaf.csr")
            crt = os.path.join(td, "leaf.crt"); ext = os.path.join(td, "ext.cnf")
            open(ext, "w").write(
                "subjectAltName=%s\nbasicConstraints=CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n" % san)
            r = _run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                      "-out", csr, "-subj", "/O=Brainbox/CN=%s" % primary])
            if r.returncode != 0:
                raise RuntimeError("leaf csr failed: " + (r.stderr or "")[:200])

            def _sign_verify(san_str):

                open(ext, "w").write(
                    "subjectAltName=%s\nbasicConstraints=CA:FALSE\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\n"
                    "extendedKeyUsage=serverAuth\n" % san_str)
                s = _run(["openssl", "x509", "-req", "-in", csr, "-CA", ca_cert, "-CAkey", ca_key,
                          "-CAcreateserial", "-out", crt, "-days", str(LEAF_DAYS), "-sha256",
                          "-extfile", ext])
                if s.returncode != 0:
                    return False, "leaf sign failed: " + (s.stderr or "")[:200]
                v = _run(["openssl", "verify", "-CAfile", ca_cert, crt])
                if v.returncode != 0:
                    return False, "verify failed: " + (v.stdout or "") + (v.stderr or "")
                return True, ""

            ok_sign, why = _sign_verify(san)
            if not ok_sign:

                kept = [e for e in entries
                        if not (e.startswith("DNS:") and "." not in e[4:] and e[4:] != "localhost")]
                san_retry = ",".join(kept)
                if san_retry != san:
                    ok_sign, why = _sign_verify(san_retry)
            if not ok_sign:
                raise RuntimeError(why)
            fullchain = open(crt).read() + open(ca_cert).read()
            leaf_key_pem = open(key).read()

        _assert_loadable(fullchain, leaf_key_pem)

        _atomic_write(P["leaf_key"], leaf_key_pem, mode=0o600)
        _atomic_write(P["leaf_cert"], fullchain)
        _atomic_write(P["stamp"], stamp)
        _remember_sans(entries, cfg_dir)
        return P["leaf_cert"], P["leaf_key"], True

def _assert_loadable(cert_pem, key_pem):
    import ssl
    with tempfile.TemporaryDirectory() as td:
        cp = os.path.join(td, "c.pem"); kp = os.path.join(td, "k.pem")
        open(cp, "w").write(cert_pem); open(kp, "w").write(key_pem)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cp, kp)

def ensure_portal_cert(cfg=None, cfg_dir=None):
    cfg = cfg or {}
    P = _paths(cfg_dir)
    if not _have_openssl():
        if _nonempty(P["leaf_cert"]) and _nonempty(P["leaf_key"]):
            return P["leaf_cert"], P["leaf_key"]
        return None, None
    try:
        cert, key, _new = issue_leaf(cfg, cfg_dir)
        return cert, key
    except Exception as e:
        sys.stderr.write("pn_certs: %s\n" % e)
        if _nonempty(P["leaf_cert"]) and _nonempty(P["leaf_key"]):
            return P["leaf_cert"], P["leaf_key"]
        return None, None

def ca_cert_path(cfg_dir=None):
    return _paths(cfg_dir)["ca_cert"]

def ca_fingerprint_sha256(cfg_dir=None):
    p = ca_cert_path(cfg_dir)
    if not os.path.exists(p):
        return ""
    r = _run(["openssl", "x509", "-in", p, "-noout", "-fingerprint", "-sha256"])
    return r.stdout.strip().split("=")[-1] if r.returncode == 0 else ""

def _cli():
    args = sys.argv[1:]
    cfg_dir = None
    if "--dir" in args:
        i = args.index("--dir"); cfg_dir = args[i + 1]; del args[i:i + 2]
    force = "--force" in args
    cmd = args[0] if args else "ensure"
    if cmd == "sans":
        dns, ips, entries = collect_sans({})
        print("DNS:", dns); print("IP :", ips); return
    if cmd == "info":
        print("ca_cert:", ca_cert_path(cfg_dir))
        print("fingerprint:", ca_fingerprint_sha256(cfg_dir))
        dns, ips, _ = collect_sans({}); print("SANs:", dns, ips); return

    cert, key, new = issue_leaf({}, cfg_dir, force=force)
    print("cert:", cert); print("key :", key); print("reissued:", new)
    print("ca_fingerprint:", ca_fingerprint_sha256(cfg_dir))

if __name__ == "__main__":
    _cli()
