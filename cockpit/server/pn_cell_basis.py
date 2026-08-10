#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import threading
import time

try:
    import pn_session_cells as _sc
except Exception:
    _sc = None

try:
    import pn_ram_admission as _ADMIT
except Exception:
    _ADMIT = None

PN_VMM_HOME = os.environ.get("PN_VMM_HOME", os.path.expanduser("~/brainarbeit/os/pn-vmm"))
BIN = os.environ.get("PN_VMM_BIN", os.path.join(PN_VMM_HOME, "target", "release", "pn-vmm"))

KERNEL = os.environ.get("PN_VMM_CELL_KERNEL", os.path.join(PN_VMM_HOME, "kernel", "vmlinux-rng.bin"))
INITRD = os.path.join(PN_VMM_HOME, "kernel", "initramfs-cell.cpio")
BASE = os.path.join(PN_VMM_HOME, "kernel", "base-owner-session.img")

KALT_REPL_WARTEN = float(os.environ.get("PN_CELL_COLD_REPL_WAIT", "90"))
KALT_STILL_HART = float(os.environ.get("PN_CELL_COLD_DRAIN", "45"))

OFFICE_BASE = os.environ.get("PN_CELL_OFFICE_IMG", os.path.join(PN_VMM_HOME, "kernel", "base-office.img"))
OFFICE_MEM_MB = int(os.environ.get("PN_CELL_OFFICE_MEM_MB", "4096"))
OFFICE_VCPUS = int(os.environ.get("PN_CELL_OFFICE_VCPUS", "3"))
WORK_GB = int(os.environ.get("PN_CELL_WORK_GB", "8"))
BROKER = os.path.join(PN_VMM_HOME, "pn_cell_http_broker.py")
PORTAL_BROKER = os.path.join(PN_VMM_HOME, "pn_cell_portal_broker.py")
NET_BROKER = os.path.join(PN_VMM_HOME, "pn_cell_net_broker.py")

BROKER_AS_ADAPTER = os.environ.get("PN_BROKER_AS_ADAPTER", "").strip().lower() in ("1", "true", "yes", "on")
BROKER_ADAPTER_USER = os.environ.get("PN_BROKER_ADAPTER_USER", "adapter")

def _maybe_adapter(argv, env):

    if not BROKER_AS_ADAPTER:
        return argv
    keep = ",".join(sorted(k for k in (env or {}) if k.startswith("PN_")))
    pre = ["sudo", "-n", "-u", BROKER_ADAPTER_USER]
    if keep:
        pre.append("--preserve-env=" + keep)
    return pre + list(argv)

def _prepare_broker_rundir(d):

    if not BROKER_AS_ADAPTER:
        return
    try:
        import grp
        gid = grp.getgrnam("pnbroker").gr_gid
        os.chown(d, -1, gid)
        os.chmod(d, 0o2770)
    except Exception:
        pass

def _adapter_reap(run_dir):

    if not BROKER_AS_ADAPTER:
        return
    try:
        subprocess.run(["sudo", "-n", "-u", BROKER_ADAPTER_USER, "/usr/bin/python3",
                        BROKER_REAP, run_dir],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass

BROKER_REAP = os.path.join(PN_VMM_HOME, "pn_cell_broker_reap.py")
ACTD = os.path.join(PN_VMM_HOME, "pn-actd.py")
DESK_BRIDGE = os.path.join(PN_VMM_HOME, "pn_cell_desk_bridge.py")
PORTALCTL_SRC = os.environ.get("PN_PORTALCTL_SRC", os.path.expanduser("~/.local/bin/portalctl"))
CELLFS_SRC = os.environ.get("PN_CELLFS_SRC", os.path.expanduser("~/.local/bin/cellfs"))
EXCHANGE_SRC = os.environ.get("PN_EXCHANGE_SRC", os.path.expanduser("~/.local/bin/cell-exchange-sync"))
TERM_INCELL_SRC = os.environ.get("PN_TERM_INCELL_SRC", os.path.join(PN_VMM_HOME, "pn_term_incell.py"))
SONOS_SRC = os.environ.get("PN_SONOS_SRC", os.path.expanduser("~/.local/bin/sonos"))
PNJOB_SRC = os.environ.get("PN_PNJOB_SRC", os.path.expanduser("~/.local/bin/pnjob"))

BIOMNI_RT_DIR = os.environ.get("PN_BIOMNI_RT_DIR",
                               os.path.expanduser("~/.local/share/brainarbeit/runtimes/biomni/current"))
BIOMNI_RT_IMG = os.path.join(BIOMNI_RT_DIR, "runtime.img")
BIOMNI_ENTRY_SRC = os.path.join(BIOMNI_RT_DIR, "biomni_entry.py")
BIOMNI_LAKE_IMG = os.environ.get("PN_BIOMNI_LAKE_IMG",
                                 os.path.expanduser("~/.local/share/brainarbeit/datasources/biomni-e1/lake.img"))

CODEX_RT_DIR = os.environ.get("PN_CODEX_RT_DIR",
                              os.path.expanduser("~/.local/share/brainarbeit/runtimes/codex/current"))
CODEX_RT_IMG = os.path.join(CODEX_RT_DIR, "runtime.img")
CODEX_BIN_GUEST = "/work/codex/bin/codex"
CODEX_PATH_DIR_GUEST = "/work/codex/codex-path"
CODEX_CA_GUEST = "/work/codex/ca-certificates.crt"

AGENTS_RT_DIR = os.environ.get("PN_AGENTS_RT_DIR",
                               os.path.expanduser("~/.local/share/brainarbeit/runtimes/agents/current"))
AGENTS_RT_IMG = os.path.join(AGENTS_RT_DIR, "runtime.img")
AGENTS_NODE_GUEST = "/work/agents/node/bin/node"
AGENTS_GEMINI_GUEST = "/work/agents/gemini/gemini.js"
AGENTS_OPENCODE_GUEST = "/work/agents/opencode/opencode"
AGENTS_LIB_GUEST = "/work/agents/lib"
AGENTS_CA_GUEST = "/work/agents/ca-certificates.crt"

RUN_DIR = os.environ.get("PN_CELL_RUN_DIR", "/tmp/pn-cells")
VOL_DIR = os.environ.get("PN_CELL_VOL_DIR",
                         os.path.expanduser("~/.local/share/brainbox-portal/session-cells/session-vols"))
MEM_MB = os.environ.get("PN_CELL_MEM_MB", "1536")
IDLE_STOP_S = 45 * 60

CLOCK_SYNC_S = float(os.environ.get("PN_CELL_CLOCK_SYNC_S", 1800))
CLOCK_DRIFT_MAX_S = float(os.environ.get("PN_CELL_CLOCK_DRIFT_MAX_S", 5))
BOOT_TRIES = 3

TERM_RELAUNCH_MAX = int(os.environ.get("PN_TERM_RELAUNCH_MAX", "3"))
TERM_RELAUNCH_WINDOW_S = int(os.environ.get("PN_TERM_RELAUNCH_WINDOW_S", "120"))
TERM_START_WAIT_S = int(os.environ.get("PN_TERM_START_WAIT_S", "12"))
SEAT_WAIT_S = 40
READY_WAIT_S = 30

REMOTE_READOPT_WAIT_S = float(os.environ.get("PN_CELL_REMOTE_READOPT_S", "60"))

ADOPT_WAIT_S = 5

READOPT_ON = os.environ.get("PN_CELL_READOPT", "1") not in ("0", "", "false", "no", "off")

def cells_enabled():

    v = os.environ.get("CELLS_ENABLED")
    if v is None:
        try:
            for ln in open("/etc/brainbox/caps.env"):
                ln = ln.strip()
                if ln.startswith("CELLS_ENABLED="):
                    v = ln.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'"); break
        except Exception:
            v = None
    if v is None:
        return True
    return str(v).strip().lower() not in ("0", "false", "no", "off")
VOICE_MAX_TURNS = int(os.environ.get("PN_VOICE_MAX_TURNS", "6"))

def preflight():

    if not cells_enabled():
        return ("Zellen sind auf dieser Box deaktiviert (CELLS_ENABLED=0 in /etc/brainbox/caps.env). "
                "Ohne microVM wird KEINE Session gestartet — eine nackte Shell waere kein Sandkasten.")
    if not os.path.exists("/dev/kvm"):
        return ("Kein KVM: /dev/kvm fehlt. Entweder ist Virtualisierung (VT-x/AMD-V) im BIOS aus, oder das "
                "Kernel-Modul kvm_intel/kvm_amd ist nicht geladen.")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        grp = "kvm"
        try:
            import grp as _grp
            grp = _grp.getgrgid(os.stat("/dev/kvm").st_gid).gr_name
        except Exception:
            pass
        return ("Kein Zugriff auf /dev/kvm: der Portal-Benutzer '%s' ist nicht in der Gruppe '%s'."
                % (_whoami(), grp))
    for path, what in ((BIN, "Das pn-vmm-Binary"), (KERNEL, "Das Gast-Kernel-Image"),
                       (INITRD, "Das Initramfs der Zelle"), (BASE, "Das Basis-Image der Zelle")):
        if not os.path.exists(path):
            return "%s fehlt: %s" % (what, path)
    if not os.access(BIN, os.X_OK):
        return "Das pn-vmm-Binary ist nicht ausfuehrbar: %s" % BIN
    for d, what in ((VOL_DIR, "Delta-Verzeichnis"), (RUN_DIR, "Laufzeit-Verzeichnis")):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            return "%s kann nicht angelegt werden (%s): %s" % (what, e.strerror or e, d)
        if not os.access(d, os.W_OK):
            return "%s ist nicht beschreibbar: %s" % (what, d)
    return None

LLMPOOL_CFG = os.environ.get("PN_LLMPOOL_CFG",
                             os.path.expanduser("~/.config/brainbox-portal/llmpool.json"))
LLMPOOL_STATE = os.environ.get("PN_LLMPOOL_STATE",
                               os.path.expanduser("~/.local/share/brainbox-portal/llmpool_state.json"))

def llm_lane_reason():

    try:
        import llmpool as _lp
        snap = _lp.LLMPool(LLMPOOL_CFG, LLMPOOL_STATE, os.path.expanduser("~")).snapshot()
        if not snap.get("degraded"):
            return None
        return snap.get("status_de") or "Kein Claude-Konto verbunden: bitte im Portal ein Konto anmelden."
    except Exception:
        pass
    for h in (os.path.expanduser("~"),):
        try:
            if os.path.exists(os.path.join(h, ".claude", ".credentials.json")):
                return None
        except OSError:
            pass
    return "Kein Claude-Konto verbunden: bitte im Portal ein Konto anmelden."

def _whoami():
    try:
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return str(os.geteuid())

def _stream_text(body):

    deltas = []
    msgs = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "stream_event":
            e = ev.get("event") or {}
            if e.get("type") == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    deltas.append(d["text"])
        elif t == "assistant":
            for blk in (ev.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                    msgs.append(blk["text"])
    return "".join(deltas) if deltas else "\n".join(msgs)

def _split_sentences(text, at_end=False, min_len=14):

    sents = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in ".!?…\n":
            seg = text[start:i + 1].strip()
            after_ws = (c == "\n") or (i + 1 < n and text[i + 1].isspace())
            if after_ws and len(seg) >= min_len:
                sents.append(seg)
                start = i + 1
        i += 1
    rem = text[start:].strip()
    return sents, rem

_WEB_TOOLS = ("WebSearch", "WebFetch")

_VOICE_META_ARTIFACTS = {
    "no response requested", "no response needed", "no response required",
    "continue from where you left off", "(no response)", "acknowledged",
}

_INJECTED_USER_MARKERS = (
    "<command-name>", "<command-message>", "<local-command-stdout>", "<system-reminder>",
    "[auto-aufsicht]", "caveat: the messages below", "<user-prompt-submit-hook>",
    "<environment_context>", "<recommended_plugins>", "this session is being continued",
)

def _is_injected_user_text(t):

    low = (t or "").strip().lower()
    if not low:
        return True
    return any(low.startswith(m) or m in low[:400] for m in _INJECTED_USER_MARKERS)

def _render_prompt_tool(name, inp):

    if not isinstance(inp, dict):
        return None
    nm = str(name or "")
    if nm == "AskUserQuestion":
        blocks = []
        for q in (inp.get("questions") or []):
            if not isinstance(q, dict):
                continue
            head = str(q.get("question") or "").strip()
            if not head:
                continue
            lines = ["**%s**" % head]
            for i, opt in enumerate(q.get("options") or [], 1):
                if isinstance(opt, dict):
                    lab = str(opt.get("label") or "").strip()
                    desc = str(opt.get("description") or "").strip()
                    lines.append("%d. %s%s" % (i, lab, (" — " + desc) if desc else ""))
                else:
                    lines.append("%d. %s" % (i, str(opt)))
            lines.append("_Antworte einfach mit der Zahl oder dem Text der Option._")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) if blocks else None
    if nm == "ExitPlanMode":
        plan = str(inp.get("plan") or "").strip()
        if not plan:
            return None
        return "**Plan zur Freigabe**\n\n%s\n\n_Passt das so? Antworte „ja“ oder sag, was anders soll._" % plan
    return None

def _speakable(t):

    t = re.sub(r"\*{1,3}|_{1,3}|`{1,3}|^#{1,6}\s*|>\s?", "", t or "", flags=re.M)
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t).strip()

def _cli_disallowed(dt):

    return [t for t in (dt or []) if t not in _WEB_TOOLS]

def _cell_name(principal, session):
    if _sc is not None:
        return _sc.cell_name(principal, session)
    import hashlib
    h = hashlib.sha256(("%s/%s" % (principal, session)).encode()).hexdigest()[:12]
    return "sc-" + h

def _read_proc_stat(pid):

    try:
        with open("/proc/%d/stat" % int(pid), "rb") as f:
            data = f.read()
    except (OSError, ValueError):
        return None
    r = data.rfind(b")")
    if r < 0:
        return None
    rest = data[r + 2:].split()
    if len(rest) < 20:
        return None
    return (rest[0].decode("ascii", "replace"), rest[19].decode("ascii", "replace"))

class _AdoptedProc:

    __slots__ = ("pid", "_start", "returncode")

    def __init__(self, pid, starttime):
        self.pid = int(pid)
        self._start = str(starttime)
        self.returncode = None

    def _same(self):
        st = _read_proc_stat(self.pid)
        return st is not None and st[0] != "Z" and st[1] == self._start

    def poll(self):
        if self.returncode is None and not self._same():
            self.returncode = -1
        return self.returncode

    def wait(self, timeout=None):
        t0 = time.time()
        while self._same():
            if timeout is not None and time.time() - t0 >= timeout:
                raise subprocess.TimeoutExpired("pn-vmm", timeout)
            time.sleep(0.1)
        self.returncode = -1
        return self.returncode

    def _signal(self, sig):
        if self._same():
            try: os.kill(self.pid, sig)
            except OSError: pass

    def kill(self): self._signal(signal.SIGKILL)
    def terminate(self): self._signal(signal.SIGTERM)
    def send_signal(self, sig): self._signal(sig)

_SHA_CACHE = {}
_SHA_CACHE_LOCK = threading.Lock()

def _file_sha256(path):

    try:
        st = os.stat(path)
    except OSError:
        return ""
    key = path
    sig = (st.st_mtime, st.st_size)
    with _SHA_CACHE_LOCK:
        c = _SHA_CACHE.get(key)
        if c and c[0] == sig[0] and c[1] == sig[1]:
            return c[2]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    hexd = h.hexdigest()
    with _SHA_CACHE_LOCK:
        _SHA_CACHE[key] = (sig[0], sig[1], hexd)
    return hexd

def _sock_lebt(pfad):

    if not pfad or not os.path.exists(pfad):
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(2.0)
        s.connect(pfad)
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass
