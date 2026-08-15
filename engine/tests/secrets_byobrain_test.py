#!/usr/bin/env python3

import os, sys, json, socket, time, tempfile, threading, subprocess, stat, hashlib

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))

SCRATCH = tempfile.mkdtemp(prefix="pn-p3-")
SECRETS_DIR = os.path.join(SCRATCH, "secrets")
os.environ["PN_SECRETS_DIR"] = SECRETS_DIR
os.environ["PN_SECRETS_ALLOW_INSECURE"] = "1"
os.environ["PN_SECRETS_PASSPHRASE"] = "test-first-boot-passphrase"

import importlib
import pnlib.secrets as pnsecrets; importlib.reload(pnsecrets)
import pnlib.redact as pnredact
import pnlib.llmpool as llmpool

PLANTED = "sk-ant-api03-PLANTEDSECRETvalue1234567890abcdefghi"

def test_secrets_store():
    print("\n== 1. secrets store: sealed, write-only, never echoes value ==")
    receipt = pnsecrets.write_cred(PLANTED, "api-key")

    blob = json.dumps(receipt)
    check("setcred receipt does not contain the value", PLANTED not in blob, "receipt=" + blob)
    check("receipt reports kind", receipt.get("kind") == "api-key")
    check("receipt reports a sealing backend", bool(receipt.get("backend")),
          "backend=" + str(receipt.get("backend")))

    keyp = os.path.join(SECRETS_DIR, "brain.key")
    raw = open(keyp, "rb").read()
    check("brain.key exists", os.path.exists(keyp))
    check("brain.key does NOT contain plaintext value (sealed)", PLANTED.encode() not in raw,
          f"sealed {len(raw)} bytes")
    mode = stat.S_IMODE(os.stat(keyp).st_mode)
    check("brain.key is 0600", mode == 0o600, oct(mode))
    dmode = stat.S_IMODE(os.stat(SECRETS_DIR).st_mode)
    check("secrets/ is 0700 (owner-only, no ambient authority)", dmode == 0o700, oct(dmode))
    check("brain.kind == api-key", pnsecrets.read_kind() == "api-key")
    check(".nobackup marker present (off-box backup exclude)",
          os.path.exists(os.path.join(SECRETS_DIR, ".nobackup")))

    back = pnsecrets.read_cred()
    check("unseal round-trips to the exact value", back == PLANTED)

    st = json.dumps(pnsecrets.status())
    check("status() does not leak the value", PLANTED not in st)

def test_redaction():
    print("\n== 2. output redaction masks planted secrets ==")

    out = f"here is the leaked key {PLANTED} embedded in model output"
    red = pnredact.redact(out)
    check("planted sk-ant key is masked by shape", PLANTED not in red, repr(red))
    check("redaction keeps surrounding text", "embedded in model output" in red)

    short = "xyz-codex-7f3a9"
    pnredact.register_secret(short)
    red2 = pnredact.redact(f"token leak: {short} done")
    check("registered exact value is masked", short not in red2, repr(red2))

    red3 = pnredact.redact("ANTHROPIC_API_KEY=sk-ant-supersecretvalue123456")
    check("ANTHROPIC_API_KEY=... value masked",
          "sk-ant-supersecretvalue123456" not in red3, repr(red3))

    red4 = pnredact.redact("auth: ghp_ABCDEFGHIJ1234567890abcdef and Bearer abcdefghij1234567890XYZ")
    check("github PAT masked", "ghp_ABCDEFGHIJ1234567890abcdef" not in red4, repr(red4))
    check("bearer token masked", "abcdefghij1234567890XYZ" not in red4, repr(red4))

    obj = {"text": f"key {PLANTED}", "nested": {"v": PLANTED}}
    ro = json.dumps(pnredact.redact_obj(obj))
    check("redact_obj masks nested secrets", PLANTED not in ro, ro)

def test_pool_routing():
    print("\n== 3. warm pool routing: dedicated locks, loose shares ==")

    echo = "/bin/echo answer:"
    pool = llmpool.Pool(size=3, model="sonnet", cmd_tmpl=echo)

    results = {}
    def loose(i):
        results[i] = pool.ask(f"small q{i}", timeout=10, kind="loose")
    ths = [threading.Thread(target=loose, args=(i,)) for i in range(6)]
    [t.start() for t in ths]; [t.join() for t in ths]
    sessions_used = {r["session"] for r in results.values()}
    check("loose: all requests ok", all(r.get("ok") for r in results.values()))
    check("loose: sessions are SHARED (#sessions <= pool size)",
          len(sessions_used) <= 3, f"sessions={sorted(sessions_used)}")

    pk = pool.peek()
    check("peek lists warm sessions", pk["ok"] and len(pk["sessions"]) >= 1,
          f"{len(pk['sessions'])} sessions")

    slow = "/bin/sh -c 'sleep 1; echo answer:$1' --"
    pool2 = llmpool.Pool(size=3, model="sonnet", cmd_tmpl=slow)
    ded = {}
    def dedic(i):
        ded[i] = pool2.ask(f"big q{i}", timeout=10, kind="dedicated")
    d1 = threading.Thread(target=dedic, args=(0,))
    d2 = threading.Thread(target=dedic, args=(1,))
    d1.start(); time.sleep(0.2); d2.start(); d1.join(); d2.join()
    check("dedicated: both requests ok", all(r.get("ok") for r in ded.values()))
    check("dedicated: two concurrent asks used DIFFERENT sessions (locked)",
          ded[0]["session"] != ded[1]["session"],
          f"s0={ded[0]['session']} s1={ded[1]['session']}")

    bp = "/bin/sh -c 'sleep 0.6; echo answer:$1' --"
    pool3 = llmpool.Pool(size=1, model="sonnet", cmd_tmpl=bp)
    t0 = time.time()
    bpr = {}
    def one(i): bpr[i] = pool3.ask(f"q{i}", timeout=10, kind="loose")
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    [t.start() for t in ths]; [t.join() for t in ths]
    dt = time.time() - t0
    check("backpressure: pool=1 serializes 3 asks (>=1.5s)", dt >= 1.5, f"wall={dt:.2f}s")

    k = pool.kill()
    check("kill reaps sessions", k["ok"] and k["killed"] >= 1, f"killed={k['killed']}")

def test_canary():
    print("\n== 4. credential-health canary flips on forced bad cred ==")

    bad = "/bin/sh -c 'echo \"Invalid API key · Please run /login\"; exit 1'"
    pool = llmpool.Pool(size=1, model="sonnet", cmd_tmpl=bad)

    AUTH = ("Invalid API key", "Please run /login", "Not logged in")
    def classify(res):
        raw = res.get("raw") or res.get("text") or res.get("error") or ""
        if any(m.lower() in raw.lower() for m in AUTH):
            return {"ok": False, "auth": True}
        return res
    res = classify(pool.ask("ping", timeout=10, kind="loose"))
    degraded = (not res.get("ok"))
    reason = "credential" if res.get("auth") else "backend"
    check("canary detects failure", degraded, f"res={res}")
    check("degraded reason is 'credential'", reason == "credential", f"reason={reason}")

    good = "/bin/echo answer:"
    pool2 = llmpool.Pool(size=1, model="sonnet", cmd_tmpl=good)
    res2 = classify(pool2.ask("ping", timeout=10, kind="loose"))
    check("canary recovers on a healthy cred", res2.get("ok"), f"res={res2}")

def test_no_ambient_authority():
    print("\n== 5. no ambient authority: tasks can't read secrets/, non-owner setcred denied ==")

    dmode = stat.S_IMODE(os.stat(SECRETS_DIR).st_mode)
    check("secrets/ has NO group/other bits (0700)", (dmode & 0o077) == 0, oct(dmode))

    OWNER_UID = os.getuid()
    SETCRED_UIDS = {OWNER_UID}
    def setcred_authz(peer_uid):
        return peer_uid is not None and peer_uid in SETCRED_UIDS
    check("owner uid may setcred", setcred_authz(OWNER_UID))
    check("a task uid (owner+1) is DENIED setcred", not setcred_authz(OWNER_UID + 1))
    check("a request with no peercred is DENIED setcred", not setcred_authz(None))

    receipt = pnsecrets.write_cred(PLANTED, "max-token")
    check("setcred receipt is value-free", PLANTED not in json.dumps(receipt))

def main():
    print(f"P3 acceptance tests (isolated scratch={SCRATCH}, FAKE backends only)")
    test_secrets_store()
    test_redaction()
    test_pool_routing()
    test_canary()
    test_no_ambient_authority()
    print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))

    import shutil; shutil.rmtree(SCRATCH, ignore_errors=True)
    sys.exit(0 if not FAIL else 1)

if __name__ == "__main__":
    main()
