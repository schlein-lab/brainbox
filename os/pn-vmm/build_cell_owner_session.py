#!/usr/bin/env python3

import os, shutil, subprocess, json
import platform, sysconfig

MULTIARCH = sysconfig.get_config_var("MULTIARCH") or "%s-linux-gnu" % platform.machine()

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
OWNER = "kernel/_ownerbase"; SESS = "kernel/_ownersession"

IMG = os.environ.get("PN_OWNER_IMG", "kernel/base-owner-session.img"); SIZE = "700M"
PROXY = "incell_mux_proxy.py"
SHIM = "pn_claude_shim.py"
BANNER = "pn_repl_banner.py"
REPL_LAUNCH = "pn_repl_launch.sh"
GATE_SRC = "pn-gate.c"
MCP_SHIM = "pn_cell_mcp.py"

PHANTOM_MCP_JSON = json.dumps({"mcpServers": {"phantom": {
    "type": "stdio", "command": "/bin/python3", "args": ["/opt/pn/pn_cell_mcp.py"],
    "env": {"PN_MCP_TRANSPORT": "vsock:2:9400"}}}}, indent=1)

def _build_pn_gate(out_path):

    cc = shutil.which("musl-gcc") or "musl-gcc"
    cmd = [cc, "-static", "-O2", "-pthread",
           "-idirafter", "/usr/include", "-idirafter", "/usr/include/%s" % MULTIARCH,
           "-o", out_path, GATE_SRC]
    subprocess.run(cmd, check=True)
    os.chmod(out_path, 0o755)

INIT = r'''#!/bin/busybox sh
export PATH=/bin:/sbin
export LANG=de_DE.UTF-8
export LC_ALL=de_DE.UTF-8
busybox mkdir -p /proc /sys /dev /tmp /work /root
busybox mount -t proc none /proc 2>/dev/null
busybox mount -t sysfs none /sys 2>/dev/null
busybox mount -t devtmpfs none /dev 2>/dev/null
busybox mount -t tmpfs none /tmp 2>/dev/null
busybox --install -s /bin 2>/dev/null
busybox ip link set lo up 2>/dev/null || busybox ifconfig lo 127.0.0.1 up 2>/dev/null
export PYTHONHOME=/usr
export PYTHONPATH=/site:/opt/pn
export LD_LIBRARY_PATH=/lib:/lib64
export GCONV_PATH=/lib/gconv
export HOME=/root
export CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1
if [ -f /seed ]; then /bin/python3 -c 'import os,fcntl,struct
d=open("/seed","rb").read()
if d:
 fd=os.open("/dev/random",os.O_WRONLY); fcntl.ioctl(fd,0x40085203,struct.pack("ii",len(d)*8,len(d))+d)' 2>/dev/null; busybox rm -f /seed; fi
echo "PN_CELL_SESSION_ALIVE uid=$(busybox id -u)"
echo -n "PN_HOME="; [ -e @PN_HOST_HOME@ ] && echo HOST_HOME_PRESENT_BAD || echo ABSENT_GOOD
# optional per-session work volume (vdc); the persistent overlay delta (vdb) already carries /root/.claude
busybox mount -t ext4 -o nosuid,nodev /dev/vdc /work 2>/dev/null && echo "PN_WORK_MOUNTED" || echo "PN_WORK_NONE"
# caged model access: in-cell mux proxy over vsock 9100 (the ONLY egress)
PN_PROXY_TRANSPORT=vsock:2:9100 PN_PROXY_PORT=8088 /bin/python3 /opt/pn/incell_mux_proxy.py >/tmp/proxy.out 2>&1 &
busybox sleep 2; busybox cat /tmp/proxy.out
export ANTHROPIC_BASE_URL=http://127.0.0.1:8088
export ANTHROPIC_AUTH_TOKEN=placeholder-not-real
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_AUTOUPDATER=1
export DISABLE_TELEMETRY=1
export DISABLE_ERROR_REPORTING=1
export DISABLE_BUG_COMMAND=1
export CI=1
echo "PN_CELL_SESSION_READY"
# hand a PERSISTENT shell to the host portal over vsock 1234 (multi-turn seat); fall back to serial sh
if [ -x /bin/vsock-seat ]; then
  /bin/vsock-seat
  echo "PN_CELL_SEAT_ENDED"
fi
exec busybox sh
'''

def main():
    assert os.path.isdir(OWNER), "need kernel/_ownerbase -> run build_cell_base_owner.py first"
    if os.path.exists(SESS):
        shutil.rmtree(SESS)
    shutil.copytree(OWNER, SESS, symlinks=True)
    os.makedirs(f"{SESS}/opt/pn", exist_ok=True)
    shutil.copy(PROXY, f"{SESS}/opt/pn/incell_mux_proxy.py")
    shutil.copy(SHIM, f"{SESS}/opt/pn/pn_claude_shim.py")
    shutil.copy(BANNER, f"{SESS}/opt/pn/pn_repl_banner.py")
    shutil.copy(REPL_LAUNCH, f"{SESS}/opt/pn/pn_repl_launch.sh")
    os.chmod(f"{SESS}/opt/pn/pn_repl_launch.sh", 0o755)

    _build_pn_gate(f"{SESS}/opt/pn/pn-gate")

    shutil.copy(MCP_SHIM, f"{SESS}/opt/pn/pn_cell_mcp.py")
    os.makedirs(f"{SESS}/etc/pn", exist_ok=True)
    with open(f"{SESS}/etc/pn/phantom.mcp.json", "w") as f:
        f.write(PHANTOM_MCP_JSON + "\n")

    if os.path.isdir("cell_terminfo"):
        dst = f"{SESS}/usr/share/terminfo"
        for sub in os.listdir("cell_terminfo"):
            s = os.path.join("cell_terminfo", sub)
            if os.path.isdir(s):
                os.makedirs(os.path.join(dst, sub), exist_ok=True)
                for name in os.listdir(s):
                    shutil.copy(os.path.join(s, name), os.path.join(dst, sub, name))
    with open(f"{SESS}/sbin/init", "w") as f:

        f.write(INIT.replace("@PN_HOST_HOME@", os.path.expanduser("~")))
    os.chmod(f"{SESS}/sbin/init", 0o755)
    subprocess.run(["truncate", "-s", SIZE, IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", SESS, IMG], check=True)
    sz = subprocess.run(["du", "-sh", SESS], capture_output=True, text=True).stdout.split()[0]
    print("session staging size:", sz)
    print("PN_SESSION_IMAGE_BUILT", IMG, SIZE)

if __name__ == "__main__":
    main()
