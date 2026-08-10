#!/usr/bin/env python3

import base64, os, re, sys, time

sys.path.insert(0, os.path.expanduser("~/brainarbeit/cockpit/server"))

BLOG = "/tmp/pn-net-broker-flatproof.log"
os.environ["PN_NET_BROKER_LOG"] = BLOG
try:
    os.unlink(BLOG)
except OSError:
    pass

import pn_cell_session as cs

PRIN, SID, CID = "flatproof", "t1", 219
NET_HOSTS = ["flathub.org"]

APP = "org.vim.Vim"
APP_MARK = "VIM - Vi IMproved"
INSTALL_BUDGET_S = 1500

ok = True

def ck(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, extra), flush=True)

def host_avail_mb():
    try:
        for l in open("/proc/meminfo"):
            if l.startswith("MemAvailable"):
                return int(l.split()[1]) // 1024
    except OSError:
        pass
    return -1

policy = {"desktop": True, "net_general": "deny", "net_internal": "deny", "net_hosts": NET_HOSTS}
cell = cs.CellSession(PRIN, SID, CID, policy=policy)

print("== boot (office profile; governed net: deny-by-default + net_hosts=%s) ==" % NET_HOSTS, flush=True)
t0 = time.time()
booted = cell.boot()
ck("office cell boots", booted, "(%.1fs)" % (time.time() - t0))
if not booted:
    print("boot_denied:", cell._boot_denied)
    sys.exit(1)
ck("net broker alive", cell.net_broker is not None and cell.net_broker.poll() is None)
ck("work vol created", os.path.exists(cell.work))

print("== desktop_stage (mount /work vdc + gui-up) ==", flush=True)
t0 = time.time()
reason = cell.desktop_stage()
ck("desktop_stage", reason is None, "(%.1fs) %s" % (time.time() - t0, reason or ""))

print("== governed-lane env in seat ==", flush=True)
okc, out = cell._run("echo PXY=$http_proxy SPXY=$https_proxy FUD=$FLATPAK_USER_DIR; echo __E1__", "__E1__", 15)
ck("http(s)_proxy exported", okc and "PXY=http://127.0.0.1:8888" in out
   and "SPXY=http://127.0.0.1:8888" in out, (out or "").strip()[:160])
ck("FLATPAK_USER_DIR=/work/flatpak", "FUD=/work/flatpak" in (out or ""))

okc, out = cell._run("busybox ls -ld /var/tmp 2>&1 | busybox head -1; busybox mkdir -p /var/tmp && "
                     "echo VT_OK; echo __VT__", "__VT__", 15)
ck("/var/tmp present for flatpak cache", okc and "VT_OK" in (out or ""), (out or "").strip()[:100])

okc, out = cell._run("export FLATPAK_SYSTEM_DIR=/work/flatpak; echo FSD=$FLATPAK_SYSTEM_DIR; echo __FS__",
                     "__FS__", 15)
ck("FLATPAK_SYSTEM_DIR=/work/flatpak exported", okc and "FSD=/work/flatpak" in (out or ""))

print("== stage flow-control-FIXED net-lane proxy (repo incell_mux_proxy.py) ==", flush=True)
_psrc = open(os.path.expanduser("~/brainarbeit/os/pn-vmm/incell_mux_proxy.py"), "rb").read()
okc, out = cell._run("printf %%s '%s' | base64 -d > /tmp/incell_mux_proxy_fix.py && echo STAGED; echo __SG__"
                     % base64.b64encode(_psrc).decode(), "__SG__", 30)
ck("fixed proxy staged (%d B)" % len(_psrc), okc and "STAGED" in (out or ""))
okc, out = cell._run("[ -f /tmp/mux.pid ] && kill $(busybox cat /tmp/mux.pid) 2>/dev/null; "
                     "busybox fuser -k 8888/tcp 2>/dev/null; busybox sleep 1; "
                     "PN_PROXY_TRANSPORT=vsock:2:9200 PN_PROXY_PORT=8888 /bin/python3 "
                     "/tmp/incell_mux_proxy_fix.py >/tmp/nproxy2.out 2>&1 & echo $! > /tmp/mux.pid; "
                     "busybox sleep 2; busybox cat /tmp/nproxy2.out; echo __RS__", "__RS__", 25)
ck("net-lane proxy restarted (fixed)", okc and "PN_INCELL_PROXY_READY" in (out or ""),
   (out or "").strip()[:120])

print("== GATE 1: remote-add + metadata sync over the lane ==", flush=True)
t0 = time.time()
okc, out = cell._run("flatpak remote-add --system --if-not-exists flathub "
                     "https://dl.flathub.org/repo/flathub.flatpakrepo 2>&1 && echo RADD_OK; echo __R1__",
                     "__R1__", 180)
radd_t = time.time() - t0
ck("remote-add over lane", okc and "RADD_OK" in out, "(%.1fs) %s" % (radd_t, (out or "").strip()[-200:]))
t0 = time.time()
okc, out = cell._run("flatpak remote-ls --system flathub 2>&1 | busybox head -8; echo __R2__", "__R2__", 300)
rls_t = time.time() - t0
rls = (out or "").strip()
ck("remote-ls (metadata sync)", okc and len(rls.splitlines()) >= 3 and "error" not in rls.lower(),
   "(%.1fs)" % rls_t)
print("\n".join("    | " + l for l in rls.splitlines()[:8]), flush=True)

print("== GATE 2: flatpak install --system %s (runtime + app) ==" % APP, flush=True)
okc, out = cell._run("busybox rm -f /tmp/fp.log; "
                     "(flatpak install --system -y --noninteractive flathub %s >/tmp/fp.log 2>&1; "
                     "echo INSTALL_RC=$? >>/tmp/fp.log) & echo __GO__" % APP, "__GO__", 20)
ck("install launched (bg, log=/tmp/fp.log)", okc)
t0 = time.time()
rc = None
while time.time() - t0 < INSTALL_BUDGET_S:
    okc, out = cell._run("busybox tail -c 400 /tmp/fp.log 2>/dev/null; echo; "
                         "busybox du -s /work/flatpak 2>/dev/null; echo __PL__", "__PL__", 45)
    lines = [l for l in (out or "").replace("\r", "\n").splitlines() if l.strip()]
    du = lines[-1].split()[0] if lines else "?"
    prog = lines[-2][-110:] if len(lines) > 1 else ""
    print("  [%4ds] /work/flatpak=%sK host_avail=%dMB | %s" % (time.time() - t0, du, host_avail_mb(), prog),
          flush=True)
    hit = [l for l in lines if "INSTALL_RC=" in l]
    if hit:
        try:
            rc = int(hit[-1].split("INSTALL_RC=")[1].split()[0])
        except ValueError:
            rc = -1
        break
    time.sleep(15)
install_t = time.time() - t0
ck("install completes rc=0", rc == 0, "(%.0fs, rc=%r)" % (install_t, rc))
okc, out = cell._run("busybox tail -c 900 /tmp/fp.log; echo __FL__", "__FL__", 20)
print("\n".join("    | " + l for l in (out or "").replace("\r", "\n").splitlines()[-10:] if l.strip()),
      flush=True)

okc, out = cell._run("flatpak list --system 2>&1; echo __L1__", "__L1__", 60)
ck("flatpak list shows app+runtime", okc and "Vim" in (out or "") and "Platform" in (out or ""))
print("\n".join("    | " + l for l in (out or "").strip().splitlines()[:6]), flush=True)
okc, out = cell._run("busybox du -sh /work/flatpak 2>/dev/null; echo __DU__", "__DU__", 60)
duh = (out or "").strip()
ck("install landed on /work/flatpak (vdc)", okc and bool(duh), duh[:80])

print("== GATE 3: flatpak run %s (CLI app -> --version) on DISPLAY :7 ==" % APP, flush=True)
okc, out = cell._run("DISPLAY=:7 flatpak run %s --version 2>&1 | busybox head -2; "
                     "echo RUN_RC=$?; echo __RUN__" % APP, "__RUN__", 180)
ck("app runs, prints output", okc and APP_MARK in (out or ""), (out or "").strip()[:200])

print("== GATE 4: full stop + re-boot -> install survives on /work ==", flush=True)
cell._teardown(reboot=False)
time.sleep(2)
ck("work vol survives teardown", os.path.exists(cell.work))
t0 = time.time()
booted = cell.boot()
ck("cell re-boots", booted, "(%.1fs)" % (time.time() - t0))
if booted:
    okc, out = cell._run("busybox mkdir -p /work; busybox mount /dev/vdc /work 2>/dev/null; "
                         "busybox grep -q ' /work ' /proc/mounts && echo WORK_OK; "
                         "export FLATPAK_SYSTEM_DIR=/work/flatpak; echo __W2__", "__W2__", 30)
    ck("/work remounts after reboot", okc and "WORK_OK" in (out or ""))
    okc, out = cell._run("flatpak list --system 2>&1; echo __L2__", "__L2__", 90)
    ck("install SURVIVES reboot (flatpak list)", okc and "Vim" in (out or ""))
    print("\n".join("    | " + l for l in (out or "").strip().splitlines()[:6]), flush=True)
    okc, out = cell._run("flatpak run %s --version 2>&1 | busybox head -1; echo __RN2__" % APP,
                         "__RN2__", 180)
    ck("app still runs post-reboot (headless)", okc and APP_MARK in (out or ""),
       (out or "").strip()[:120])

print("== teardown + erase (disposable proof cell) ==", flush=True)
cell._teardown(reboot=False)
time.sleep(1)
cell._erase_state()
ck("erase removes work vol (Not-Aus)", not os.path.exists(cell.work))
ck("erase removes delta", not os.path.exists(cell.delta))

print("== governed-lane evidence (broker log %s) ==" % BLOG, flush=True)
counts = {}
try:
    for l in open(BLOG):
        m = (re.search(r"CONNECT ([^ :]+):\d+ -> (ALLOW|DENY)", l)
             or re.search(r"HTTP \S+ https?://([^/:]+)\S* -> (ALLOW|DENY)", l))
        if m:
            k = (m.group(1), m.group(2))
            counts[k] = counts.get(k, 0) + 1
except OSError:
    pass
for (h, v), n in sorted(counts.items()):
    print("    %-5s %-28s x%d" % (v, h, n), flush=True)
denied = sorted({h for (h, v) in counts if v == "DENY"})
if denied:
    print("    NOTE: DENIED hosts during proof (candidates for net_hosts): %s" % ", ".join(denied), flush=True)
def _listed(host):
    host = host.lower().strip(".")
    return any(host == e or host.endswith("." + e) for e in (x.lower().strip(".") for x in NET_HOSTS))

ck("all ALLOWed egress stayed inside net_hosts (suffix rule)",
   bool(counts) and all(_listed(h) for (h, v) in counts if v == "ALLOW"))

print("\nFLATPAK-NET-PROOF:", "ALL GREEN" if ok else "FAILURES", flush=True)
sys.exit(0 if ok else 1)
