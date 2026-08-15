#!/usr/bin/env python3

import os, shutil, subprocess

D = os.path.dirname(os.path.realpath(__file__)); os.chdir(D)
OWNER = "kernel/_ownerbase"; OT = "kernel/_ownertask"; IMG = "kernel/base-owner-task.img"; SIZE = "640M"
PROXY = "incell_mux_proxy.py"

INIT = r'''#!/bin/busybox sh
export PATH=/bin:/sbin
export LANG=de_DE.UTF-8
export LC_ALL=de_DE.UTF-8
busybox mkdir -p /proc /sys /dev /tmp /work
busybox mount -t proc none /proc 2>/dev/null
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
echo "PN_CELL_OWNERTASK_ALIVE uid=$(busybox id -u)"
echo -n "PN_HOME="; [ -n "$(busybox ls -A /home 2>/dev/null)" ] && echo HOST_HOME_PRESENT_BAD || echo ABSENT_GOOD
busybox mount -t ext4 -o nosuid,nodev /dev/vdc /work 2>/dev/null && echo "PN_WORK_MOUNTED" || echo "PN_WORK_MOUNT_FAIL"
# caged model access: in-cell mux proxy over vsock 9100
PN_PROXY_TRANSPORT=vsock:2:9100 PN_PROXY_PORT=8088 /bin/python3 /opt/pn/incell_mux_proxy.py >/tmp/proxy.out 2>&1 &
busybox sleep 2; busybox cat /tmp/proxy.out
export ANTHROPIC_BASE_URL=http://127.0.0.1:8088
export ANTHROPIC_AUTH_TOKEN=placeholder-not-real
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_AUTOUPDATER=1
export DISABLE_TELEMETRY=1
echo "PN_TASK_BEGIN"
TASK=$(busybox cat /work/in/task.txt 2>/dev/null)
RESULT=$(/bin/claude -p "You are working inside an isolated cell. Read this granted input and reply with exactly ONE short sentence summarizing it. Input: $TASK" </dev/null 2>/tmp/claude.err)
echo "PN_TASK_RESULT=$RESULT"
# PROPOSE the result as a governed effect (the ONLY egress) — nothing leaves without host confirm
printf '%s\n' "$RESULT" > /work/outbox/summary.txt
/bin/python3 -c 'import json; json.dump({"kind":"artifact","target_name":"summary.txt","summary":"caged agent summary of the granted input","body":"summary.txt"}, open("/work/outbox/summary.effect.json","w"))'
echo "PN_TASK_PROPOSED"
busybox sync
echo "PN_CELL_WORK_DONE"
busybox reboot -f
'''

def main():
    assert os.path.isdir(OWNER), "need kernel/_ownerbase -> run build_cell_base_owner.py first"
    if os.path.exists(OT):
        shutil.rmtree(OT)
    shutil.copytree(OWNER, OT, symlinks=True)
    os.makedirs(f"{OT}/opt/pn", exist_ok=True)
    shutil.copy(PROXY, f"{OT}/opt/pn/incell_mux_proxy.py")
    with open(f"{OT}/sbin/init", "w") as f:
        f.write(INIT)
    os.chmod(f"{OT}/sbin/init", 0o755)
    subprocess.run(["truncate", "-s", SIZE, IMG], check=True)
    subprocess.run(["mke2fs", "-t", "ext4", "-F", "-q", "-d", OT, IMG], check=True)
    print("PN_OWNERTASK_IMAGE_BUILT", IMG, SIZE)

if __name__ == "__main__":
    main()
