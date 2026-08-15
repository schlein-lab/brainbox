
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.expanduser("~/.local/bin"))

import portal_sessions as PS

DAY = 86400

def _store(uid="owner"):
    d = tempfile.mkdtemp(prefix="pssess-")
    return PS.SessionStore(d, uid, "cockpit")

def _raw(st, sid):

    for s in st._load():
        if s.get("id") == sid:
            return s
    return None

def _to_download(st, sid, now=0):

    st.apply_scan({sid: {"action": "enter_grace"}}, now=now)
    st.apply_scan({sid: {"action": "offer_download"}}, now=now)

def test_create_list_get_and_naming():
    st = _store()
    s = st.create("First chat", sid="abc123def456", now=1000)
    assert s["id"] == "abc123def456"
    assert s["tmux"].endswith("-cockpit-abc123def456")
    assert st.transcript_path(s["id"]).endswith("/transcripts/abc123def456.log")
    assert st.get("abc123def456")["title"] == "First chat"
    assert [x["id"] for x in st.list()] == ["abc123def456"]

def test_tmux_naming_is_injective_across_colliding_uids():

    names = set()
    for uid in ("owner", "Owner", "OWNER", "own.er", "own_er", "own-er"):
        st = PS.SessionStore(tempfile.mkdtemp(), uid, "cockpit")
        names.add(st.tmux_name("abc123def456"))
    assert len(names) == 6, f"tmux names collide across distinct uids: {names}"

def test_auto_resume_picks_most_recent():
    st = _store()
    st.create("old", sid="aaaaaa111111", now=1000)
    st.create("new", sid="bbbbbb222222", now=2000)
    st.touch("aaaaaa111111", now=3000)
    assert st.last_active()["id"] == "aaaaaa111111"
    st.touch("bbbbbb222222", now=4000)
    assert st.last_active()["id"] == "bbbbbb222222"

def test_touch_resets_expiry_state_but_not_deleted():
    st = _store()
    st.create("s", sid="cccccc333333", now=0)
    st.apply_scan({"cccccc333333": {"action": "enter_grace"}})
    assert st.get("cccccc333333")["state"] == PS.STATE_EXPIRING
    st.touch("cccccc333333", now=100)
    got = st.get("cccccc333333")
    assert got["state"] == PS.STATE_ACTIVE and got["last_active"] == 100

    _to_download(st, "cccccc333333", now=200)
    st.apply_scan({"cccccc333333": {"action": "delete"}}, now=300)
    assert st.get("cccccc333333") is None
    assert _raw(st, "cccccc333333")["state"] == PS.STATE_DELETED
    assert st.touch("cccccc333333", now=400) is None
    assert _raw(st, "cccccc333333")["state"] == PS.STATE_DELETED

def test_rename_and_bad_id():
    st = _store()
    st.create("s", sid="dddddd444444", now=0)
    assert st.rename("dddddd444444", "renamed")["title"] == "renamed"
    try:
        st.tmux_name("bad id!"); raise AssertionError("must reject bad id")
    except ValueError:
        pass

def test_retention_active_within_28d_untouched():
    st = _store()
    s = st.create("s", sid="eeeeee555555", now=0)
    assert PS.retention_scan([s], now=27 * DAY, has_client=False) == {}

def test_retention_client_present_hands_off_and_is_persisted():

    st = _store()
    s = st.create("s", sid="ffffff666666", now=0)
    acts = PS.retention_scan([s], now=29 * DAY, has_client=True)
    assert acts["ffffff666666"]["action"] == "copy_to_client"
    st.apply_scan(acts, now=29 * DAY)
    assert st.get("ffffff666666") is None
    assert _raw(st, "ffffff666666")["state"] == PS.STATE_DELETED
    assert st.list() == []

def test_retention_deadlines_anchored_to_apply_time_not_last_active():

    st = _store()
    st.create("s", sid="aaaaaa777777", now=0)

    T1 = 100 * DAY
    a1 = PS.retention_scan(st.list(), now=T1, has_client=False)["aaaaaa777777"]
    assert a1["action"] == "enter_grace"
    st.apply_scan({"aaaaaa777777": a1}, now=T1)
    assert st.get("aaaaaa777777")["grace_started_at"] == T1

    assert PS.retention_scan(st.list(), now=T1 + 3600, has_client=False) == {}

    T2 = T1 + PS.KEEP_GRACE_S + 10
    a2 = PS.retention_scan(st.list(), now=T2, has_client=False)["aaaaaa777777"]
    assert a2["action"] == "offer_download"
    st.apply_scan({"aaaaaa777777": a2}, now=T2)

    assert PS.retention_scan(st.list(), now=T2 + DAY, has_client=False) == {}

    a3 = PS.retention_scan(st.list(), now=T2 + PS.DOWNLOAD_WINDOW_S + 10, has_client=False)["aaaaaa777777"]
    assert a3["action"] == "delete"

def test_behalten_ticket_defers_deletion_and_apply_scan_respects_it():
    st = _store()
    st.create("keepme", sid="bbbbbb888888", now=0)
    st.mark_kept("bbbbbb888888", now=28 * DAY)
    s = st.get("bbbbbb888888")
    assert s["kept_until"] == 28 * DAY + PS.KEPT_EXTENSION_S
    assert PS.retention_scan([s], now=40 * DAY, has_client=False) == {}

    st.apply_scan({"bbbbbb888888": {"action": "delete"}}, now=40 * DAY)
    assert st.get("bbbbbb888888")["state"] == PS.STATE_ACTIVE

    acts2 = PS.retention_scan([st.get("bbbbbb888888")],
                              now=28 * DAY + PS.KEPT_EXTENSION_S + 10, has_client=False)
    assert acts2["bbbbbb888888"]["action"] == "enter_grace"

def test_mark_kept_never_resurrects_deleted():

    st = _store()
    st.create("gone", sid="cccccc999999", now=0)
    _to_download(st, "cccccc999999", now=0)
    st.apply_scan({"cccccc999999": {"action": "delete"}}, now=0)
    assert st.get("cccccc999999") is None
    assert _raw(st, "cccccc999999")["state"] == PS.STATE_DELETED
    assert st.mark_kept("cccccc999999", now=1) is None
    assert _raw(st, "cccccc999999")["state"] == PS.STATE_DELETED
    assert st.last_active() is None

def test_delete_skipped_when_resumed_out_of_download():

    st = _store()
    st.create("s", sid="dddddd000000", now=0)
    _to_download(st, "dddddd000000", now=0)
    assert _raw(st, "dddddd000000")["state"] == PS.STATE_DOWNLOAD
    st.touch("dddddd000000", now=10)
    st.apply_scan({"dddddd000000": {"action": "delete"}}, now=20)
    assert _raw(st, "dddddd000000")["state"] == PS.STATE_ACTIVE
    assert st.get("dddddd000000") is not None

def test_delete_purges_transcript_file():

    st = _store()
    st.create("s", sid="eeeeee000000", now=0)
    p = st.transcript_path("eeeeee000000")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("secret transcript")
    assert os.path.exists(p)
    _to_download(st, "eeeeee000000", now=0)
    st.apply_scan({"eeeeee000000": {"action": "delete"}}, now=0)
    assert not os.path.exists(p)

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  PASS  {name}"); passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}"); failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}"); failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
