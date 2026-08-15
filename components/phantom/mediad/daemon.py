
from __future__ import annotations
import threading

from . import signaling as S
from . import session as MS
from . import backend as B
from . import carriage as C
from . import framesrc

def device_call_channels():

    return [
        S.channel(S.CH_VOICE, S.SENDRECV),
        S.channel(S.CH_VIDEO, S.SENDRECV),
        S.channel(S.CH_SCREEN_BOX, S.SENDONLY),
    ]

def box_push_screen_channels():

    return [S.channel(S.CH_SCREEN_BOX, S.SENDONLY)]

def user_share_screen_channels():

    return [S.channel(S.CH_SCREEN_USER, S.SENDONLY)]

class MediaEndpoint:

    def __init__(self, *, principal, relay_carrier, bus_carrier=None,
                 allowed_channels=None, frame_tap=None, sdp_engine=None,
                 backend_factory=None):
        self.principal = principal
        self.relay = relay_carrier
        self.bus = bus_carrier
        self.allowed = allowed_channels
        self.frame_tap = frame_tap
        self.sdp_engine = sdp_engine
        self.backend_factory = backend_factory or B.select_backend()
        self.sessions: dict[str, MS.MediaSession] = {}
        self._screen_sources = {}
        self._lock = threading.Lock()

    def _carrier(self):
        if self.bus is not None:
            return C.TeeCarrier(self.relay, self.bus, inbound=self.relay)
        return self.relay

    def _new_session(self, role, session_id=None):
        carrier = self._carrier()
        sid = session_id or (S.new_session_id() if role == "offerer" else None)
        be = self.backend_factory(sid or "pending")
        sess = MS.MediaSession(role=role, principal=self.principal, session_id=sid,
                               backend=be, emit=carrier.emit, sdp_engine=self.sdp_engine,
                               allowed_channels=self.allowed)
        return sess

    def initiate(self, channels=None):
        sess = self._new_session("offerer")
        sess.create_offer(channels or box_push_screen_channels())
        with self._lock:
            self.sessions[sess.session_id] = sess
        self._maybe_attach_screen(sess)
        return sess

    def handle_signal(self, env: dict):

        reason = S.validate(env)
        if reason:
            return S.error(env.get("session_id", "?"), "bad-signal", reason)
        sid = env["session_id"]
        t = env["t"]
        with self._lock:
            sess = self.sessions.get(sid)
        if t == S.MEDIA_OFFER:
            if sess is None:
                sess = self._new_session("answerer", session_id=sid)
                with self._lock:
                    self.sessions[sid] = sess
            res = sess.on_offer(env)
            self._maybe_attach_screen(sess)
            return res
        if sess is None:
            return S.error(sid, "unknown-session", "no media session for that id")
        if t == S.MEDIA_ANSWER:
            return sess.on_answer(env)
        if t == S.MEDIA_CANDIDATE:
            return sess.on_candidate(env)
        if t == S.MEDIA_READY:
            sess.on_ready(env)
            return None
        if t == S.MEDIA_UPDATE:
            sess.on_update(env)
            self._maybe_attach_screen(sess)
            return None
        if t == S.MEDIA_BYE:
            res = sess.on_bye(env)
            if sess.state == MS.ST_CLOSED:
                self._teardown(sid)
            return res
        return None

    def _maybe_attach_screen(self, sess):

        if (self.frame_tap is not None and S.CH_SCREEN_BOX in sess.channels
                and sess.channels[S.CH_SCREEN_BOX] in (S.SENDONLY, S.SENDRECV)
                and sess.session_id not in self._screen_sources):
            src = framesrc.ScreenSource(sess, self.frame_tap).start()
            self._screen_sources[sess.session_id] = src

    def _teardown(self, sid):
        with self._lock:
            self.sessions.pop(sid, None)
            src = self._screen_sources.pop(sid, None)
        if src:
            src.stop()

    def run_once(self, timeout=1.0):

        env = self.relay.poll(timeout=timeout)
        if env is None:
            return None
        self.handle_signal(env)
        return env

    def close_all(self, reason="endpoint-closing"):
        for sid in list(self.sessions):
            try:
                self.sessions[sid].close(reason=reason)
            finally:
                self._teardown(sid)
