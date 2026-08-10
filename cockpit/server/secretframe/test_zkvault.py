
import os, sys, tempfile, base64, hashlib, importlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zkvault
from zkvault import ZeroKnowledgeVault, ConflictError, TooBigError, VaultError

def _fresh():
    d = tempfile.mkdtemp(prefix="zkv-")
    return ZeroKnowledgeVault(d), d

def test_empty_is_none():
    v, _ = _fresh()
    assert v.get("alice") is None
    assert v.head("alice") is None

def test_first_put_and_roundtrip():
    v, _ = _fresh()
    blob = b"\x00\x01\x02 opaque-ciphertext \xff\xfe not-json"
    r = v.put("alice", blob, base_version=0)
    assert r["version"] == 1, r
    got = v.get("alice")
    assert got["version"] == 1
    assert base64.b64decode(got["blob_b64"]) == blob, "byte-exact round-trip"
    assert got["sha256"] == hashlib.sha256(blob).hexdigest()

    v2, _ = _fresh()
    assert v2.put("bob", blob, base_version=None)["version"] == 1

def test_optimistic_concurrency():
    v, _ = _fresh()
    v.put("alice", b"v1", base_version=0)
    v.put("alice", b"v2", base_version=1)
    assert v.head("alice")["version"] == 2

    try:
        v.put("alice", b"stale", base_version=1)
        assert False, "expected ConflictError on stale base_version"
    except ConflictError:
        pass

    try:
        v.put("alice", b"clobber", base_version=0)
        assert False, "expected ConflictError on base_version 0 over existing"
    except ConflictError:
        pass
    assert base64.b64decode(v.get("alice")["blob_b64"]) == b"v2"

def test_size_limit():
    v, _ = _fresh()
    orig = zkvault.MAX_BLOB_BYTES
    try:
        zkvault.MAX_BLOB_BYTES = 16
        try:
            v.put("alice", b"x" * 17, base_version=0)
            assert False, "expected TooBigError"
        except TooBigError:
            pass
        assert v.put("alice", b"x" * 16, base_version=0)["version"] == 1
    finally:
        zkvault.MAX_BLOB_BYTES = orig

def test_tenant_isolation_and_traversal():
    v, _ = _fresh()
    v.put("alice", b"A-secret", base_version=0)
    v.put("bob", b"B-secret", base_version=0)
    assert base64.b64decode(v.get("alice")["blob_b64"]) == b"A-secret"
    assert base64.b64decode(v.get("bob")["blob_b64"]) == b"B-secret"

    v.put("../../etc/evil", b"nope", base_version=0)
    root = v._root
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            assert os.path.realpath(full).startswith(os.path.realpath(root) + os.sep), full

    try:
        v.put("", b"x", base_version=0)
        assert False, "expected VaultError on empty principal"
    except VaultError:
        pass

def test_versioning_gc_keeps_only_current():
    v, d = _fresh()
    v.put("alice", b"one", base_version=0)
    v.put("alice", b"two", base_version=1)
    v.put("alice", b"three", base_version=2)
    adir = v._dir("alice")
    blobs = sorted(f for f in os.listdir(adir) if f.startswith("blob."))
    assert blobs == ["blob.3.bin"], f"only current version kept, got {blobs}"
    assert base64.b64decode(v.get("alice")["blob_b64"]) == b"three"

def test_delete_versioned():
    v, _ = _fresh()
    v.put("alice", b"data", base_version=0)
    try:
        v.delete("alice", base_version=99)
        assert False, "expected ConflictError on wrong version delete"
    except ConflictError:
        pass
    assert v.delete("alice", base_version=1)["ok"] is True
    assert v.get("alice") is None

    assert v.delete("ghost", base_version=None)["ok"] is True

def test_box_is_blind_structural():

    import ast
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "zkvault.py")).read()
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
            for a in node.names:
                imported.add(a.name)

    banned = {"cryptography", "nacl", "Crypto", "Fernet", "HKDF", "AESGCM", "ChaCha20Poly1305", "scrypt"}
    leaked = banned & imported
    assert not leaked, f"zkvault must stay crypto-free / keyless — imports {leaked}"
    assert not hasattr(zkvault, "Fernet")
    assert not hasattr(ZeroKnowledgeVault, "decrypt")
    assert not hasattr(ZeroKnowledgeVault, "plaintext")

def _main():
    tests = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_main())
