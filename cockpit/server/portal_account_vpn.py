
import os, re, json, pwd

DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "brainbox-portal")
DEFAULT_PRINCIPAL = "owner"
HOME = os.path.expanduser("~")
_NETNS_ASKPASS = "/tmp/.pnvpn-portal-askpass.sh"

_netns_uid = None
_autonomy_permission_mode = None
_cockpit_disallowed = None
_cockpit_brief_file = None

def configure(**kw):

    g = globals()
    for k, v in kw.items():
        if v is not None:
            g[k] = v
    g["_USERVPN_FILE"] = os.path.join(g["DATA_DIR"], "user-vpn-grants.json")

_USERVPN_FILE = os.path.join(DATA_DIR, "user-vpn-grants.json")
_BOXUSER = os.environ.get("USER") or os.path.basename(os.path.expanduser("~")) or pwd.getpwuid(os.getuid()).pw_name

def _uservpn_grants():
    try:
        with open(_USERVPN_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _uservpn_allowed(uid, vpn):
    if not vpn:
        return False
    if str(uid) == DEFAULT_PRINCIPAL:
        return True
    return vpn in (_uservpn_grants().get(str(uid)) or [])

def _uservpn_set(uid, vpn, on):
    d = _uservpn_grants()
    cur = set(d.get(str(uid)) or [])
    if on:
        cur.add(vpn)
    else:
        cur.discard(vpn)
    d[str(uid)] = sorted(cur)
    try:
        tmp = _USERVPN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, _USERVPN_FILE)
    except Exception:
        pass
    return d.get(str(uid))

def _vpn_is_shared(vpn):

    if str(os.environ.get("PN_VPN_SHARED", "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        path = os.environ.get("PN_VPN_REGISTRY") or os.path.join(HOME, ".config", "pn-vpn", "registry.json")
        reg = json.load(open(path))
        for e in (reg if isinstance(reg, list) else []):
            if e.get("id") == vpn:
                return bool(e.get("shared"))
    except Exception:
        pass
    return False

def _account_netns_name(principal, vpn):

    tag = re.sub(r"[^a-zA-Z0-9]", "", str(vpn))[:16] or "vpn"
    sfx = "acct" if _vpn_is_shared(vpn) else "default"
    return "pnv-%s-%s-%s" % (tag, _netns_uid(principal), sfx)

def _netns_exists(ns):
    try:
        return bool(ns) and (os.path.exists("/run/netns/" + ns) or os.path.exists("/var/run/netns/" + ns))
    except Exception:
        return False

def _ensure_netns_askpass():
    try:
        if not os.path.exists(_NETNS_ASKPASS):
            with open(_NETNS_ASKPASS, "w") as f:
                f.write("#!/bin/bash\n%s/.local/bin/phantom secret get sudo_pass\n" % HOME)
            os.chmod(_NETNS_ASKPASS, 0o755)
    except Exception:
        pass

_AGENT_FAILED_TEXT = (
    "[Der Agent konnte nicht starten. Diese Box gibt statt eines Agenten NIE eine Kommandozeile aus.\n"
    " Naechster Schritt: die Sitzung in ihrer eigenen Zelle oeffnen (Reiter „Sessions“).\n"
    " Bleibt es dabei, ist die Box nicht angemeldet oder der Agent defekt - beides steht im Portal-Log.]")

HOST_PERMISSION_MODES_ALLOWED = ()

def _write_launch_script(path, wd, flags, netns="", task=None, fail_closed=False, env=None, note=""):

    import shlex as _sh
    claude = os.path.join(HOME, ".local", "bin", "claude")
    flagstr = " ".join(_sh.quote(f) for f in flags)
    if task is not None:
        run_line = "timeout 1800 %s -p %s %s" % (_sh.quote(claude), _sh.quote(task), flagstr)
    else:
        run_line = "%s %s" % (_sh.quote(claude), flagstr)
    L = ["#!/bin/bash",
         "export HOME=%s" % _sh.quote(HOME),
         'export PATH=%s/.local/bin:"$PATH"' % HOME,
         'export XDG_RUNTIME_DIR=/run/user/$(id -u)']
    for _k, _v in sorted((env or {}).items()):
        L.append("export %s=%s" % (_k, _sh.quote(str(_v))))
    L.append("cd %s 2>/dev/null || cd \"$HOME\"" % _sh.quote(wd))
    if netns:
        _ensure_netns_askpass()
        L += ['if [ "$1" != "__GO__" ]; then',
              '  if ip netns list 2>/dev/null | grep -qw %s; then' % _sh.quote(netns),
              '    export SUDO_ASKPASS=%s' % _sh.quote(_NETNS_ASKPASS),
              '    exec sudo -A ip netns exec %s sudo -u %s --preserve-env=HOME,PATH,XDG_RUNTIME_DIR bash "$0" __GO__'
              % (_sh.quote(netns), _sh.quote(_BOXUSER))]
        if fail_closed:
            L += ['  else',
                  '    echo "[VPN] Account-Tunnel %s nicht aktiv - Abbruch (fail-closed)" >&2; exit 3' % netns,
                  '  fi',
                  'fi']
        else:
            L += ['  fi',
                  'fi']

    if note:
        L.append("printf '%%s\\n' %s" % _sh.quote(str(note)))
    if task is not None:
        L.append("exec %s" % run_line)
    else:

        L += [run_line,
              "rc=$?",
              '[ "$rc" = 0 ] && exit 0',
              "printf '%%s\\n' %s >&2" % _sh.quote(_AGENT_FAILED_TEXT),
              'exit "$rc"']
    try:
        open(path, "w").write("\n".join(L) + "\n")
        os.chmod(path, 0o755)
        return path
    except Exception:
        return None

def _equip_flags(uid, key, prov, headless=False):

    flags = []
    m = str(prov.get("model") or "").strip()

    try:
        from portal_session_svc import sess_models
        known = {str(e.get("id") or "").strip() for e in sess_models()}
    except Exception:
        known = {"opus", "sonnet", "haiku", "fable"}
    if m in known or m.startswith("claude-"):
        flags += ["--model", m]
    ef = str(prov.get("effort") or "").strip()
    if ef in ("low", "medium", "high", "xhigh", "max"):
        flags += ["--effort", ef]
    if not headless:

        pm = _autonomy_permission_mode(prov.get("autonomy", 1))
        if pm and pm in HOST_PERMISSION_MODES_ALLOWED:
            flags += ["--permission-mode", pm]
    dis = _cockpit_disallowed(uid, key)
    if dis:
        flags += ["--disallowedTools"] + dis
    bp = _cockpit_brief_file(uid, key, prov)
    if bp:
        flags += ["--append-system-prompt-file", bp]
    return flags
