#!/usr/bin/env python3

import os, sys, json, socket, time, tempfile, subprocess, signal

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))

SCRATCH = tempfile.mkdtemp(prefix="pn-p3-e2e-")
SOCK = os.path.join(SCRATCH, "pn-llmd.sock")
SECRETS_DIR = os.path.join(SCRATCH, "secrets")
PLANTED = "sk-ant-api03-E2ELEAKEDSECRET0987654321zyxwvut"

FAKE = os.path.join(SCRATCH, "fake_backend.sh")
with open(FAKE, "w") as f:
    f.write("#!/bin/sh\n"
            'echo "answer to: $* (debug token ' + PLANTED + ')"\n')
os.chmod(FAKE, 0o755)

def req(d, timeout=10):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps(d) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c: break
        buf += c
    s.close()
    return json.loads(buf.split(b"\n", 1)[0])

def main():
    env = dict(os.environ)
    env.update({
        "PN_LLM_SOCK": SOCK,
        "PN_SECRETS_DIR": SECRETS_DIR,
        "PN_SECRETS_ALLOW_INSECURE": "1",
        "PN_SECRETS_PASSPHRASE": "e2e-passphrase",
        "PN_LLM_CMD": f"{FAKE} {{model}}",
        "PN_LLM_POOL": "2",
        "PN_LLM_CANARY_INTERVAL": "3",
    })
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "tools", "pn-llmd")],
                            env=env, stderr=subprocess.PIPE, text=True)
    try:

        for _ in range(50):
            if os.path.exists(SOCK):
                break
            time.sleep(0.1)
        print("\n== e2e: pn-llmd over temp socket, FAKE backend ==")
        st = req({"verb": "status"})
        check("daemon responds to status", st.get("ok"), str(st)[:120])

        sc = req({"verb": "setcred", "value": PLANTED, "brain_kind": "api-key"})
        check("setcred ok", sc.get("ok"), str(sc))
        check("setcred response does NOT contain the value", PLANTED not in json.dumps(sc), str(sc))
        check("setcred returns a sealing receipt", bool(sc.get("sealed_with")), str(sc))

        keyp = os.path.join(SECRETS_DIR, "brain.key")
        raw = open(keyp, "rb").read() if os.path.exists(keyp) else b""
        check("brain.key sealed on disk (no plaintext)", PLANTED.encode() not in raw,
              f"{len(raw)} bytes")

        a = req({"verb": "ask", "prompt": "hello", "kind": "loose"})
        check("ask ok", a.get("ok"), str(a)[:160])
        check("ask output REDACTS the leaked secret", PLANTED not in json.dumps(a),
              "text=" + repr(a.get("text", ""))[:160])
        check("ask still returns the non-secret answer", "answer to" in (a.get("text") or ""),
              repr(a.get("text", ""))[:120])

        ad = req({"verb": "ask", "prompt": "big", "kind": "dedicated"})
        check("dedicated ask ok + has session id", ad.get("ok") and ad.get("session"),
              f"session={ad.get('session')}")

        pk = req({"verb": "peek"})
        check("peek lists sessions", pk.get("ok") and "sessions" in pk, str(pk)[:120])

        tok = req({"verb": "issue-shell-token"})
        check("issue-shell-token ok", tok.get("ok") and tok.get("token"), str(tok)[:80])
        rd1 = req({"verb": "redeem-shell-token", "token": tok.get("token")})
        check("redeem yields owner principal", rd1.get("ok") and rd1.get("principal") == "owner",
              str(rd1))
        rd2 = req({"verb": "redeem-shell-token", "token": tok.get("token")})
        check("token is ONE-TIME (second redeem fails)", not rd2.get("ok"), str(rd2))

        time.sleep(4)
        h = req({"verb": "health"})
        check("health reports credential present", h["credential"].get("present"), str(h)[:160])
        check("health not degraded with a working fake backend",
              not h["health"].get("degraded"),
              f"degraded={h['health'].get('degraded')} reason={h['health'].get('reason')}")

        print(f"\n==== e2e: {len(PASS)} passed, {len(FAIL)} failed ====")
        if FAIL:
            print("FAILED:", ", ".join(FAIL))

    finally:
        try:
            proc.send_signal(signal.SIGTERM); proc.wait(timeout=5)
        except Exception:
            proc.kill()
        import shutil; shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(0 if not FAIL else 1)

if __name__ == "__main__":
    main()
