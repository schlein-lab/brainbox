#!/usr/bin/env python3
import os, socket, subprocess, time, tempfile, sys, signal, struct, zlib, shutil

PH = os.environ.get("PHANTOM_BIN") or shutil.which("phantom") or os.path.expanduser("~/phantom/target/release/phantom")
rt = tempfile.mkdtemp(prefix="phxw-")
env = dict(os.environ, XDG_RUNTIME_DIR=rt)
ctl = os.path.join(rt, "phantom.ctl")
procs = []

def spawn(args, **kw):
    kw.setdefault("env", env)
    p = subprocess.Popen(args, **kw); procs.append(p); return p

def send(line):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(ctl)
    s.sendall((line+"\n").encode()); s.shutdown(socket.SHUT_WR)
    d=b""
    while True:
        c=s.recv(4096)
        if not c: break
        d+=c
    s.close(); return d.decode(errors="replace")

def png_nonblank(path):
    d=open(path,"rb").read()
    assert d[:8]==b"\x89PNG\r\n\x1a\n", "not a PNG"
    W,H=struct.unpack(">II", d[16:24])
    assert 100<=W<=4096 and 100<=H<=4096, f"insane {W}x{H}"
    idat=b""; i=8
    while i<len(d):
        ln=struct.unpack(">I",d[i:i+4])[0]; t=d[i+4:i+8]
        if t==b"IDAT": idat+=d[i+8:i+8+ln]
        if t==b"IEND": break
        i+=12+ln
    raw=zlib.decompress(idat) if idat else b""
    distinct=len(set(raw[:200000]))
    return W,H,distinct

fails=[]
def check(n,c,g): print(("PASS" if c else "FAIL"),n,"->",g); (fails.append(n) if not c else None)

try:
    if not shutil.which("Xwayland"):
        print("SKIP: Xwayland not installed"); sys.exit(0)

    spawn([PH,"--headless","phantom-0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists(ctl): break
        time.sleep(0.03)
    check("control socket up", os.path.exists(ctl), os.path.exists(ctl))

    xw = spawn([PH,"xwayland","phantom-0"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    disp=None
    for _ in range(120):
        line = xw.stdout.readline()
        if line.startswith("DISPLAY="):
            disp=line.strip().split("=",1)[1]; break
        if xw.poll() is not None: break
    check("Xwayland reported a DISPLAY", bool(disp), disp)
    if not disp:
        print("no DISPLAY; aborting"); sys.exit(1)

    xclient = shutil.which("xeyes") or shutil.which("xclock") or shutil.which("xterm")
    check("an X11 test app exists", bool(xclient), xclient)
    if xclient:
        spawn([xclient], env=dict(env, DISPLAY=disp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3.0)

    lst = send("list")
    print("list ->", lst.strip())
    check("Xwayland surface listed", "Xwayland" in lst or "xeyes" in lst.lower(), lst.strip()[:120])

    cid=None
    for tok in lst.split():
        if tok.startswith("cid="):
            cid=tok.split("=",1)[1]; break
    check("a cid resolved", bool(cid), cid)

    if cid:
        out = os.path.join(rt,"xw.png")
        r = send(f"snapshot {cid} {out}")
        print("snapshot ->", r.strip())
        if os.path.exists(out):
            W,H,distinct = png_nonblank(out)
            check(f"PNG is non-blank ({W}x{H}, {distinct} distinct bytes)", distinct>5, f"{W}x{H} d={distinct}")
        else:
            check("snapshot produced a PNG", False, r.strip())
finally:
    for p in reversed(procs):
        try: p.send_signal(signal.SIGTERM)
        except Exception: pass
    time.sleep(0.5)
    for p in reversed(procs):
        try: p.kill()
        except Exception: pass
    shutil.rmtree(rt, ignore_errors=True)

print()
if fails: print("FAILURES:", fails); sys.exit(1)
print("ALL XWAYLAND TESTS PASSED")
