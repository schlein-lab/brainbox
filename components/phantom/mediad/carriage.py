
from __future__ import annotations
import json

from . import signaling as S

class RelayCarrier:

    def __init__(self, send_encrypted, recv_encrypted=None):
        self._send = send_encrypted
        self._recv = recv_encrypted

    def emit(self, env: dict):

        self._send(S.dumps(env))

    def poll(self, timeout=None):

        if self._recv is None:
            return None
        raw = self._recv(timeout=timeout)
        if raw is None:
            return None
        try:
            env = S.loads(raw)
        except Exception:
            return None
        if env.get("t") in S.ALL_TYPES:
            return env
        return None

class BusCarrier:

    def __init__(self, principal, add_typed_event, submit_fn=None, job_id=None):
        self.principal = principal
        self._add_event = add_typed_event
        self._submit = submit_fn
        self.job_id = job_id

    def ensure_job(self, session_id):

        if self.job_id is not None or self._submit is None:
            return self.job_id
        req = {
            "verb": "submit",
            "task_type": "media.session",
            "params": {"session_id": session_id, "kind": "realtime-media"},
            "class": "interactive",

            "_method": "device-channel",
            "_selector": self.principal,
        }
        resp = self._submit(req) or {}
        self.job_id = resp.get("id")
        return self.job_id

    def emit(self, env: dict):

        self.ensure_job(env.get("session_id"))
        if self.job_id is None:
            return
        self._add_event(self.job_id, S.BUS_EVENT_KIND, env)

class TeeCarrier:

    def __init__(self, *carriers, inbound=None):
        self._carriers = carriers
        self._inbound = inbound or (carriers[0] if carriers else None)

    def emit(self, env: dict):
        for c in self._carriers:
            try:
                c.emit(env)
            except Exception:
                pass

    def poll(self, timeout=None):
        return self._inbound.poll(timeout=timeout) if self._inbound else None
