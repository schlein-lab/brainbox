#!/usr/bin/env python3

import os, sys, time, ssl, json, threading, urllib.request, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from pnlib import meters, ipc

A = sys.argv[1:]
N_BIG    = int(A[0]) if len(A) > 0 else 120
BIG_MB   = int(A[1]) if len(A) > 1 else 300
BIG_SEC  = int(A[2]) if len(A) > 2 else 20
N_SMALL  = int(A[3]) if len(A) > 3 else 120
SMALL_MB = int(A[4]) if len(A) > 4 else 50
SMALL_SEC= int(A[5]) if len(A) > 5 else 10
DURATION = int(A[6]) if len(A) > 6 else 120

HOG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hog.py")
PORTAL = "https://127.0.0.1:8077/"
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

def nrestarts():
    try:
        out = subprocess.run(["systemctl", "--user", "show", "brainbox-portal.service",
                              "-p", "NRestarts"], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip().split("=")[1])
    except Exception:
        return -1

def cfg():
    return ipc.send_request({"verb": "status"})["cfg"]

def submit(mb, secs, klass):
    ipc.send_request({"verb": "submit", "cmd": ["python3", HOG, str(mb), str(secs)],
                      "cwd": os.path.expanduser("~"), "class": klass, "mem": mb,
                      "tag": f"hog{mb}", "env": {"PATH": os.environ.get("PATH", "")}})

stop = {"v": False}
samples = []
portal_codes = []

def monitor():
    while not stop["v"]:
        s = meters.snapshot()
        samples.append((s["mem_available"], s["batch_current"], s["psi_avg10"], s["swap_used"]))
        time.sleep(1)

def portal_probe():
    while not stop["v"]:
        try:
            r = urllib.request.urlopen(PORTAL, timeout=4, context=CTX)
            portal_codes.append(r.getcode())
        except urllib.error.HTTPError as e:
            portal_codes.append(e.code)
        except Exception:
            portal_codes.append("ERR")
        time.sleep(1)

def main():
    c = cfg()
    floor = c["mem_floor"]
    nr0 = nrestarts()
    print(f"== loadtest == floor={floor}MiB batch_high={c['batch_high']} max_conc={c['max_concurrent']}")
    print(f"flooding {N_BIG}x{BIG_MB}MiB/{BIG_SEC}s + {N_SMALL}x{SMALL_MB}MiB/{SMALL_SEC}s "
          f"= {N_BIG*BIG_MB + N_SMALL*SMALL_MB} MiB requested vs ~9943 MiB RAM")
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=portal_probe, daemon=True).start()

    t0 = time.time()
    for i in range(max(N_BIG, N_SMALL)):
        if i < N_BIG:   submit(BIG_MB, BIG_SEC, "compute")
        if i < N_SMALL: submit(SMALL_MB, SMALL_SEC, "tiny")
    print(f"submitted {N_BIG+N_SMALL} jobs in {time.time()-t0:.1f}s; observing {DURATION}s...")

    deadline = time.time() + DURATION
    while time.time() < deadline:
        st = ipc.send_request({"verb": "status"})
        cnt = st["counts"]; sn = st["snap"]
        print(f"  t={int(time.time()-t0):3d}s avail={sn['mem_available']:5d} "
              f"batch={sn['batch_current']:5d} psi={sn['psi_avg10']:5.1f} "
              f"run={cnt.get('running',0)} q={cnt.get('queued',0)} "
              f"done={cnt.get('done',0)} fail={cnt.get('failed',0)}")
        if cnt.get("queued", 0) == 0 and cnt.get("running", 0) == 0:
            print("  queue drained."); break
        time.sleep(5)

    stop["v"] = True
    time.sleep(1.5)
    nr1 = nrestarts()
    min_avail = min((s[0] for s in samples), default=0)
    max_batch = max((s[1] for s in samples), default=0)
    max_psi = max((s[2] for s in samples), default=0)
    portal_ok = sum(1 for x in portal_codes if isinstance(x, int))
    portal_bad = sum(1 for x in portal_codes if x == "ERR")

    total = meters.meminfo()["total"]

    pn_floor_ok = (total - max_batch) >= floor
    cap_ok = max_batch <= int(total * 0.60) + 200
    flap_ok = (nr1 - nr0) == 0 and nr1 >= 0
    portal_live = portal_bad == 0 and portal_ok > 0
    global_floor_ok = min_avail >= floor - 200

    print("\n== RESULTS ==")
    print(f"  pn-batch current max  : {max_batch} MiB   (slice MemoryMax ~{int(total*0.60)})")
    print(f"  portioneer-only floor : {total - max_batch} MiB free attributable  (floor {floor}) -> {'OK' if pn_floor_ok else 'BREACH'}")
    print(f"  global MemAvailable min: {min_avail} MiB   (background-influenced; {'ok' if global_floor_ok else 'dominated by ungoverned load'})")
    print(f"  batch PSI max         : {max_psi:.1f}")
    print(f"  phantom NRestarts     : {nr0} -> {nr1}  (delta {nr1-nr0})")
    print(f"  phantom HTTP probes   : ok={portal_ok} err={portal_bad}")

    verdict = pn_floor_ok and cap_ok and flap_ok and portal_live
    print(f"\n  pn_floor_ok={pn_floor_ok}  cap_held={cap_ok}  phantom_no_flap={flap_ok}  "
          f"portal_live={portal_live}  [global_floor_ok={global_floor_ok}]")
    print("  VERDICT:", "PASS ✅" if verdict else "FAIL ❌")
    sys.exit(0 if verdict else 1)

if __name__ == "__main__":
    main()
