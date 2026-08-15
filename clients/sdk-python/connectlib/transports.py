
from __future__ import annotations
import os, json, socket, threading

class LanTransport:

    kind = "lan"

    def __init__(self, sock_path: str):
        self.sock_path = sock_path

    def _connect(self, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock_path)
        return s

    def call(self, req: dict, timeout=15) -> dict:

        req = {k: v for k, v in req.items() if k not in ("principal", "uid", "_peer_uid", "token")}
        s = self._connect(timeout)
        try:
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n", 1)[0]
            return json.loads(line.decode()) if line else {"ok": False, "error": "empty"}
        finally:
            s.close()

    submit = call

    def subscribe(self, req: dict, on_frame, stop: threading.Event):

        s = self._connect(timeout=None)
        s.settimeout(1.0)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        try:
            while not stop.is_set():
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            on_frame(json.loads(line.decode()))
                        except ValueError:
                            pass
        finally:
            try:
                s.close()
            except OSError:
                pass

class RelayTransport:

    kind = "relay"

    def __init__(self, *, relay_url, appliance_id_pub_hex, appliance_x_pub_hex,
                 device_keys: dict, totp_provider=None):
        from relaylib.device import Device
        self.relay_url = relay_url
        self.totp_provider = totp_provider
        self._dev = Device(
            box_id_pub=bytes.fromhex(appliance_id_pub_hex),
            box_x_pub=bytes.fromhex(appliance_x_pub_hex),
            id_priv=bytes.fromhex(device_keys["id_priv"]),
            id_pub=bytes.fromhex(device_keys["id_pub"]),
            sx_priv=bytes.fromhex(device_keys["sx_priv"]),
            sx_pub=bytes.fromhex(device_keys["sx_pub"]),
        )
        self._lock = threading.Lock()
        self._connected = False

    @property
    def did(self) -> str:
        return self._dev.did

    def connect(self):

        if self._connected:
            return
        self._dev.connect(self.relay_url)
        self._connected = True

    def close(self):
        try:
            self._dev.close()
        finally:
            self._connected = False

    def pair(self, code: str, *, label=None, totp_code=None) -> dict:

        self.connect()
        tc = totp_code if totp_code is not None else (self.totp_provider() if self.totp_provider else None)
        with self._lock:
            return self._dev.pair(code, label=label, totp_code=tc)

    def hello(self, token: str) -> dict:
        self.connect()
        self._dev.token = token
        with self._lock:
            return self._dev.hello()

    @staticmethod
    def _normalize(resp: dict) -> dict:

        if not isinstance(resp, dict):
            return {"ok": False, "error": "no response"}
        t = resp.get("t")
        if t == "result":
            return {"ok": True, "id": resp.get("id"), "pos": resp.get("pos"),
                    "principal": resp.get("principal"), "via": resp.get("via"),
                    "control_result": resp.get("control_result")}
        if t == "error":
            return {"ok": False, "error": resp.get("error"), **{
                k: resp[k] for k in ("need_2fa", "need_step_up_2fa") if k in resp}}
        return resp

    def submit(self, req: dict) -> dict:

        self.connect()
        task_type = req.get("task_type")
        params = req.get("params")
        cmd = req.get("cmd")
        cls = req.get("class", "worker")

        step_up_auto = bool(self.totp_provider) and req.get("step_up", False)
        with self._lock:
            raw = self._dev.submit(task_type=task_type, params=params, cmd=cmd, cls=cls,
                                   step_up_auto=step_up_auto)
        return self._normalize(raw)

    def call(self, req: dict) -> dict:

        self.connect()
        verb = req.get("verb")

        sensitive = verb in ("approve", "deny", "reject") or bool(req.get("step_up"))
        step_up_auto = sensitive and (
            bool(self.totp_provider) or bool(getattr(self._dev, "totp_secret", None)))
        inner = {k: v for k, v in req.items() if k != "step_up"}
        with self._lock:
            raw = self._dev.submit(task_type="relay.control", params={"control": inner},
                                   step_up_auto=step_up_auto)
        resp = self._normalize(raw)

        cr = resp.get("control_result")
        return cr if cr is not None else resp

    def subscribe(self, req: dict, on_frame, stop: threading.Event):

        after = req.get("after_id", 0)
        principal = None
        for tp in req.get("topics", []):
            if tp.startswith("user/"):
                principal = tp.split("/", 1)[1]
        while not stop.is_set():
            rep = self.call({"verb": "replay",
                             "topics": req.get("topics", []), "after_id": after})
            for ev in (rep.get("events") or []):
                on_frame(ev)
                eid = ev.get("event_id") or ev.get("seq")
                if isinstance(eid, int) and eid > after:
                    after = eid
            stop.wait(0.5)
