#!/usr/bin/env python3

import os, sys, time, threading

HERE = os.path.dirname(os.path.realpath(__file__))
MEDIAD_PARENT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, MEDIAD_PARENT)

RELAY_ROOT = os.environ.get("RELAY_ROOT", os.path.expanduser(
    "~/brainarbeit-build/relay-hardened"))
if os.path.isdir(RELAY_ROOT):
    sys.path.insert(0, RELAY_ROOT)

from mediad import signaling as S
from mediad import session as MS
from mediad import backend as B
from mediad import carriage as C
from mediad import iceconfig
from mediad import framesrc

PASS = FAIL = 0

def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}")

def make_pair(allowed_box=None, allowed_dev=None):

    inbox_box, inbox_dev = [], []
    box = MS.MediaSession(role="answerer", principal="alice",
                          backend=B.LoopbackBackend("pending", "box"),
                          emit=lambda e: inbox_dev.append(e),
                          allowed_channels=allowed_box)
    dev = MS.MediaSession(role="offerer", principal="alice",
                          backend=B.LoopbackBackend("pending", "dev"),
                          emit=lambda e: inbox_box.append(e),
                          allowed_channels=allowed_dev)
    return box, dev, inbox_box, inbox_dev

def negotiate(box, dev, inbox_box, inbox_dev, channels):

    offer = dev.create_offer(channels)
    assert inbox_box and inbox_box[-1]["t"] == S.MEDIA_OFFER
    box.on_offer(inbox_box.pop())
    assert inbox_dev and inbox_dev[-1]["t"] == S.MEDIA_ANSWER
    dev.on_answer(inbox_dev.pop())

    box.backend.link(dev.backend)
    box.mark_connected(); dev.mark_connected()

    for e in list(inbox_box):
        if e["t"] == S.MEDIA_READY:
            box.on_ready(e)
    for e in list(inbox_dev):
        if e["t"] == S.MEDIA_READY:
            dev.on_ready(e)
    inbox_box.clear(); inbox_dev.clear()

def test_vocabulary():
    print("[a] signaling vocabulary + validation")
    sid = S.new_session_id()
    ok = S.offer(sid, "v=0", [S.channel(S.CH_VOICE)], [], role="device")
    check(S.validate(ok) is None, "a well-formed offer validates")
    check(S.validate({"t": "media.offer", "session_id": sid}) is not None, "offer missing sdp rejected")
    check(S.validate({"t": "bogus", "session_id": sid}) is not None, "unknown type rejected")
    check(S.mirror_direction(S.SENDONLY) == S.RECVONLY, "sendonly mirrors to recvonly")
    check(S.mirror_direction(S.SENDRECV) == S.SENDRECV, "sendrecv is self-mirror")
    try:
        S.channel("not-a-channel")
        check(False, "invalid channel raises")
    except ValueError:
        check(True, "invalid channel raises")

def test_negotiation():
    print("[b] full negotiation box-answerer <-> device-offerer reaches connected")
    box, dev, ib, idv = make_pair()
    chans = [S.channel(S.CH_VOICE, S.SENDRECV), S.channel(S.CH_SCREEN_BOX, S.SENDONLY)]
    negotiate(box, dev, ib, idv, chans)
    check(dev.state == MS.ST_CONNECTED and box.state == MS.ST_CONNECTED, "both ends connected")
    check(box.is_live() and dev.is_live(), "both ends report live media")

    check(box.channels[S.CH_SCREEN_BOX] == S.RECVONLY, "answerer mirrored screen-box to recvonly")
    check(dev.channels[S.CH_SCREEN_BOX] == S.SENDONLY, "offerer kept screen-box sendonly")
    check(box.channels[S.CH_VOICE] == S.SENDRECV and dev.channels[S.CH_VOICE] == S.SENDRECV,
          "voice is two-way (sendrecv) on both ends")

def test_two_way_voice():
    print("[c] TWO-WAY VOICE: audio both directions on one call")
    box, dev, ib, idv = make_pair()
    negotiate(box, dev, ib, idv, [S.channel(S.CH_VOICE, S.SENDRECV)])
    check(dev.send(S.CH_VOICE, b"user-says-hello"), "device sends audio")
    check(box.recv(S.CH_VOICE) == b"user-says-hello", "box receives the user's audio")
    check(box.send(S.CH_VOICE, b"box-replies"), "box sends audio")
    check(dev.recv(S.CH_VOICE) == b"box-replies", "device receives the box's audio")

def test_two_way_video():
    print("[d] TWO-WAY VIDEO: video both directions")
    box, dev, ib, idv = make_pair()
    negotiate(box, dev, ib, idv, [S.channel(S.CH_VIDEO, S.SENDRECV)])
    check(dev.send(S.CH_VIDEO, b"user-cam-frame"), "device sends video")
    check(box.recv(S.CH_VIDEO) == b"user-cam-frame", "box receives user video")
    check(box.send(S.CH_VIDEO, b"box-cam-frame"), "box sends video")
    check(dev.recv(S.CH_VIDEO) == b"box-cam-frame", "device receives box video")

def test_screen_both_ways():
    print("[e] SCREEN-SHARE BOTH WAYS: box->user AND user->box")
    box, dev, ib, idv = make_pair()
    chans = [S.channel(S.CH_SCREEN_BOX, S.SENDONLY),
             S.channel(S.CH_SCREEN_USER, S.RECVONLY)]
    negotiate(box, dev, ib, idv, chans)

    check(dev.channels[S.CH_SCREEN_USER] == S.RECVONLY, "device offered screen-user recvonly")

    bbox, bdev, bib, bidv = make_pair()

    bbox_off = MS.MediaSession(role="offerer", principal="alice",
                               backend=B.LoopbackBackend("pending", "box"),
                               emit=lambda e: bidv.append(e))
    bdev_ans = MS.MediaSession(role="answerer", principal="alice",
                               backend=B.LoopbackBackend("pending", "dev"),
                               emit=lambda e: bib.append(e))
    bbox_off.create_offer([S.channel(S.CH_SCREEN_BOX, S.SENDONLY)])
    bdev_ans.on_offer(bidv.pop())
    bbox_off.on_answer(bib.pop())
    bbox_off.backend.link(bdev_ans.backend)
    bbox_off.mark_connected(); bdev_ans.mark_connected()
    check(bbox_off.send(S.CH_SCREEN_BOX, b"box-screen-frame"), "box sends its screen")
    check(bdev_ans.recv(S.CH_SCREEN_BOX) == b"box-screen-frame", "user receives box's screen (box->user)")

    ubox, udev, uib, uidv = make_pair()
    negotiate(ubox, udev, uib, uidv, [S.channel(S.CH_SCREEN_USER, S.SENDONLY)])
    check(udev.send(S.CH_SCREEN_USER, b"user-screen-frame"), "user sends their screen")
    check(ubox.recv(S.CH_SCREEN_USER) == b"user-screen-frame", "box receives the user's screen (user->box)")

def test_direction_gating():
    print("[f] direction gating enforced (sendonly can't recv; recvonly can't send)")
    box, dev, ib, idv = make_pair()
    negotiate(box, dev, ib, idv, [S.channel(S.CH_SCREEN_BOX, S.SENDONLY)])
    check(dev.send(S.CH_SCREEN_BOX, b"frame"), "device (sendonly) can send")
    check(box.recv(S.CH_SCREEN_BOX) == b"frame", "box (recvonly) can recv")
    check(box.send(S.CH_SCREEN_BOX, b"nope") is False, "box (recvonly) CANNOT send (gated)")
    check(dev.recv(S.CH_SCREEN_BOX) is None, "device (sendonly) CANNOT recv (gated)")

def test_blast_radius():
    print("[g] bounded blast radius: a disallowed channel is dropped from the answer")

    box, dev, ib, idv = make_pair(allowed_box={S.CH_VOICE})
    dev.create_offer([S.channel(S.CH_VOICE), S.channel(S.CH_VIDEO), S.channel(S.CH_SCREEN_BOX)])
    ans = box.on_offer(ib.pop())
    check(set(ans["accepted_channels"]) == {S.CH_VOICE},
          f"box accepted ONLY the allowed channel ({ans.get('accepted_channels')})")
    check(S.CH_VIDEO not in box.channels and S.CH_SCREEN_BOX not in box.channels,
          "disallowed channels never added to the box session")

def test_renegotiation():
    print("[h] mid-call renegotiation: media.update adds a channel that then carries frames")
    box, dev, ib, idv = make_pair()
    negotiate(box, dev, ib, idv, [S.channel(S.CH_VOICE, S.SENDRECV)])
    ib.clear(); idv.clear()

    dev.update_channels([S.channel(S.CH_VIDEO, S.SENDRECV)])
    upd = ib.pop()
    check(upd["t"] == S.MEDIA_UPDATE, "media.update emitted")
    box.on_update(upd)

    check(dev.send(S.CH_VIDEO, b"late-video"), "device sends on the freshly-added video channel")
    check(box.recv(S.CH_VIDEO) == b"late-video", "box receives on the renegotiated channel")

def test_ice_turn():
    print("[i] ICE/TURN config: off-by-default loopback; gated TURN => ephemeral HMAC cred")
    os.environ.pop("MEDIA_ENABLED", None)
    check(iceconfig.production_ice_servers("alice") == [], "media off => loopback-only (no ICE servers)")

    import tempfile
    secret = b"super-secret-coturn-shared-key"
    sf = tempfile.mktemp(); open(sf, "wb").write(secret)
    os.environ["MEDIA_ENABLED"] = "1"
    os.environ["MEDIA_TURN_URL"] = "turn:turn.example.com:3478?transport=udp"
    os.environ["MEDIA_TURN_SECRET_FILE"] = sf
    servers = iceconfig.production_ice_servers("alice")
    check(len(servers) == 1 and "credential" in servers[0], "gated TURN advertises a credential")
    user, cred = servers[0]["username"], servers[0]["credential"]
    check(":alice" in user and user.split(":")[0].isdigit(), "username is <expiry>:<principal> (ephemeral)")
    import hmac, hashlib, base64
    expect = base64.b64encode(hmac.new(secret, user.encode(), hashlib.sha1).digest()).decode()
    check(cred == expect, "credential is HMAC-SHA1(secret, username) — verifiable, time-limited")
    check(secret.decode() not in (user + cred), "the long-lived TURN secret never appears in the offer")
    os.unlink(sf)
    for k in ("MEDIA_ENABLED", "MEDIA_TURN_URL", "MEDIA_TURN_SECRET_FILE"):
        os.environ.pop(k, None)

def test_relay_carriage_zero_knowledge():
    print("[j] carriage over the relay E2E channel — relay sees only ciphertext (zero-knowledge)")
    try:
        from relaylib import crypto, transport, protocol as P
    except Exception as e:
        check(False, f"relaylib not importable (set RELAY_ROOT) — {e}")
        return

    relay = transport.MockRelay()

    bx, bxp = crypto.gen_x25519(); be, bep = crypto.gen_ed25519()
    dx, dxp = crypto.gen_x25519(); de, dep = crypto.gen_ed25519()
    hs_box = crypto.Handshake(initiator=False, static_x_priv=bx, static_x_pub=bxp,
                              id_ed_priv=be, id_ed_pub=bep)
    hs_dev = crypto.Handshake(initiator=True, static_x_priv=dx, static_x_pub=dxp,
                              id_ed_priv=de, id_ed_pub=dep)
    rz = crypto.rendezvous_topic(bxp)
    bch = transport.Channel.register(relay.url, rz)
    dch = transport.Channel.dial(relay.url, rz)

    dch.send_blob(hs_dev.write_msg1().hex())
    hs_box.read_msg1(bytes.fromhex(bch.recv_blob(timeout=5)))
    bch.send_blob(hs_box.write_msg2().hex())
    hs_dev.read_msg2(bytes.fromhex(dch.recv_blob(timeout=5)))
    dch.send_blob(hs_dev.write_msg3().hex())
    hs_box.read_msg3(bytes.fromhex(bch.recv_blob(timeout=5)))
    sess_box, sess_dev = hs_box.session(), hs_dev.session()

    dev_relay = C.RelayCarrier(
        send_encrypted=lambda pt: dch.send_blob(sess_dev.encrypt(pt).hex()),
        recv_encrypted=lambda timeout=None: (lambda b: sess_dev.decrypt(bytes.fromhex(b)) if b else None)(dch.recv_blob(timeout=timeout)))
    box_relay = C.RelayCarrier(
        send_encrypted=lambda pt: bch.send_blob(sess_box.encrypt(pt).hex()),
        recv_encrypted=lambda timeout=None: (lambda b: sess_box.decrypt(bytes.fromhex(b)) if b else None)(bch.recv_blob(timeout=timeout)))

    secret_sdp = "v=0 SECRET-SDP-DO-NOT-LEAK candidate 9.9.9.9"
    off = S.offer(S.new_session_id(), secret_sdp, [S.channel(S.CH_VOICE)],
                  [{"urls": ["turn:turn.example.com"], "credential": "EPHEMERAL"}], role="device")
    dev_relay.emit(off)
    got = box_relay.poll(timeout=5)
    check(got is not None and got["t"] == S.MEDIA_OFFER and got["sdp"] == secret_sdp,
          "box received the media.offer over the E2E relay session")

    blob_dump = repr(relay.seen_frames)
    check(secret_sdp not in blob_dump and "9.9.9.9" not in blob_dump,
          "the relay NEVER saw the SDP/ICE in plaintext (signaling is E2E ciphertext)")
    check("EPHEMERAL" not in blob_dump, "the relay never saw the TURN credential either")
    bch.close(); dch.close(); relay.stop()

def test_bus_projection():
    print("[k] bus projection over the EXISTING typed event-bus (no new verbs / schema)")
    events = []
    submitted = []
    def add_typed_event(job_id, kind, data):
        events.append((job_id, kind, data)); return [1]
    def submit_fn(req):
        submitted.append(req); return {"id": 4242, "ok": True}
    bus = C.BusCarrier("alice", add_typed_event, submit_fn=submit_fn)
    sid = S.new_session_id()
    bus.emit(S.offer(sid, "v=0", [S.channel(S.CH_VOICE)], []))
    bus.emit(S.answer(sid, "v=0", accepted_channels=[S.CH_VOICE]))
    check(submitted and submitted[0]["task_type"] == "media.session",
          "the call registered as a media.session job via the EXISTING submit broker")
    check(submitted[0].get("_method") == "device-channel" and submitted[0].get("_selector") == "alice",
          "submit used the on-behalf-of broker contract (server-side principal resolution)")
    check(all(kind == S.BUS_EVENT_KIND for (_, kind, _) in events),
          "every projected event uses the single `media` typed-event kind")
    check(events and events[0][2]["t"] == S.MEDIA_OFFER and events[0][0] == 4242,
          "the media.offer was mirrored as a typed event on the media.session job")

def test_streamd_upgrade_seam():
    print("[l] streamd upgrade seam: ScreenSource pumps composited frames into screen-box")

    box_ib, box_idv = [], []
    box = MS.MediaSession(role="offerer", principal="alice",
                          backend=B.LoopbackBackend("pending", "box"),
                          emit=lambda e: box_idv.append(e))
    dev = MS.MediaSession(role="answerer", principal="alice",
                          backend=B.LoopbackBackend("pending", "dev"),
                          emit=lambda e: box_ib.append(e))
    box.create_offer([S.channel(S.CH_SCREEN_BOX, S.SENDONLY)])
    dev.on_offer(box_idv.pop())
    box.on_answer(box_ib.pop())
    box.backend.link(dev.backend)
    box.mark_connected(); dev.mark_connected()

    class FakeTap:
        def __init__(self): self.gen = 0
        def snapshot(self):
            self.gen += 1
            return (b"\xff" * (4 * 4 * 4), 4, 4, self.gen)
    src = framesrc.ScreenSource(box, FakeTap(), max_fps=50, idle_poll=0.02).start()

    got = None
    for _ in range(50):
        got = dev.recv(S.CH_SCREEN_BOX)
        if got is not None:
            break
        time.sleep(0.02)
    src.stop()
    check(got is not None and isinstance(got, framesrc.VideoFrame),
          "device received a composited box-screen frame over the screen-box WebRTC track")
    check(src.frames_pushed >= 1, "ScreenSource pushed at least one generation-gated frame")

    class StillTap:
        def snapshot(self): return (b"\x00" * 64, 4, 4, 7)
    box2_idv = []
    box2 = MS.MediaSession(role="offerer", principal="alice",
                           backend=B.LoopbackBackend("pending", "box"),
                           emit=lambda e: box2_idv.append(e))
    dev2 = MS.MediaSession(role="answerer", principal="alice",
                           backend=B.LoopbackBackend("pending", "dev"), emit=lambda e: None)
    box2.create_offer([S.channel(S.CH_SCREEN_BOX, S.SENDONLY)])
    dev2.on_offer(box2_idv.pop())
    box2.backend.link(dev2.backend); box2.mark_connected(); dev2.mark_connected()
    s2 = framesrc.ScreenSource(box2, StillTap(), max_fps=50, idle_poll=0.01).start()
    time.sleep(0.2); s2.stop()
    check(s2.frames_pushed <= 1, "a STILL screen (constant generation) is idle-paused (no re-push)")

def main():
    print("=== mediad — bidirectional realtime media tier — test suite (loopback mock peer) ===")
    test_vocabulary()
    test_negotiation()
    test_two_way_voice()
    test_two_way_video()
    test_screen_both_ways()
    test_direction_gating()
    test_blast_radius()
    test_renegotiation()
    test_ice_turn()
    test_relay_carriage_zero_knowledge()
    test_bus_projection()
    test_streamd_upgrade_seam()
    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
