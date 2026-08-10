
from __future__ import annotations
import threading

from . import signaling as S
from . import backend as B
from . import iceconfig

ST_NEW = "new"
ST_OFFERING = "offering"
ST_ANSWERING = "answering"
ST_NEGOTIATED = "negotiated"
ST_CONNECTED = "connected"
ST_CLOSED = "closed"

class MediaSession:
    def __init__(self, *, role: str, principal: str, session_id: str | None = None,
                 backend: B.MediaBackend | None = None, emit=None, sdp_engine=None,
                 allowed_channels=None):

        if role not in ("offerer", "answerer"):
            raise ValueError("role must be offerer or answerer")
        self.role = role
        self.principal = principal
        self.session_id = session_id or (S.new_session_id() if role == "offerer" else None)
        self.backend = backend if backend is not None else B.select_backend()(
            self.session_id or "pending")
        self._emit = emit or (lambda env: None)
        self.sdp = sdp_engine or FakeSdpEngine()
        self.allowed = frozenset(allowed_channels) if allowed_channels is not None else None
        self.state = ST_NEW
        self.channels: dict[str, str] = {}
        self.remote_ready = False
        self.local_ready = False
        self._cand_seq = 0
        self._lock = threading.Lock()
        self._closed_reason = None

    def create_offer(self, requested_channels):

        if self.role != "offerer":
            raise RuntimeError("only the offerer creates an offer")
        chans = self._filter_channels(requested_channels)
        if not chans:
            raise PermissionError("no requested channel is permitted for this principal")
        for c in chans:
            self.channels[c["channel"]] = c["direction"]
            self.backend.add_channel(c["channel"], c["direction"])
        ice = iceconfig.production_ice_servers(self.principal)
        sdp = self.sdp.make_offer(self.session_id, chans)
        self.state = ST_OFFERING
        env = S.offer(self.session_id, sdp, chans, ice, role="box")
        self._emit(env)
        return env

    def on_answer(self, env):

        if self.role != "offerer" or self.state != ST_OFFERING:
            return self._reject("bad-state", "answer arrived but not offering")
        self.sdp.set_answer(self.session_id, env.get("sdp"))

        acc = env.get("accepted_channels")
        if acc is not None:
            for ch in list(self.channels):
                if ch not in acc:
                    self.channels.pop(ch, None)
                    self.backend.remove_channel(ch)
        self.state = ST_NEGOTIATED
        return None

    def on_offer(self, env):

        if self.role != "answerer":
            return self._reject("bad-role", "offer arrived at a non-answerer")
        reason = S.validate(env)
        if reason:
            return self._reject("bad-offer", reason)
        self.session_id = env["session_id"]
        self.backend.session_id = self.session_id
        offered = env.get("channels", [])
        accepted = []
        for c in offered:
            ch, dirn = c["channel"], c.get("direction", S.SENDRECV)
            if self.allowed is not None and ch not in self.allowed:
                continue
            local_dir = S.mirror_direction(dirn)
            self.channels[ch] = local_dir
            self.backend.add_channel(ch, local_dir)
            accepted.append(ch)
        if not accepted:
            return self._reject("no-channels", "no offered channel is permitted")
        self.state = ST_ANSWERING
        sdp = self.sdp.make_answer(self.session_id, self.sdp.parse_offer(env.get("sdp")))
        ans = S.answer(self.session_id, sdp, accepted_channels=accepted)
        self._emit(ans)
        self.state = ST_NEGOTIATED
        return ans

    def add_local_candidate(self, cand, *, mid=None, mline_index=None, end=False):

        with self._lock:
            seq = self._cand_seq
            self._cand_seq += 1
        env = S.candidate(self.session_id, cand, seq=seq, mid=mid,
                          mline_index=mline_index, end=end)
        self._emit(env)
        return env

    def on_candidate(self, env):

        if self.state in (ST_CLOSED,):
            return None
        self.sdp.add_remote_candidate(self.session_id, env)
        return None

    def mark_connected(self):

        if self.state in (ST_NEGOTIATED, ST_CONNECTED):
            self.backend.connected = True
            self.local_ready = True
            self.state = ST_CONNECTED
            self._emit(S.ready(self.session_id, who=self.role))
        return self.state

    def on_ready(self, env):
        self.remote_ready = True
        return self.is_live()

    def is_live(self) -> bool:
        return self.state == ST_CONNECTED and self.backend.connected

    def update_channels(self, channels):

        chans = self._filter_channels(channels)
        for c in chans:
            ch, dirn = c["channel"], c["direction"]
            if dirn == S.INACTIVE:
                self.channels.pop(ch, None)
                self.backend.remove_channel(ch)
            else:
                self.channels[ch] = dirn
                self.backend.add_channel(ch, dirn)
        self._emit(S.update(self.session_id, chans))
        return chans

    def on_update(self, env):

        for c in env.get("channels", []):
            ch, dirn = c["channel"], c.get("direction", S.SENDRECV)
            if self.allowed is not None and ch not in self.allowed:
                continue
            local_dir = S.mirror_direction(dirn) if self.role == "answerer" else dirn
            if local_dir == S.INACTIVE:
                self.channels.pop(ch, None)
                self.backend.remove_channel(ch)
            else:
                self.channels[ch] = local_dir
                self.backend.add_channel(ch, local_dir)
        return self.channels

    def close(self, reason=None):
        if self.state == ST_CLOSED:
            return
        self.state = ST_CLOSED
        self._closed_reason = reason
        try:
            self._emit(S.bye(self.session_id, reason=reason))
        finally:
            self.backend.close()

    def on_bye(self, env):
        ch = env.get("channel")
        if ch:
            self.channels.pop(ch, None)
            self.backend.remove_channel(ch)
            return self.channels
        self.state = ST_CLOSED
        self.backend.close()
        return None

    def send(self, channel, frame) -> bool:
        return self.backend.send_frame(channel, frame)

    def recv(self, channel, timeout=None):
        return self.backend.recv_frame(channel, timeout=timeout)

    def _filter_channels(self, requested):
        out = []
        for c in requested:
            ch = c.get("channel")
            if ch not in S.ALL_CHANNELS:
                continue
            if self.allowed is not None and ch not in self.allowed:
                continue
            out.append(S.channel(ch, c.get("direction", S.SENDRECV)))
        return out

    def _reject(self, code, message):
        env = S.error(self.session_id or "?", code, message)
        self._emit(env)
        return env

class FakeSdpEngine:
    def make_offer(self, session_id, channels) -> str:
        lines = [f"v=0", f"o=- {session_id} 1 IN IP4 127.0.0.1", "s=brainarbeit-media"]
        for c in channels:
            kind = B.CHANNEL_KIND[c["channel"]]
            lines.append(f"m={kind} 9 UDP/TLS/RTP/SAVPF 0")
            lines.append(f"a=mid:{c['channel']}")
            lines.append(f"a={c['direction']}")
            lines.append("a=setup:actpass")
            lines.append("a=fingerprint:sha-256 FAKE")
        return "\r\n".join(lines) + "\r\n"

    def parse_offer(self, sdp: str) -> dict:
        chans = {}
        cur = None
        for line in (sdp or "").splitlines():
            if line.startswith("a=mid:"):
                cur = line[len("a=mid:"):]
            elif cur and line.startswith("a=") and line[2:] in S.DIRECTIONS:
                chans[cur] = line[2:]
        return chans

    def make_answer(self, session_id, parsed_offer: dict) -> str:
        lines = [f"v=0", f"o=- {session_id} 1 IN IP4 127.0.0.1", "s=brainarbeit-media"]
        for mid, dirn in parsed_offer.items():
            kind = B.CHANNEL_KIND.get(mid, "application")
            lines.append(f"m={kind} 9 UDP/TLS/RTP/SAVPF 0")
            lines.append(f"a=mid:{mid}")
            lines.append(f"a={S.mirror_direction(dirn)}")
            lines.append("a=setup:active")
            lines.append("a=fingerprint:sha-256 FAKE")
        return "\r\n".join(lines) + "\r\n"

    def set_answer(self, session_id, sdp):
        return True

    def add_remote_candidate(self, session_id, env):
        return True
