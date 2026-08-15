
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "secretframe"))
import zklink
from zklink import LinkRelay, OPEN, OFFERED, DONE

NEWPUB = "BASE64_NEW_DEVICE_PUB=="
SEALED = {"epk": "e", "iv": "i", "ct": "c"}

def test_happy_path():
    R = LinkRelay()
    link_id, code = R.start("alice", NEWPUB)
    assert "-" in code and len(code) == 7
    res = R.resolve("alice", code)
    assert res["link_id"] == link_id and res["new_pub"] == NEWPUB
    assert R.offer("alice", link_id, SEALED) is True
    got = R.get("alice", link_id)
    assert got["new_pub"] == NEWPUB and got["offer"] == SEALED and got["state"] in (OFFERED, DONE)

def test_principal_isolation():
    R = LinkRelay()
    link_id, code = R.start("alice", NEWPUB)
    assert R.resolve("bob", code) is None
    assert R.offer("bob", link_id, SEALED) is False
    assert R.get("bob", link_id) is None

def test_code_is_case_insensitive():
    R = LinkRelay()
    _, code = R.start("alice", NEWPUB)
    assert R.resolve("alice", code.lower()) is not None

def test_offer_only_once_and_only_when_open():
    R = LinkRelay()
    link_id, code = R.start("alice", NEWPUB)
    assert R.offer("alice", link_id, SEALED) is True
    assert R.offer("alice", link_id, {"epk": "x", "iv": "y", "ct": "z"}) is False
    got = R.get("alice", link_id)
    assert got["offer"] == SEALED

def test_unknown_code_and_link():
    R = LinkRelay()
    assert R.resolve("alice", "ZZZ-ZZZ") is None
    assert R.get("alice", "nope") is None

def test_expiry():
    R = LinkRelay()
    link_id, code = R.start("alice", NEWPUB)
    R._links[link_id].created = time.time() - (zklink.LINK_TTL + 5)
    assert R.resolve("alice", code) is None
    assert R.get("alice", link_id) is None

def _main():
    tests = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    p = f = 0
    for t in tests:
        try:
            t(); print("PASS " + t.__name__); p += 1
        except Exception as e:
            print("FAIL " + t.__name__ + ": " + type(e).__name__ + ": " + str(e)); f += 1
    print("\n%d/%d passed" % (p, p + f))
    return 1 if f else 0

if __name__ == "__main__":
    sys.exit(_main())
