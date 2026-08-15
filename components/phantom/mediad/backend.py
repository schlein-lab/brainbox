
from __future__ import annotations
import os, queue, threading
from dataclasses import dataclass, field

from . import signaling as S

@dataclass
class Track:

    channel: str
    kind: str
    direction: str

    def can_send(self) -> bool:
        return self.direction in (S.SENDRECV, S.SENDONLY)

    def can_recv(self) -> bool:
        return self.direction in (S.SENDRECV, S.RECVONLY)

CHANNEL_KIND = {
    S.CH_VOICE: "audio",
    S.CH_VIDEO: "video",
    S.CH_SCREEN_BOX: "video",
    S.CH_SCREEN_USER: "video",
}

class MediaBackend:

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.tracks: dict[str, Track] = {}
        self.connected = False

    def add_channel(self, channel: str, direction: str):
        self.tracks[channel] = Track(channel, CHANNEL_KIND[channel], direction)

    def remove_channel(self, channel: str):
        self.tracks.pop(channel, None)

    def send_frame(self, channel: str, frame) -> bool:
        raise NotImplementedError

    def recv_frame(self, channel: str, timeout=None):
        raise NotImplementedError

    def close(self):
        self.connected = False

    def kind() -> str:
        return "abstract"

class LoopbackBackend(MediaBackend):

    def __init__(self, session_id: str, label: str = "peer"):
        super().__init__(session_id)
        self.label = label
        self._peer: LoopbackBackend | None = None

        self._inbox: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()

    def link(self, other: "LoopbackBackend"):

        self._peer = other
        other._peer = self
        self.connected = True
        other.connected = True

    def add_channel(self, channel: str, direction: str):
        super().add_channel(channel, direction)
        with self._lock:
            self._inbox.setdefault(channel, queue.Queue(maxsize=256))

    def send_frame(self, channel: str, frame) -> bool:

        t = self.tracks.get(channel)
        if t is None or not t.can_send() or self._peer is None or not self.connected:
            return False
        pt = self._peer.tracks.get(channel)
        if pt is None or not pt.can_recv():
            return False
        try:
            self._peer._inbox[channel].put_nowait(frame)
            return True
        except (KeyError, queue.Full):
            return False

    def recv_frame(self, channel: str, timeout=None):

        t = self.tracks.get(channel)
        if t is None or not t.can_recv():
            return None
        q = self._inbox.get(channel)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout) if timeout is not None else q.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        super().close()
        if self._peer is not None:
            self._peer.connected = False

    @staticmethod
    def kind() -> str:
        return "loopback"

class AiortcBackend(MediaBackend):

    def __init__(self, session_id: str):
        super().__init__(session_id)
        raise NotImplementedError(
            "AiortcBackend requires the `aiortc` dependency (DTLS-SRTP + ICE). It is "
            "intentionally NOT installed in this build (PyAV/ffmpeg is heavy; the box is "
            "RAM-constrained). A live deploy: `pip install aiortc` then select_backend('aiortc').")

    @staticmethod
    def kind() -> str:
        return "aiortc"

def select_backend(name: str | None = None):

    name = (name or os.environ.get("MEDIA_BACKEND", "loopback")).strip().lower()
    if name == "aiortc":
        return AiortcBackend
    return LoopbackBackend
