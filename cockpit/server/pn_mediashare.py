#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time

DATA_DIR = os.environ.get("PN_PORTAL_DATA",
                          os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal"))
REGISTRY = os.path.join(DATA_DIR, "mediashares.json")

parent_of = None

def _default_root():
    for cand in (os.environ.get("PN_SHARES_ROOT"), "/data/shares"):
        if not cand:
            continue
        if os.path.isdir(cand) and os.access(cand, os.W_OK):
            return cand
        parent = os.path.dirname(cand.rstrip("/"))
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            return cand
    return os.path.join(DATA_DIR, "shares")

SHARES_ROOT = _default_root()
SMB_CONF = os.environ.get("PN_SMB_CONF", "/etc/samba/smb.conf")
SITE_CONF = "/etc/brainbox/site.conf"

def media_enabled():
    val = ""
    try:
        with open(SITE_CONF, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("MEDIA_SERVER_ENABLED="):
                    val = ln.split("=", 1)[1].split("#")[0].strip().strip("\"'")
    except OSError:
        return False
    return val == "1"
PROVISION_HELPER = os.environ.get("PN_MEDIASHARE_HELPER", "pn-mediashare-provision")
SERVICE_USER = os.environ.get("PN_SHARES_OWNER") or (os.environ.get("USER") or "brainbox")
WORKGROUP = os.environ.get("PN_SMB_WORKGROUP", "WORKGROUP")
NETBIOS = os.environ.get("PN_SMB_NETBIOS", "BRAINBOX")

_SID_RE = re.compile(r"[^a-z0-9]+")

_SSO_UID_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,15}$")

import portal_zustand as _zst
_zst.register("pn_mediashare.ShareManager._ro_cache", "cache", __name__, ref=None,
              beschreibung="geparster Registry-Stand fuer NUR-LESE-Pfade (get/list/list_for/_users), invalidiert per (mtime_ns, size)-Signatur; je Instanz; Schreibpfade parsen immer frisch und invalidieren via _save()",
              neustart="verfaellt", schreiber="_load_ro() (Lese-Pfade)")
_SSO_DENY = frozenset((
    "root daemon bin sys sync games man lp mail news uucp proxy www-data backup list irc "
    "nobody sshd samba messagebus systemd-network sharemembers"
).split())

def smb_available():

    return bool(shutil.which("smbd") or os.path.exists("/usr/sbin/smbd"))

def _smb_user(sid):

    s = _SID_RE.sub("-", str(sid).lower()).strip("-")
    return ("bshare-" + s)[:20].rstrip("-")

def _sso_uid(principal):

    if not principal:
        return None
    uid = str(principal)
    if not _SSO_UID_RE.match(uid):
        return None
    if uid in _SSO_DENY or uid.startswith("bshare-") or uid == (SERVICE_USER or ""):
        return None
    return uid

class ShareManager:
    def __init__(self, root=SHARES_ROOT, registry=REGISTRY):
        self.root = root
        self.registry = registry
        self._lock = threading.RLock()

        if os.environ.get("PN_MEDIASHARE_NO_DAEMONS"):
            return
 
 
 
 
 
        if not media_enabled():
            return
        
        
        
        
        
        
        threading.Thread(target=self._ensure_daemons, daemon=True).start()

        threading.Thread(target=self._ensure_dlnad, daemon=True).start()

    @staticmethod
    def _ensure_daemons():
        try:
            argv = [PROVISION_HELPER, "ensure"]
            if os.geteuid() != 0:
                argv = ["sudo", "-n"] + argv
            subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        except Exception:
            pass

    @staticmethod
    def _ensure_dlnad():
        try:
            if not media_enabled():          
                return
            import socket as _s
            probe = _s.socket(_s.AF_INET, _s.SOCK_STREAM); probe.settimeout(0.5)
            up = (probe.connect_ex(("127.0.0.1", 8200)) == 0); probe.close()
            if up:
                return
            dlnad = os.path.join(os.path.dirname(os.path.realpath(__file__)), "pn_dlnad.py")
            if not os.path.exists(dlnad):
                return
            pub = os.path.join(SHARES_ROOT, "public")
            try:
                os.makedirs(pub, exist_ok=True)
            except OSError:
                pass
            subprocess.Popen(["python3", dlnad, "--root", pub, "--name", "Brainbox", "--port", "8200"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass

    def _load(self):
        try:
            with open(self.registry) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.registry) or ".", exist_ok=True)
        tmp = "%s.tmp.%d" % (self.registry, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(d, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, self.registry)

    def _load_ro(self):

        try:
            st = os.stat(self.registry)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        with self._lock:
            c = getattr(self, "_ro_cache", None)
            if c is not None and c[0] == sig:
                return c[1]
        d = self._load()
        with self._lock:
            self._ro_cache = (sig, d)
        return d

    def _records(self, reg=None):

        reg = reg if reg is not None else self._load_ro()
        return {k: v for k, v in reg.items() if k != "_users" and isinstance(v, dict)}

    def _users(self):

        u = self._load_ro().get("_users")
        return sorted(u) if isinstance(u, list) else []

    def _register_user(self, uid):

        with self._lock:
            reg = self._load()
            users = reg.get("_users")
            if not isinstance(users, list):
                users = []
            if uid in users:
                return False
            users.append(uid)
            reg["_users"] = users
            self._save(reg)
            return True

    def public_dir(self):
        return os.path.join(self.root, "public")

    def user_dir(self, uid):

        return os.path.join(self.root, "users", uid)

    def _ensure_user_dirs(self, uid):
        base = self.user_dir(uid)
        os.makedirs(os.path.join(base, "sessions"), exist_ok=True)
        for p in (base, os.path.join(base, "sessions")):
            try:
                os.chmod(p, 0o2775)
            except OSError:
                pass
        return base

    @staticmethod
    def _folder_name(sid, title=None):

        sidslug = _SID_RE.sub("-", str(sid).lower()).strip("-")
        short = (sidslug.rsplit("-", 1)[-1] or sidslug)[:12]
        t = _SID_RE.sub("-", str(title or "").lower()).strip("-")[:40]
        name = ((t + "-" + short) if t else sidslug).strip("-")[:64].strip("-")
        return name or "sitzung"

    def session_dir(self, sid, principal=None, title=None):

        uid = _sso_uid(principal)
        name = self._folder_name(sid, title)

        try:
            psid = parent_of(sid) if callable(parent_of) else None
        except Exception:
            psid = None
        if psid and str(psid) != str(sid):
            try:
                prec = (self._load() or {}).get(str(psid)) or {}
                pdir = prec.get("path")
            except Exception:
                pdir = None
            if pdir and os.path.isdir(pdir):
                return os.path.join(pdir, "children", name)
        if uid:
            return os.path.join(self.user_dir(uid), "sessions", name)
        return os.path.join(self.root, "sessions", name)

    def ensure_public(self):
        p = self.public_dir()
        os.makedirs(p, exist_ok=True)
        try:
            os.chmod(p, 0o2775)
        except OSError:
            pass

        return p

    def public_ro_hint(self):

        return self.public_dir()

    def ensure_user_sso(self, uid, password):

        uid = _sso_uid(uid)
        if not uid or not password:
            return None
        try:
            self._ensure_user_dirs(uid)
        except OSError:
            pass
        newshare = self._register_user(uid)
        ok = self._provision_sso(uid, password, self.user_dir(uid))

        if newshare:
            self.apply()
        return uid if ok else None

    def _seed_layout(self, path, title=None):

        try:
            os.makedirs(os.path.join(path, "tmp"), exist_ok=True)
            idx = os.path.join(path, "INDEX.md")
            if not os.path.exists(idx):
                with open(idx, "w", encoding="utf-8") as f:
                    f.write(
                        "# %s\n\n"
                        "Angelegt: %s\n\n"
                        "Ordnung dieser Ablage (Konvention der Box):\n"
                        "- Endergebnisse in sprechend benannte Dateien/Ordner; bei Versionen Datumspraefix JJJJ-MM-TT.\n"
                        "- tmp/ = Unfertiges & Zwischenstaende (darf jederzeit geleert werden).\n"
                        "- children/ = Ablagen von Unter-Sessions (nur bei Orchestratoren).\n"
                        "- Diese Tabelle pflegt die Session (was liegt wo):\n\n"
                        "| Datei/Ordner | Inhalt | Stand |\n|---|---|---|\n"
                        % ((title or os.path.basename(path)), time.strftime("%Y-%m-%d %H:%M")))
        except OSError:
            pass

    def ensure_share(self, sid, title=None, rotate=False, principal=None):

        uid = _sso_uid(principal)
        with self._lock:
            reg = self._load()
            rec = dict(reg.get(sid) if isinstance(reg.get(sid), dict) else {})
            if uid:

                self._ensure_user_dirs(uid)
                path = rec["path"] if (rec.get("sso") and rec.get("path")) else \
                    self.session_dir(sid, principal=uid, title=title)
                os.makedirs(path, exist_ok=True)
                try:
                    os.chmod(path, 0o2775)
                except OSError:
                    pass
                self._seed_layout(path, title or rec.get("title"))
                rec.update({"sid": sid, "title": title or rec.get("title") or sid,
                            "sso": True, "owner_uid": uid, "user": None, "password": None,
                            "path": path, "share": uid,
                            "subpath": os.path.relpath(path, self.user_dir(uid)),
                            "unc": "\\\\%s\\%s" % (NETBIOS.lower(), uid),
                            "created": rec.get("created") or time.time()})
                reg[sid] = rec
                users = reg.get("_users")
                if not isinstance(users, list):
                    users = []
                if uid not in users:
                    users.append(uid)
                reg["_users"] = users
                self._save(reg)
            else:
                path = rec["path"] if (rec.get("path") and not rec.get("sso")) else self.session_dir(sid, title=title)
                os.makedirs(path, exist_ok=True)
                try:
                    os.chmod(path, 0o2770)
                except OSError:
                    pass
                self._seed_layout(path, title or rec.get("title"))
                user = rec.get("user") or _smb_user(sid)
                if rotate or not rec.get("password"):
                    rec["password"] = secrets.token_urlsafe(9)
                slug = _SID_RE.sub("-", str(sid).lower()).strip("-")[:32]
                rec.update({"sid": sid, "title": title or rec.get("title") or sid,
                            "user": user, "path": path, "sso": False,
                            "unc": "\\\\%s\\session-%s" % (NETBIOS.lower(), slug),
                            "share": "session-" + slug,
                            "created": rec.get("created") or time.time()})
                reg[sid] = rec
                self._save(reg)
        if not uid:

            self._provision(rec["user"], rec["password"], path)
        self.apply()
        return self.public_record(rec)

    def remove_share(self, sid, wipe=False):
        with self._lock:
            reg = self._load()
            rec = reg.pop(sid, None)
            if not isinstance(rec, dict):
                rec = None
            self._save(reg)
        if rec:

            if not rec.get("sso") and rec.get("user"):
                self._provision(rec.get("user"), None, None, remove=True)
            if wipe and rec.get("path"):
                shutil.rmtree(rec["path"], ignore_errors=True)
            self.apply()
        return bool(rec)

    def get(self, sid):
        rec = self._load_ro().get(sid)
        return self.public_record(rec) if isinstance(rec, dict) else None

    def list(self, include_archived=False):

        return [self.public_record(r) for r in self._records().values()
                if include_archived or not r.get("archived")]

    def list_for(self, principal, admin=False, include_archived=False):

        uid = _sso_uid(principal)
        out = []
        for rec in self._records().values():
            if rec.get("archived") and not include_archived:
                continue
            if admin:
                out.append(self.public_record(rec))
                continue
            owner = rec.get("owner_uid") if rec.get("sso") else None
            shared = rec.get("shared_with")
            if uid and (owner == uid or (isinstance(shared, list) and uid in shared)):
                out.append(self.public_record(rec))
        return out

    @staticmethod
    def public_record(rec):
        if not isinstance(rec, dict) or not rec:
            return None
        out = {k: rec.get(k) for k in ("sid", "title", "user", "password", "path", "unc", "share")}
        if rec.get("sso"):
            out["sso"] = True
            out["owner_uid"] = rec.get("owner_uid")
            out["subpath"] = rec.get("subpath")
            out["hint"] = ("Mit deinem Portal-Login erreichbar unter %s" % (rec.get("unc") or "")).strip()
        if rec.get("archived"):
            out["archived"] = True
            out["archived_at"] = rec.get("archived_at")
        return out

    def archive_dir_for(self, rec):

        if rec.get("sso") and rec.get("owner_uid"):
            return os.path.join(self.user_dir(rec["owner_uid"]), "archiv")
        return os.path.join(self.root, "_archiv")

    def archive_share(self, sid, apply_now=True):

        with self._lock:
            reg = self._load()
            rec = reg.get(sid)
            if not isinstance(rec, dict) or rec.get("archived"):
                return None
            src = rec.get("path")
            adir = self.archive_dir_for(rec)
            dest, moved = None, False
            if src and os.path.isdir(src):
                try:
                    os.makedirs(adir, exist_ok=True)
                    base = os.path.basename(src.rstrip("/")) or str(sid)
                    dest = os.path.join(adir, base)
                    if os.path.exists(dest):
                        dest = os.path.join(adir, "%s-%s" % (base, time.strftime("%Y%m%d-%H%M%S")))
                    shutil.move(src, dest)
                    moved = True
                except OSError:
                    dest, moved = None, False
            rec = dict(rec)
            rec["archived"] = True
            rec["archived_at"] = time.time()
            if moved and dest:
                rec["archived_from"] = src
                rec["archived_path"] = dest
                rec["path"] = dest
            reg[sid] = rec
            self._save(reg)

        if not rec.get("sso") and rec.get("user"):
            self._provision(rec.get("user"), None, None, remove=True)
        if apply_now:
            self.apply()
        return self.public_record(rec)

    def unarchive_share(self, sid, apply_now=True):

        with self._lock:
            reg = self._load()
            rec = reg.get(sid)
            if not isinstance(rec, dict) or not rec.get("archived"):
                return None
            apath = rec.get("archived_path") or rec.get("path")
            principal = rec.get("owner_uid") if rec.get("sso") else None
            dest = self.session_dir(sid, principal=principal, title=rec.get("title"))
            moved = False
            if apath and os.path.isdir(apath):
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if os.path.exists(dest):
                        dest = "%s-restored-%s" % (dest, time.strftime("%Y%m%d-%H%M%S"))
                    shutil.move(apath, dest)
                    moved = True
                except OSError:
                    dest, moved = apath, False
            rec = dict(rec)
            for k in ("archived", "archived_at", "archived_from", "archived_path"):
                rec.pop(k, None)
            rec["path"] = dest if moved else apath
            reg[sid] = rec
            self._save(reg)
        if not rec.get("sso") and rec.get("user"):
            self._provision(rec.get("user"), rec.get("password"), rec.get("path"))
        if apply_now:
            self.apply()
        return self.public_record(rec)

    def _provision(self, user, password, path, remove=False):

        if not user:
            return False
        argv = [PROVISION_HELPER, "remove" if remove else "add", user]
        if not remove and path:
            argv.append(path)
        runner = argv if os.geteuid() == 0 else (["sudo", "-n"] + argv)
        try:
            subprocess.run(runner, input=(password or "").encode(), capture_output=True, timeout=30)
            return True
        except Exception:
            return False

    def _provision_sso(self, uid, password, path):

        if not uid or not password:
            return False
        argv = [PROVISION_HELPER, "sso", uid]
        if path:
            argv.append(path)
        runner = argv if os.geteuid() == 0 else (["sudo", "-n"] + argv)
        try:
            r = subprocess.run(runner, input=(password or "").encode(), capture_output=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    def render_smb_conf(self):

        _have_users = bool(self._users())
        L = [
            "# GENERATED by pn_mediashare — do not edit by hand; edits are overwritten on the next",
            "# session provision. Share layout: one gated folder per session + a public space.",
            "[global]",
            "   workgroup = %s" % WORKGROUP,
            "   netbios name = %s" % NETBIOS,
            "   server string = Brainbox Medienserver",
            "   security = user",
            "   map to guest = %s" % ("never" if _have_users else "bad user"),

            "   server min protocol = SMB2",

            "   smb encrypt = off",
            "   disable netbios = no",
            "   load printers = no",
            "   printing = bsd",
            "   printcap name = /dev/null",
            "",
            "[public]",
            "   comment = Öffentlich (jeder im LAN liest+schreibt; Agenten nur lesend via Zell-Mount)",
            "   path = %s" % self.public_dir(),
            "   browseable = yes",
            "   read only = no",

            ("   valid users = @sharemembers" if _have_users else "   guest ok = yes"),
            "   force user = %s" % SERVICE_USER,
            "   force group = sharemembers",
            "   create mask = 0664",
            "   directory mask = 2775",
            "",
        ]

        for uid in self._users():
            L += [
                "[%s]" % uid,
                "   comment = Deine Sitzungen & Dateien (Portal-Login)",
                "   path = %s" % self.user_dir(uid),
                "   browseable = yes",
                "   read only = no",
                "   valid users = %s" % uid,
                "   force user = %s" % SERVICE_USER,
                "   force group = sharemembers",
                "   create mask = 0664",
                "   directory mask = 2775",
                "",
            ]

        for rec in self._records().values():
            if rec.get("sso") or rec.get("archived"):
                continue
            share, user = rec.get("share"), rec.get("user")
            path = rec.get("path")
            if not (share and user and path):
                continue
            L += [
                "[%s]" % share,
                "   comment = Sitzung %s" % (rec.get("title") or rec.get("sid")),
                "   path = %s" % path,
                "   browseable = no",
                "   read only = no",
                "   force user = %s" % SERVICE_USER,
                "   valid users = %s" % user,
                "   create mask = 0660",
                "   directory mask = 2770",
                "",
            ]
        return "\n".join(L) + "\n"

    def write_conf(self, path=None):

        target = path or SMB_CONF
        text = self.render_smb_conf()
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w") as f:
                f.write(text)
            return target
        except OSError:
            fallback = os.path.join(DATA_DIR, "smb.conf.preview")
            with open(fallback, "w") as f:
                f.write(text)
            return fallback

    def apply(self):

        self.ensure_public()
        text = self.render_smb_conf()
        if os.geteuid() == 0:
            self.write_conf()
            try:
                subprocess.run([PROVISION_HELPER, "reload"], capture_output=True, timeout=20)
            except Exception:
                pass
            return
        self.write_conf(os.path.join(DATA_DIR, "smb.conf.preview"))
        try:
            subprocess.run(["sudo", "-n", PROVISION_HELPER, "reload", SMB_CONF],
                           input=text.encode(), capture_output=True, timeout=20)
        except Exception:
            pass

def _selftest():
    import tempfile
    os.environ.setdefault("PN_MEDIASHARE_NO_DAEMONS", "1")
    d = tempfile.mkdtemp(prefix="mediashare-")
    global DATA_DIR
    mgr = ShareManager(root=os.path.join(d, "shares"), registry=os.path.join(d, "reg.json"))
    rec = mgr.ensure_share("cockpit-abc123", title="Testsitzung")
    assert rec["user"] == "bshare-cockpit-abc12"[:20] or rec["user"].startswith("bshare-"), rec
    assert rec["password"] and len(rec["password"]) >= 8, rec
    assert os.path.isdir(rec["path"]), rec
    conf = mgr.render_smb_conf()
    assert "[public]" in conf and rec["share"] in conf and "valid users = %s" % rec["user"] in conf, conf
    assert "read only = no" in conf

    p1 = mgr.get("cockpit-abc123")["password"]
    assert mgr.ensure_share("cockpit-abc123")["password"] == p1
    assert mgr.ensure_share("cockpit-abc123", rotate=True)["password"] != p1
    assert mgr.remove_share("cockpit-abc123", wipe=True)
    assert mgr.get("cockpit-abc123") is None

    r2 = mgr.ensure_share("cockpit-def456", title="SSO-Sitzung", principal="smbtest")
    assert r2.get("sso") and r2.get("owner_uid") == "smbtest", r2
    assert r2.get("password") is None and r2.get("user") is None, r2
    assert os.path.join("users", "smbtest", "sessions") in r2["path"], r2
    conf2 = mgr.render_smb_conf()
    assert "[smbtest]" in conf2 and "valid users = smbtest" in conf2, conf2
    assert "[session-cockpit-def456]" not in conf2, conf2

    assert _sso_uid("root") is None and _sso_uid("BadUID") is None and _sso_uid("") is None
    assert _sso_uid("smbtest") == "smbtest"

    mgr.ensure_share("cockpit-def456", title="SSO-Sitzung", principal="smbtest")
    mgr.ensure_share("cockpit-ghi789", title="Fremd", principal="othertest")
    mine = mgr.list_for("smbtest")
    assert {r["sid"] for r in mine} == {"cockpit-def456"}, mine
    assert mgr.list_for("othertest")[0]["sid"] == "cockpit-ghi789"
    assert len(mgr.list_for(None)) == 0
    assert len(mgr.list_for("smbtest", admin=True)) >= 2

    r3 = mgr.ensure_share("cockpit-arch01", title="Archivierbar", principal="smbtest")
    with open(os.path.join(r3["path"], "beweis.txt"), "w") as f:
        f.write("payload")
    arch = mgr.archive_share("cockpit-arch01")
    assert arch and arch.get("archived") and not os.path.isdir(r3["path"]), arch
    apath = mgr._load()["cockpit-arch01"]["archived_path"]
    assert os.path.isfile(os.path.join(apath, "beweis.txt")), apath
    assert "archiv" in apath
    assert all(r["sid"] != "cockpit-arch01" for r in mgr.list()), "archived leaks into list()"
    assert "cockpit-arch01" in {r["sid"] for r in mgr.list(include_archived=True)}

    un = mgr.unarchive_share("cockpit-arch01")
    assert un and not un.get("archived"), un
    assert os.path.isfile(os.path.join(un["path"], "beweis.txt")), un
    assert mgr.remove_share("cockpit-arch01", wipe=True)
    assert mgr.remove_share("cockpit-ghi789", wipe=True)
    assert mgr.remove_share("cockpit-def456", wipe=True)
    shutil.rmtree(d, ignore_errors=True)
    print("pn_mediashare selftest: ALL GREEN (smb_available=%s, root=%s)" % (smb_available(), SHARES_ROOT))

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    elif "--render" in sys.argv:
        print(ShareManager().render_smb_conf())
    else:
        print("usage: pn_mediashare.py --selftest | --render")
