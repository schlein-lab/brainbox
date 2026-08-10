#!/usr/bin/env python3

import os, socket, subprocess, time, tempfile, sys, signal, shutil, struct, zlib, hashlib

PH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target/release/phantom")
rt = tempfile.mkdtemp(prefix="phxwr-")
env = dict(os.environ, XDG_RUNTIME_DIR=rt)
ctl = os.path.join(rt, "phantom.ctl")
procs = []
def spawn(args, **kw):
    kw.setdefault("env", env); p = subprocess.Popen(args, **kw); procs.append(p); return p
def send(line):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(ctl)
    s.sendall((line+"\n").encode()); s.shutdown(socket.SHUT_WR)
    d=b""
    while True:
        c=s.recv(4096)
        if not c: break
        d+=c
    s.close(); return d.decode(errors="replace")
fails=[]
def check(n,c,g): print(("PASS" if c else "FAIL"),n,"->",g); (fails.append(n) if not c else None)

def parse_list(txt):

    wins=[]
    for ln in txt.splitlines():
        if 'app_id="xwayland"' not in ln: continue
        cid = ln.split("cid=",1)[1].split()[0]
        title = ln.split('title="',1)[1].split('"')[0] if 'title="' in ln else ""
        ready = "ready=true" in ln
        wins.append({"cid":cid,"title":title,"ready":ready})
    return wins

def shot(cid_or_at):
    out=os.path.join(rt,"s_%s.png" % str(cid_or_at).replace("@",""))
    r=send(f"sense {cid_or_at} shot {out}")
    if not os.path.exists(out): return None,None,None
    d=open(out,"rb").read(); W,H=struct.unpack(">II", d[16:24])
    return W,H,hashlib.sha256(d).hexdigest()[:16]

try:
    if not shutil.which("Xwayland"): print("SKIP: no Xwayland"); sys.exit(0)
    apps=[a for a in ("xlogo","xcalc","xeyes","xclock") if shutil.which(a)]
    if len(apps)<2: print("SKIP: need two of xlogo/xcalc/xeyes/xclock"); sys.exit(0)

    spawn([PH,"--headless","phantom-0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if os.path.exists(ctl): break
        time.sleep(0.03)

    xwerr=open(os.path.join(rt,"xw.err"),"w+")
    xw=spawn([PH,"xwayland","--rootless","phantom-0"], stdout=subprocess.PIPE, stderr=xwerr, text=True)
    disp=None
    for _ in range(120):
        line=xw.stdout.readline()
        if line.startswith("DISPLAY="): disp=line.strip().split("=",1)[1]; break
        if xw.poll() is not None: break
    check("rootless Xwayland reported DISPLAY", bool(disp), disp)
    if not disp: sys.exit(1)
    time.sleep(0.6); xwerr.flush()
    wmlog=open(os.path.join(rt,"xw.err")).read()
    check("phantom-xwm became the WM", "managing X root" in wmlog, None)
    check("Composite manual-redirect engaged (per-window presentation)",
          "Composite manual-redirect" in wmlog, None)

    a,b = apps[0], apps[1]
    spawn([a], env=dict(env, DISPLAY=disp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    spawn([b], env=dict(env, DISPLAY=disp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4.0)

    wins = parse_list(send("list"))
    check(f"two per-window cids ({a}+{b})", len(wins)==2, [w["cid"]+":"+w["title"] for w in wins])
    check("both per-window targets ready", all(w["ready"] for w in wins), wins)

    dims={}
    for w in wins:
        W,H,_ = shot(w["cid"]); dims[w["cid"]]=(W,H)
        check(f"cid {w['cid']} ({w['title']}) snapshot {W}x{H}", bool(W) and W>0, (W,H))
    check("the two windows are distinct surfaces (different sizes)",
          len(set(dims.values()))==2, dims)

    check("titles are real WM_NAME (not 'X11 window N')",
          all(w["title"] and not w["title"].startswith("X11 window") for w in wins),
          [w["title"] for w in wins])

    big = max(wins, key=lambda w: dims[w["cid"]][0]*dims[w["cid"]][1])
    other = [w for w in wins if w["cid"]!=big["cid"]][0]

    Wb,Hb,_ = shot("@"+big["title"].split()[0])
    check(f"@{big['title'].split()[0]} resolves to its window {Wb}x{Hb}",
          (Wb,Hb)==dims[big["cid"]], (Wb,Hb))

    _,_,bb = shot(big["cid"]); _,_,ob = shot(other["cid"])
    for k in ("1","2","3","4","5"):
        send(f"act {big['cid']} type {k}"); time.sleep(0.15)
    time.sleep(0.7)
    _,_,ba = shot(big["cid"]); _,_,oa = shot(other["cid"])
    check(f"forged keys reached {big['title']} (its pixels changed)", bb!=ba, (bb,ba))
    check(f"NO leak to {other['title']} (its pixels unchanged)", ob==oa, (ob,oa))

    before = {w["cid"] for w in parse_list(send("list"))}
    killed = False
    for p in procs:
        try:
            if os.path.basename(p.args[0]) == a:
                p.send_signal(signal.SIGTERM); killed = True; break
        except Exception: pass
    time.sleep(1.5)
    after = {w["cid"] for w in parse_list(send("list"))}
    check("closing one X window drops exactly one per-window cid",
          killed and len(after) == len(before) - 1,
          {"before": sorted(before), "after": sorted(after)})

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
print("ALL ROOTLESS PER-WINDOW XWM TESTS PASSED")
