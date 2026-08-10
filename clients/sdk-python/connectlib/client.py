
from __future__ import annotations
import hashlib, os, threading

from connectlib import contract, user_topic
from connectlib.keystore import Keystore
from connectlib.voice import VoiceIntake, utterance_to_verb

class ConnectClient:
    def __init__(self, box_label: str, *, keystore: Keystore | None = None,
                 transport=None, principal: str | None = None, voice: VoiceIntake | None = None):
        self.box_label = box_label
        self.ks = keystore or Keystore()
        self.transport = transport
        self.principal = principal
        self.voice = voice or VoiceIntake()
        self.reality = contract.Reality()
        self._watch_thread = None
        self._stop = threading.Event()

    def pair(self, code: str, *, totp_code: str, label: str | None = None) -> dict:

        if self.transport is None or self.transport.kind != "relay":
            raise RuntimeError("pairing requires the off-LAN relay transport")
        resp = self.transport.pair(code, label=label, totp_code=totp_code)
        if resp.get("t") == "pair_ok":
            self.principal = resp.get("principal", self.principal)
            self.ks.save_alliance(self.box_label, principal=self.principal,
                                  token=resp["token"], did=self.transport.did,
                                  caps=resp.get("caps"))
        return resp

    def is_paired(self) -> bool:
        return self.ks.is_paired(self.box_label)

    def connect(self) -> dict:

        if self.transport is None:
            raise RuntimeError("no transport configured")
        if self.transport.kind == "lan":
            r = self.transport.call({"verb": "ping"})
            lan = self.ks.lan(self.box_label)
            if lan and not self.principal:
                self.principal = lan.get("principal")
            return {"ok": r.get("ok", True), "mode": "lan", "principal": self.principal}

        al = self.ks.alliance(self.box_label)
        if not al or not al.get("token"):
            return {"ok": False, "error": "not paired; pair this device once first"}
        resp = self.transport.hello(al["token"])
        if resp.get("t") == "hello_ok":
            self.principal = resp.get("principal", al.get("principal"))
            return {"ok": True, "mode": "relay", "principal": self.principal,
                    "caps": resp.get("caps")}
        return {"ok": False, "mode": "relay", "error": resp.get("error", "hello failed")}

    def disconnect(self):
        self._stop.set()
        if self._watch_thread:
            self._watch_thread.join(timeout=2)
        if hasattr(self.transport, "close"):
            try:
                self.transport.close()
            except Exception:
                pass

    def watch(self, on_cvm=None, *, background=True):

        if not self.principal:
            raise RuntimeError("no principal resolved; connect() first")
        req = contract.subscribe(self.principal, after_id=self.reality.last_event_id or None)

        def on_frame(frame):
            cvm = self.reality.apply(frame)
            if cvm is not None and on_cvm:
                on_cvm(cvm)

        self._stop.clear()
        if background:
            self._watch_thread = threading.Thread(
                target=self.transport.subscribe, args=(req, on_frame, self._stop), daemon=True)
            self._watch_thread.start()
            return self._watch_thread
        return self.transport.subscribe(req, on_frame, self._stop)

    def type(self, text: str) -> dict:

        return self.transport.submit(
            contract.submit_text(text, reply_topic=user_topic(self.principal)))

    def say(self, audio_or_text) -> dict:

        text = self.voice.to_text(audio_or_text)
        return self.transport.submit(
            contract.submit_text(text, reply_topic=user_topic(self.principal)))

    def attach(self, text: str, *, files=None, paths=None) -> dict:

        manifest = [self._file_manifest(p) for p in (files or [])]
        return self.transport.submit(contract.submit_attach(
            text, attachments=manifest, paths=list(paths or []),
            reply_topic=user_topic(self.principal)))

    @staticmethod
    def _file_manifest(path: str) -> dict:
        st = os.stat(path)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return {"name": os.path.basename(path), "size": st.st_size,
                "sha256": h.hexdigest(), "path": os.path.abspath(path)}

    def pending_approvals(self) -> list[dict]:
        return self.reality.pending_approvals()

    def approve(self, cvm_or_nonce) -> dict:
        nonce = self._nonce(cvm_or_nonce)
        return self.transport.call(contract.approve(nonce))

    def reject(self, cvm_or_nonce) -> dict:
        nonce = self._nonce(cvm_or_nonce)
        return self.transport.call(contract.reject(nonce))

    def revise(self, cvm: dict, feedback: str) -> dict:
        return self.transport.call(contract.revise(cvm["id"], feedback))

    def decide_spoken(self, cvm: dict, utterance: str) -> dict:

        d = utterance_to_verb(utterance)
        if not d:
            return {"ok": False, "error": f"could not parse decision from: {utterance!r}"}
        if d["verb"] == "approve":
            return self.approve(cvm)
        if d["verb"] == "deny":
            return self.reject(cvm)
        return self.revise(cvm, d.get("feedback", ""))

    @staticmethod
    def _nonce(cvm_or_nonce) -> str:
        if isinstance(cvm_or_nonce, str):
            return cvm_or_nonce
        n = contract.nonce_of(cvm_or_nonce)
        if not n:
            raise ValueError("no approval nonce on that CVM (nothing to approve)")
        return n
