
from __future__ import annotations
import threading
import time
import urllib.request

from . import signaling as S

class FrameTapClient:

    def __init__(self, base_url="http://127.0.0.1:8089"):
        self.base = base_url.rstrip("/")

    def generation(self) -> int:
        try:
            with urllib.request.urlopen(self.base + "/state", timeout=2) as r:
                import json
                return int(json.loads(r.read().decode()).get("generation", 0))
        except Exception:
            return 0

    def snapshot(self):

        try:
            with urllib.request.urlopen(self.base + "/frame.rgba", timeout=3) as r:
                data = r.read()
                w = int(r.headers.get("X-Phantom-W", "0"))
                h = int(r.headers.get("X-Phantom-H", "0"))
                gen = int(r.headers.get("X-Phantom-Gen", "0"))
                if w and h and data:
                    return data, w, h, gen
        except Exception:
            pass
        return None

class VideoFrame:

    __slots__ = ("rgba", "width", "height", "generation", "ts")

    def __init__(self, rgba, width, height, generation):
        self.rgba = rgba
        self.width = width
        self.height = height
        self.generation = generation
        self.ts = time.time()

class ScreenSource:

    def __init__(self, session, tap: FrameTapClient, *, max_fps=12, idle_poll=0.5):
        self.session = session
        self.tap = tap
        self.max_fps = max_fps
        self.idle_poll = idle_poll
        self._run = False
        self._t = None
        self.frames_pushed = 0

    def start(self):
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def stop(self):
        self._run = False
        if self._t:
            self._t.join(timeout=1)

    def _loop(self):
        last_gen = -1
        min_dt = 1.0 / max(1, self.max_fps)
        while self._run:
            if S.CH_SCREEN_BOX not in self.session.channels or not self.session.is_live():
                time.sleep(self.idle_poll)
                continue
            snap = self.tap.snapshot()
            if snap is None:
                time.sleep(self.idle_poll)
                continue
            rgba, w, h, gen = snap
            if gen == last_gen:
                time.sleep(self.idle_poll)
                continue
            last_gen = gen
            self.session.send(S.CH_SCREEN_BOX, VideoFrame(rgba, w, h, gen))
            self.frames_pushed += 1
            time.sleep(min_dt)
