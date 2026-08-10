
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "secretframe"))
import zkchannel, zkrelease
from zkrelease import ReleaseRegistry, FULFILLED, CONSUMED, DENIED

SECRET = b"sk-SECRET-VALUE-\xf0\x9f\x94\x90"

def _seal(box_pub, val):
    return zkchannel.seal_to(box_pub, val)

def test_full_release_lifecycle():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "openai_key")
    p = R.pending("alice")
    assert len(p) == 1 and p[0]["req_id"] == rid and p[0]["box_pub"] == box_pub
    assert R.fulfill("alice", rid, _seal(box_pub, SECRET)) is True
    assert R.status("alice", rid) == FULFILLED
    got = R.consume("alice", rid, lambda mv: bytes(mv))
    assert got == SECRET, got

    assert R.consume("alice", rid, lambda mv: bytes(mv)) is None
    assert R.pending("alice") == []

def test_tenant_isolation():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "k")

    assert R.pending("bob") == []
    assert R.fulfill("bob", rid, _seal(box_pub, SECRET)) is False
    R.fulfill("alice", rid, _seal(box_pub, SECRET))
    assert R.consume("bob", rid, lambda mv: bytes(mv)) is None
    assert R.consume("alice", rid, lambda mv: bytes(mv)) == SECRET

def test_tampered_seal_refused():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "k")
    sealed = _seal(box_pub, SECRET)
    import base64
    ctb = bytearray(base64.b64decode(sealed["ct"])); ctb[0] ^= 0xFF
    sealed["ct"] = base64.b64encode(bytes(ctb)).decode()
    assert R.fulfill("alice", rid, sealed) is False
    assert R.status("alice", rid) == "pending"

def test_deny_path():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "bank_login")
    assert R.deny("alice", rid, "user declined") is True
    assert R.status("alice", rid) == DENIED
    assert R.pending("alice") == []
    assert R.consume("alice", rid, lambda mv: bytes(mv)) is None

def test_expiry_sweeps_pending():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "k")
    R._reqs[rid].created = time.time() - (zkrelease.REQUEST_TTL + 5)
    assert R.pending("alice") == []
    assert R.status("alice", rid) in (None, "expired")

def test_consume_before_fulfill_is_none():
    R = ReleaseRegistry()
    rid, box_pub = R.request("alice", "k")
    assert R.consume("alice", rid, lambda mv: bytes(mv)) is None

def _main():
    tests = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = failed = 0
    for t in tests:
        try:
            t(); print("PASS " + t.__name__); passed += 1
        except Exception as e:
            print("FAIL " + t.__name__ + ": " + type(e).__name__ + ": " + str(e)); failed += 1
    print("\n%d/%d passed" % (passed, passed + failed))
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_main())
